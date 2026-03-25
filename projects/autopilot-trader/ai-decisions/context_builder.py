"""
Context builder — assembles compressed prompt from signals, positions, history, outcomes.
Live reflection system: performance stats, pattern rules with decay, hold regret.
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

# ── Token estimation ────────────────────────────────────────────────

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(text: str) -> int:
        """Estimate token count using cl100k_base encoding."""
        return len(_enc.encode(text))

except ImportError:
    log.warning("tiktoken not installed — falling back to char//4 token estimation")

    def estimate_tokens(text: str) -> int:
        """Fallback token estimation: ~4 chars per token."""
        return len(text) // 4


MAX_PROMPT_TOKENS = 16000


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
            self.result_file = Path(config_dir) / config.get("result_file", "../ai-result.json")
            self.patterns_file = Path(config_dir) / config.get("patterns_file", "state/patterns.json")
        else:
            self.signals_file = Path(config["signals_file"])
            self.result_file = Path(config.get("result_file", "../ai-result.json"))
            self.patterns_file = Path(config.get("patterns_file", "state/patterns.json"))

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
        """Remove opportunities for symbols that were recently traded.

        Allows re-entry in the opposite direction — only blocks same-direction trades
        within the cooldown window.
        """
        try:
            recent_outcomes = self.db.get_recently_traded_symbols(hours=2)

            if not recent_outcomes:
                return opportunities

            filtered = []
            for o in opportunities:
                symbol = o.get('symbol')
                if symbol not in recent_outcomes:
                    filtered.append(o)
                    continue
                # Same direction — block (cooldown). Opposite direction — allow re-entry
                outcome_dir = recent_outcomes[symbol]
                opp_dir = o.get('direction')
                if opp_dir and outcome_dir and opp_dir != outcome_dir:
                    filtered.append(o)  # Opposite direction, allow re-entry

            removed = len(opportunities) - len(filtered)
            if removed > 0:
                log.info(f"Filtered out {removed} recently traded symbols (same direction cooldown)")

            return filtered
        except Exception as e:
            log.warning(f"Failed to filter traded symbols: {e}")
            return opportunities

    def read_positions(self) -> list[dict]:
        """Read current positions from bot's result file, falling back to DB if missing."""
        if self.result_file.exists():
            data = safe_read_json(self.result_file)
            if data and data.get("processed_decision_id"):
                # Bot has processed a decision — positions are fresh
                # MED-26: Canonical position size field is 'position_size_usd' (written by bot's _write_ai_result).
                # Both 'position_size_usd' and 'size_usd' are set by the bot for compatibility.
                return data.get("positions", [])

        # HIGH-9: Result file missing/empty — reconstruct from DB.
        # Find the most recent outcome per symbol. Open positions are those
        # whose last entry has no exit (exit_price is NULL) or was an "open" event.
        try:
            cursor = self.db.conn.cursor()
            cursor.execute('''
                SELECT o.symbol, o.direction, o.entry_price, o.size_usd, o.timestamp
                FROM outcomes o
                INNER JOIN (
                    SELECT symbol, MAX(id) as max_id
                    FROM outcomes
                    GROUP BY symbol
                ) latest ON o.id = latest.max_id
                WHERE o.exit_price IS NULL
                   OR o.exit_reason IN ('opened', 'new_position')
                ORDER BY o.timestamp DESC
            ''')
            rows = cursor.fetchall()
            if rows:
                positions = []
                for row in rows:
                    positions.append({
                        "symbol": row[0],
                        "side": row[1] if row[1] else "long",
                        "entry_price": row[2] or 0,
                        "size_usd": row[3] or 0,
                        "size": row[3] or 0,
                        "position_size_usd": row[3] or 0,
                        "current_price": row[2] or 0,  # No live price from DB
                        "source": "db_fallback",
                    })
                log.info(f"HIGH-9: Reconstructed {len(positions)} open positions from DB (result file unavailable)")
                return positions
        except Exception as e:
            log.warning(f"Failed to read positions from DB fallback: {e}")

        return []

    # ── Pattern rules with decay ──────────────────────────────────────

    def _load_patterns(self) -> dict:
        """Load patterns.json. Returns dict with 'patterns' list."""
        if not self.patterns_file.exists():
            return {"patterns": []}
        try:
            data = json.loads(self.patterns_file.read_text())
            if isinstance(data, dict) and "patterns" in data:
                return data
            return {"patterns": []}
        except (json.JSONDecodeError, OSError):
            return {"patterns": []}

    def _save_patterns(self, data: dict):
        """Write patterns.json atomically."""
        self.patterns_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.patterns_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self.patterns_file)

    def read_patterns(self) -> list[dict]:
        """Returns active patterns with confidence >= 0.4."""
        data = self._load_patterns()
        return [p for p in data.get("patterns", []) if p.get("confidence", 0) >= 0.4]

    def decay_patterns(self, decay: float = 0.02):
        """Subtract decay from each pattern confidence, drop below 0.3, write back."""
        if not self.patterns_file.exists():
            return
        try:
            data = self._load_patterns()
            original_count = len(data.get("patterns", []))
            updated = []
            for p in data.get("patterns", []):
                new_conf = p.get("confidence", 0) - decay
                if new_conf >= 0.3:
                    p["confidence"] = round(new_conf, 2)
                    updated.append(p)
            data["patterns"] = updated
            self._save_patterns(data)
            dropped = original_count - len(updated)
            if dropped > 0:
                log.info(f"Pattern decay: dropped {dropped} weak patterns, {len(updated)} remain")
        except Exception as e:
            log.warning(f"Pattern decay failed: {e}")

    def reinforce_pattern(self, rule_text: str, boost: float = 0.1):
        """Bump confidence of existing pattern or add new one at 0.5."""
        data = self._load_patterns()
        for p in data.get("patterns", []):
            if p.get("rule", "").lower() == rule_text.lower():
                p["confidence"] = min(1.0, round(p.get("confidence", 0) + boost, 2))
                self._save_patterns(data)
                return
        # New pattern
        data["patterns"].append({"rule": rule_text, "confidence": 0.5})
        self._save_patterns(data)

    def build_patterns_section(self) -> str:
        """Format active patterns (confidence >= 0.4) for prompt."""
        patterns = self.read_patterns()
        if not patterns:
            return ""
        lines = ["## Learned Patterns"]
        for p in sorted(patterns, key=lambda x: x.get("confidence", 0), reverse=True):
            lines.append(f"- {p['rule']} (confidence: {p.get('confidence', 0):.1f})")
        return "\n".join(lines)

    # ── Live stats section ────────────────────────────────────────────

    def build_live_stats_section(self) -> str:
        """Build performance window section from last 20 outcomes."""
        dir_stats = self.db.get_direction_stats(limit=20)
        hold_stats = self.db.get_hold_time_stats(limit=20)
        streak = self.db.get_streak(limit=5)

        if not dir_stats and not hold_stats:
            return ""

        lines = ["## Performance Window (last 20)"]
        for direction in ["long", "short"]:
            d = dir_stats.get(direction)
            if d and d["total"] > 0:
                win_pct = int(d["wins"] / d["total"] * 100)
                sign = "+" if d["avg_pnl"] >= 0 else ""
                lines.append(
                    f"- {direction.capitalize()}s: {win_pct}% win rate "
                    f"({d['wins']}/{d['total']}) avg=${sign}{d['avg_pnl']:.2f}"
                )

        if hold_stats:
            hold_parts = []
            for h in hold_stats:
                if h["total"] > 0:
                    wp = int(h["wins"] / h["total"] * 100)
                    hold_parts.append(f"{h['bracket']} {wp}% win")
            if hold_parts:
                lines.append(f"- Hold time: {' | '.join(hold_parts)}")

        if streak["count"] > 0:
            lines.append(f"- Streak: {streak['count']} {streak['type']}s")

        return "\n".join(lines)

    # ── Hold regret section ───────────────────────────────────────────

    def build_hold_regret_section(self) -> str:
        """Build hold regret section from last 6h of hold decisions."""
        try:
            regret_data = self.db.get_hold_regret_data(hours=6)
        except Exception as e:
            log.warning(f"Hold regret query failed: {e}")
            return ""

        if not regret_data:
            return ""

        lines = ["## Recent Holds (last 6h)"]
        for entry in regret_data[:5]:  # Show at most 5
            ts_short = entry["timestamp"][11:16] if len(entry["timestamp"]) > 16 else entry["timestamp"]
            top = entry["top_signals"]
            traded = entry["traded_after"]
            missed = entry["missed_pnl"]

            if missed:
                missed_parts = []
                for sym, pnl in missed.items():
                    sign = "+" if pnl >= 0 else ""
                    missed_parts.append(f"{sym} was later traded for ${sign}{pnl:.2f} (missed)")
                lines.append(f"- {ts_short} held — {', '.join(missed_parts)}")
            elif traded:
                lines.append(f"- {ts_short} held — top signals {', '.join(top[:3])} — traded but not profitable")
            else:
                lines.append(f"- {ts_short} held — top signals {', '.join(top[:3])} — none traded after")

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────

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
        signals_config: dict,
    ) -> str:
        """Assemble the LLM user prompt from compressed context.
        Live reflection: performance stats, pattern rules, hold regret.
        Tracks per-section token estimates and enforces MAX_PROMPT_TOKENS.
        """
        # Decay pattern confidences at the start of each cycle
        self.decay_patterns()

        sections = []
        section_tokens = {}

        # Resolve equity early — needed by positions ROE calc below
        equity = signals_config.get("accountEquity", 1000)

        # 1. Current positions (compressed)
        if positions:
            pos_summary = []
            for p in positions:
                roe = self._calc_roe(p, equity)
                pos_summary.append(
                    f"- {p.get('symbol', '?')} {p.get('side', '?').upper()} "
                    # MED-26: Canonical field is 'position_size_usd' (written by bot's _write_ai_result)
                    f"${p.get('position_size_usd', p.get('size_usd', 0)):.0f} "
                    f"@ {p.get('entry_price', 0):.4f} "
                    f"(ROE: {roe:+.1f}%)"
                )
            pos_section = "## Open Positions\n" + "\n".join(pos_summary)
        else:
            pos_section = "## Open Positions\nNone open"
        section_tokens["positions"] = estimate_tokens(pos_section)
        sections.append(pos_section)

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
            sig_section = "## Market Opportunities (Top 10)\n" + "\n".join(sig_lines)
        else:
            sig_section = "## Market Opportunities\nNone available"
        section_tokens["signals"] = estimate_tokens(sig_section)
        sections.append(sig_section)

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
            out_section = "## Recent Trade Outcomes\n" + "\n".join(out_lines)
            section_tokens["outcomes"] = estimate_tokens(out_section)
            sections.append(out_section)
        else:
            section_tokens["outcomes"] = 0

        # 4. Performance Window (live stats from last 20 outcomes)
        stats_section = self.build_live_stats_section()
        section_tokens["stats"] = estimate_tokens(stats_section) if stats_section else 0
        sections.append(stats_section)

        # 5. Learned Patterns (from pattern rules with decay)
        patterns_section = self.build_patterns_section()
        section_tokens["patterns"] = estimate_tokens(patterns_section) if patterns_section else 0
        sections.append(patterns_section)

        # 6. Recent Holds (hold regret from last 6h)
        regret_section = self.build_hold_regret_section()
        section_tokens["regret"] = estimate_tokens(regret_section) if regret_section else 0
        sections.append(regret_section)

        # 7. Account state with win rate and timing
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

        max_pos = self.config.get("safety", {}).get("max_positions", 8)
        if len(positions) >= max_pos:
            slots_status = f"⚠️ ALL SLOTS FULL ({len(positions)}/{max_pos}) — DO NOT open new positions. Only consider closing existing ones."
        elif len(positions) == 0:
            slots_status = f"📊 {max_pos} slots available — no positions open. Scan signals for high-score opportunities."
        else:
            available = max_pos - len(positions)
            slots_status = f"📊 {available}/{max_pos} slots open — consider adding positions from strong signals (score ≥ 70, safety ✅)."

        account_section = (
            f"## Account\n"
            f"- Equity: ${equity:.2f}\n"
            f"{slots_status}\n"
            f"- Daily realized PnL: ${self.db.get_daily_pnl():+.2f}\n"
            f"- Win rate: {win_rate:.0f}% ({wins}/{total_trades})\n"
            f"- Avg win: ${avg_win:+.2f} | Avg loss: ${avg_loss:+.2f}\n"
            f"{last_dec_str}\n"
            f"- Session: {session} ({datetime.now(timezone.utc).strftime('%H:%M')} UTC)"
        )
        section_tokens["account"] = estimate_tokens(account_section)
        sections.append(account_section)

        # 8. Recent decisions (last 5, for context)
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
            dec_section = "## Recent Decisions\n" + "\n".join(dec_lines)
            section_tokens["decisions"] = estimate_tokens(dec_section)
            sections.append(dec_section)
        else:
            section_tokens["decisions"] = 0

        # Size guard: if total exceeds MAX_PROMPT_TOKENS, truncate new sections first
        total_est = sum(section_tokens.values())
        new_section_keys = ["stats", "patterns", "regret"]

        if total_est > MAX_PROMPT_TOKENS:
            # First, try emptying new sections (index 3, 4, 5)
            for idx_offset, key in enumerate([3, 4, 5]):
                if section_tokens[new_section_keys[idx_offset]] > 0:
                    sections[key] = ""
                    section_tokens[new_section_keys[idx_offset]] = 0
                    total_est = sum(section_tokens.values())
                    if total_est <= MAX_PROMPT_TOKENS:
                        break
            if total_est > MAX_PROMPT_TOKENS:
                log.warning(
                    f"⚠️ Prompt token estimate {total_est} exceeds MAX_PROMPT_TOKENS={MAX_PROMPT_TOKENS} "
                    f"— new reflection sections cleared"
                )

        # Log token breakdown
        log.info(
            f"Token estimate: total={total_est} "
            f"(signals={section_tokens.get('signals', 0)}, "
            f"positions={section_tokens.get('positions', 0)}, "
            f"outcomes={section_tokens.get('outcomes', 0)}, "
            f"stats={section_tokens.get('stats', 0)}, "
            f"patterns={section_tokens.get('patterns', 0)}, "
            f"regret={section_tokens.get('regret', 0)}, "
            f"account={section_tokens.get('account', 0)}, "
            f"decisions={section_tokens.get('decisions', 0)})"
        )

        return "\n\n".join(s for s in sections if s)

    @staticmethod
    def _calc_roe(position: dict, equity: float = 0) -> float:
        """Calculate ROE% for a position.
        
        For cross margin: ROE = raw_move% × (notional / equity)
        Falls back to isolated margin (raw_move% × leverage) if equity is unavailable.
        """
        entry = position.get("entry_price", 0)
        current = position.get("current_price", entry)
        side = position.get("side", "long")
        if entry == 0:
            return 0.0
        raw = (current - entry) / entry * 100
        if side == "short":
            raw = -raw
        # Cross margin ROE: effective_leverage = notional / equity
        notional = position.get("position_size_usd", position.get("size_usd", 0))
        if equity > 0 and notional > 0:
            effective_lev = notional / equity
            return raw * effective_lev
        # Fallback: isolated margin
        leverage = position.get("leverage", 1.0)
        return raw * leverage
