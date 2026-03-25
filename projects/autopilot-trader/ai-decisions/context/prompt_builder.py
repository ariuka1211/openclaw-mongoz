"""Prompt builder — assembles LLM user prompt with token budget enforcement."""
import logging
from datetime import datetime, timezone

from context.token_estimator import estimate_tokens, MAX_PROMPT_TOKENS
from context.sanitizer import sanitize_reasoning

log = logging.getLogger("ai-trader.context.prompt")


class PromptBuilder:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

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
        self.ai_trader.pattern_engine.decay_patterns()

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
        stats_section = self.ai_trader.stats_formatter.build_live_stats_section()
        section_tokens["stats"] = estimate_tokens(stats_section) if stats_section else 0
        sections.append(stats_section)

        # 5. Learned Patterns (from pattern rules with decay)
        patterns_section = self.ai_trader.pattern_engine.build_section()
        section_tokens["patterns"] = estimate_tokens(patterns_section) if patterns_section else 0
        sections.append(patterns_section)

        # 6. Recent Holds (hold regret from last 6h)
        regret_section = self.ai_trader.stats_formatter.build_hold_regret_section()
        section_tokens["regret"] = estimate_tokens(regret_section) if regret_section else 0
        sections.append(regret_section)

        # 7. Account state with win rate and timing
        # Win rate from DB performance stats
        stats = self.ai_trader.db.get_performance_stats()
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

        max_pos = self.ai_trader.config.get("safety", {}).get("max_positions", 8)
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
            f"- Daily realized PnL: ${self.ai_trader.db.get_daily_pnl():+.2f}\n"
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