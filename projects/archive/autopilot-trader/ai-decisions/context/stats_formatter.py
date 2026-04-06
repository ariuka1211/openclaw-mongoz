"""Stats formatter — performance stats and hold regret for LLM prompt sections."""

import logging

log = logging.getLogger("ai-trader.context.stats")


class StatsFormatter:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    def build_live_stats_section(self) -> str:
        """Build performance window section from last 20 outcomes."""
        dir_stats = self.ai_trader.db.get_direction_stats(limit=20)
        hold_stats = self.ai_trader.db.get_hold_time_stats(limit=20)
        streak = self.ai_trader.db.get_streak(limit=5)

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

    def build_hold_regret_section(self) -> str:
        """Build hold regret section from last 6h of hold decisions."""
        try:
            regret_data = self.ai_trader.db.get_hold_regret_data(hours=6)
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
