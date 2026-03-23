"""
Context builder — assembles compressed prompt from signals, positions, history, outcomes, memory.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from db import DecisionDB

log = logging.getLogger("ai-trader.context")

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json


# ── Prompt injection sanitizer ──────────────────────────────────────

_INJECTION_PATTERNS = [
    r'\bignore\s+(all\s+)?previous\b',
    r'\bignore\s+(all\s+)?above\b',
    r'\bignore\s+(all\s+)?instructions\b',
    r'\bdisregard\s+(all\s+)?previous\b',
    r'\bdisregard\s+(all\s+)?above\b',
    r'\bdisregard\s+(all\s+)?instructions\b',
    r'\boverride\s+(your\s+)?instructions\b',
    r'\boverride\s+(the\s+)?system\b',
    r'\bforget\s+(all\s+)?previous\b',
    r'\bforget\s+(all\s+)?instructions\b',
    r'\bact\s+as\s+(a\s+)?different\b',
    r'\bnew\s+instructions\b',
    r'\bsystem\s*prompt\b',
    r'\bsystem\s*message\b',
    r'\bjailbreak\b',
    r'\bdan\s*mode\b',
    r'\bunfiltered\s*(mode)?\b',
    r'\bimportant\s*:?\s*instruction\b',
    r'\breplace\s+(the\s+)?system\b',
    r'\bdisable\s+(safety|filter)\b',
    r'\boverride\s+safety\b',
]


def strip_injection_patterns(text: str) -> str:
    """Remove prompt-injection attempts from text using regex with word boundaries.

    When an injection pattern is found, truncates the text at the match point
    (everything after the injection is removed since it may contain malicious instructions).
    Iterates to handle multiple injection attempts. Returns [blocked] if entire text is consumed.
    """
    if not text:
        return ""
    for _ in range(10):  # max 10 passes to prevent infinite loops
        first_match = None
        for pattern in _INJECTION_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m and (first_match is None or m.start() < first_match.start()):
                first_match = m
        if first_match is None:
            break
        text = text[:first_match.start()].strip()
    if not text:
        return "[blocked]"
    return text


def sanitize_reasoning(text: str | None) -> str:
    """Truncate and strip prompt-injection attempts from LLM reasoning."""
    if not text:
        return ""
    s = text[:200]
    return strip_injection_patterns(s)


class ContextBuilder:
    def __init__(self, config: dict, db: DecisionDB, config_dir: str | None = None):
        self.config = config
        self.db = db
        # Resolve relative paths relative to config file directory
        if config_dir:
            self.signals_file = Path(config_dir) / config["signals_file"]
            self.memory_file = Path(config_dir) / config.get("strategy_memory_file", "state/strategy_memory.md")
            self.result_file = Path(config_dir) / config.get("result_file", "../ai-result.json")
        else:
            self.signals_file = Path(config["signals_file"])
            self.memory_file = Path(config.get("strategy_memory_file", "state/strategy_memory.md"))
            self.result_file = Path(config.get("result_file", "../ai-result.json"))

    def read_signals(self) -> tuple[list[dict], dict]:
        """Read signals.json. Returns (top_opportunities_list, config_dict).

        Filters to top N signals by compositeScore to keep prompt size manageable.
        """
        if not self.signals_file.exists():
            log.warning(f"Signals file not found: {self.signals_file}")
            return [], {}

        try:
            data = safe_read_json(self.signals_file)
            if data is None:
                log.error(f"Failed to read signals: {self.signals_file}")
                return [], {}

            opportunities = data.get("opportunities", [])
            if not opportunities:
                return [], data.get("config", {})

            max_signals = self.config.get("max_signals", 15)

            # Filter to top N by compositeScore descending
            top_opportunities = sorted(
                opportunities,
                key=lambda x: x.get("compositeScore", 0),
                reverse=True,
            )[:max_signals]

            # Remove symbols that were recently traded (last 2 hours)
            top_opportunities = self._filter_traded_symbols(top_opportunities)

            log.info(
                f"Filtered {len(opportunities)} signals to top {len(top_opportunities)} "
                f"(max_signals={max_signals})"
            )
            return top_opportunities, data.get("config", {})

        except (json.JSONDecodeError, OSError) as e:
            log.error(f"Failed to read signals: {e}")
            return [], {}

    def _filter_traded_symbols(self, opportunities: list[dict]) -> list[dict]:
        """Remove opportunities for symbols that were recently traded."""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute('''
                SELECT DISTINCT symbol
                FROM outcomes
                WHERE timestamp > datetime('now', '-2 hours')
            ''')
            traded_symbols = {row[0] for row in cursor.fetchall()}

            if not traded_symbols:
                return opportunities

            filtered = [o for o in opportunities if o.get('symbol') not in traded_symbols]
            removed = len(opportunities) - len(filtered)
            if removed > 0:
                log.info(f"Filtered out {removed} traded symbols: {traded_symbols}")

            return filtered
        except Exception as e:
            log.warning(f"Failed to filter traded symbols: {e}")
            return opportunities

    def read_positions(self) -> list[dict]:
        """Read current positions from the shared decision result file."""
        if self.result_file.exists():
            data = safe_read_json(self.result_file)
            if data:
                return data.get("positions", [])
        return []

    def read_strategy_memory(self) -> str:
        """Read learned patterns from strategy memory file (truncated to save tokens)."""
        if not self.memory_file.exists():
            return ""
        try:
            content = self.memory_file.read_text().strip()
            # Truncate to last 10000 chars to keep prompt size manageable
            if len(content) > 10000:
                content = "..." + content[-10000:]
            return content
        except OSError:
            return ""

    def read_recent_decisions(self, limit: int = 20) -> list[dict]:
        return self.db.get_recent_decisions(limit)

    def read_recent_outcomes(self, limit: int = 10) -> list[dict]:
        return self.db.get_recent_outcomes(limit)

    def build_prompt(
        self,
        signals: list[dict],
        positions: list[dict],
        history: list[dict],
        outcomes: list[dict],
        memory: str,
        signals_config: dict,
    ) -> str:
        """Assemble the LLM user prompt from compressed context."""
        sections = []

        # 1. Current positions (compressed)
        if positions:
            pos_summary = []
            for p in positions:
                roe = self._calc_roe(p)
                pos_summary.append(
                    f"- {p.get('symbol', '?')} {p.get('side', '?').upper()} "
                    f"${p.get('position_size_usd', p.get('size_usd', 0)):.0f} "
                    f"@ {p.get('entry_price', 0):.4f} "
                    f"(ROE: {roe:+.1f}%)"
                )
            sections.append("## Open Positions\n" + "\n".join(pos_summary))
        else:
            sections.append("## Open Positions\nNone open")

        # 2. Market opportunities (top 10 by score) with funding direction
        top_signals = sorted(signals, key=lambda s: s.get("compositeScore", 0), reverse=True)[:10]
        if top_signals:
            sig_lines = []
            for s in top_signals:
                safe_emoji = "✅" if s.get("safetyPass") else "❌"
                direction = s.get("direction", "?")
                score = s.get("compositeScore", 0)
                funding = s.get("fundingSpread8h", 0)
                vol = s.get("dailyVolumeUsd", 0)
                mom = s.get("dailyPriceChange", 0)
                # Funding direction: positive = longs pay shorts (shorts collect)
                if funding > 0.01:
                    funding_note = f"shorts collect"
                elif funding < -0.01:
                    funding_note = f"longs collect"
                else:
                    funding_note = f"neutral"
                sig_lines.append(
                    f"- {s['symbol']}: score={score} dir={direction} "
                    f"funding={funding:+.3f}% ({funding_note}) vol=${vol/1000:.0f}K "
                    f"mom={mom:+.1f}% {safe_emoji}"
                )
            sections.append("## Market Opportunities (Top 10)\n" + "\n".join(sig_lines))
        else:
            sections.append("## Market Opportunities\nNone available")

        # 3. Recent outcomes (last 5 closed trades) with running win rate
        if outcomes:
            out_lines = []
            for o in outcomes[:5]:
                emoji = "🟢" if o.get("pnl_usd", 0) > 0 else "🔴"
                roe = o.get("roe_pct", o.get("pnl_pct", 0))
                out_lines.append(
                    f"{emoji} {o['symbol']} {o.get('direction', '?')} → "
                    f"${o.get('pnl_usd', 0):+.2f} (ROE {roe:+.1f}%) "
                    f"held {o.get('hold_time_seconds', 0) // 60}min"
                )
            sections.append("## Recent Trade Outcomes\n" + "\n".join(out_lines))

        # 4. Strategy memory (learned patterns — from our own file, no injection risk)
        if memory:
            sections.append(f"## Learned Patterns\n{memory[:10000]}")

        # 5. Account state with win rate and timing
        equity = signals_config.get("accountEquity", 1000)

        # Win rate from DB performance stats
        stats = self.db.get_performance_stats()
        win_rate = stats.get("win_rate", 0)
        total_trades = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        avg_win = stats.get("avg_win", 0)
        avg_loss = stats.get("avg_loss", 0)

        # Time since last decision
        if history:
            last_ts = history[0].get("timestamp", "")
            try:
                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                mins_ago = int((datetime.now(timezone.utc) - last_dt).total_seconds() / 60)
                last_dec_str = f"- Last decision: {mins_ago} min ago"
            except (ValueError, TypeError):
                last_dec_str = "- Last decision: N/A"
        else:
            last_dec_str = "- Last decision: never"

        # Session context
        hour_utc = datetime.now(timezone.utc).hour
        if 0 <= hour_utc < 8:
            session = "Asia"
        elif 8 <= hour_utc < 16:
            session = "EU/US overlap"
        else:
            session = "US"

        sections.append(
            f"## Account\n"
            f"- Equity: ${equity:.2f}\n"
            f"- Open positions: {len(positions)}/3 max\n"
            f"- Daily realized PnL: ${self.db.get_daily_pnl():+.2f}\n"
            f"- Win rate: {win_rate:.0f}% ({wins}/{total_trades})\n"
            f"- Avg win: ${avg_win:+.2f} | Avg loss: ${avg_loss:+.2f}\n"
            f"{last_dec_str}\n"
            f"- Session: {session} ({datetime.now(timezone.utc).strftime('%H:%M')} UTC)"
        )

        # 6. Recent decisions (last 5, for context)
        if history:
            dec_lines = []
            for h in history[:5]:
                status = "✅" if h["executed"] else ("⚠️ rejected" if h["action"] != "hold" else "⏸️ hold")
                sanitized_reason = sanitize_reasoning(h.get("reasoning", ""))
                dec_lines.append(
                    f"- [{h['timestamp'][:16]}] {h['action']} {h.get('symbol', '')} "
                    f"conf={h.get('confidence', 0):.1f} {status}"
                    + (f" reason: {sanitized_reason}" if sanitized_reason else "")
                )
            sections.append("## Recent Decisions\n" + "\n".join(dec_lines))

        return "\n\n".join(sections)

    @staticmethod
    def _calc_roe(position: dict) -> float:
        """Calculate ROE% for a position."""
        entry = position.get("entry_price", 0)
        current = position.get("current_price", entry)
        side = position.get("side", "long")
        if entry == 0:
            return 0.0
        raw = (current - entry) / entry * 100
        if side == "short":
            raw = -raw
        return raw
