"""
Context builder — assembles compressed prompt from signals, positions, history, outcomes, memory.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from db import DecisionDB

log = logging.getLogger("ai-trader.context")

# ── Safe JSON reader ────────────────────────────────────────────────

def safe_read_json(path: Path, retries: int = 2, delay: float = 0.1) -> dict | None:
    """Read JSON with retry — handles race conditions from concurrent atomic_write."""
    for attempt in range(retries + 1):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            if attempt < retries:
                time.sleep(delay)
            else:
                return None
    return None

# ── Prompt injection sanitizer ──────────────────────────────────────

_INJECTION_PATTERNS = [
    "IMPORTANT:", "OVERRIDE:", "IGNORE PREVIOUS", "IGNORE ALL",
    "SYSTEM:", "ADMIN:", "ROOT:", "NEW INSTRUCTION", "DISREGARD",
    "FORGET", "OVERRIDE SAFETY", "DISABLE", "REPLACE SYSTEM",
    "JAILBREAK", "DAN:", "UNFILTERED",
]

_INJECTION_PATTERNS_LOWER = [p.lower() for p in _INJECTION_PATTERNS]


def strip_injection_patterns(text: str) -> str:
    """Remove prompt-injection attempts from text (no truncation)."""
    if not text:
        return ""
    lower = text.lower()
    for pat in _INJECTION_PATTERNS_LOWER:
        idx = lower.find(pat)
        if idx != -1:
            text = text[:idx].strip()
            if text:
                text += " [sanitized]"
            else:
                text = "[blocked]"
            break
    return text.strip()


def sanitize_reasoning(text: str | None) -> str:
    """Truncate and strip prompt-injection attempts from LLM reasoning."""
    if not text:
        return ""
    s = text[:200]
    return strip_injection_patterns(s)


class ContextBuilder:
    def __init__(self, config: dict, db: DecisionDB):
        self.config = config
        self.db = db
        self.signals_file = Path(config["signals_file"])
        self.memory_file = Path(config.get("strategy_memory_file", "state/strategy_memory.md"))

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
        result_file = self.config.get("result_file", "../ai-result.json")
        path = Path(result_file)
        if path.exists():
            data = safe_read_json(path)
            if data:
                return data.get("positions", [])
        return []

    def read_strategy_memory(self) -> str:
        """Read learned patterns from strategy memory file (truncated to save tokens)."""
        if not self.memory_file.exists():
            return ""
        try:
            content = self.memory_file.read_text().strip()
            # Truncate to last 2000 chars to keep prompt size manageable
            if len(content) > 2000:
                content = "..." + content[-2000:]
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
                    f"${p.get('size_usd', 0):.0f} "
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
                    f"held {o.get('hold_time', '?')}"
                )
            sections.append("## Recent Trade Outcomes\n" + "\n".join(out_lines))

        # 4. Strategy memory (learned patterns — sanitize to prevent injection)
        if memory:
            sections.append(f"## Learned Patterns\n{sanitize_reasoning(memory)}")

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
