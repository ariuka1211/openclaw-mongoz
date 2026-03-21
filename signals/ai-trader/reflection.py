"""
Reflection agent — periodic learning from trade outcomes.
Runs via cron or triggered every 2-4 hours.
Uses cheaper model (mimo-v2-pro) to extract patterns.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from context_builder import strip_injection_patterns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ai-trader.reflection")

REFLECTION_PROMPT = """Review these recent trade outcomes and extract patterns.

{outcomes}

{quantitative_stats}

Based on these trades and statistics, write 3-5 concise bullet points for the trading strategy.
Focus on ACTIONABLE adjustments, not descriptions of what happened.
Format as markdown bullet points. Be specific about numbers when possible.

Examples of good patterns:
- "Our SHORT win rate is 40% vs LONG 65% → bias toward longs"
- "Positions held > 12h tend to deteriorate → consider time-based exits"
- "Funding rate > 0.05%/8h → contrarian short often wins within 4h"

Examples of bad patterns (avoid these):
- "We traded ROBO today" (descriptive, not actionable)
- "The market was volatile" (too vague)
"""


def _gather_quantitative_stats(db) -> str:
    """Run statistical analyses on outcomes and return formatted summary."""
    lines = []

    # 1. Win rate by direction
    rows = db._conn.execute("""
        SELECT direction,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
               COALESCE(AVG(pnl_usd), 0) as avg_pnl,
               COALESCE(SUM(pnl_usd), 0) as total_pnl
        FROM outcomes
        GROUP BY direction
    """).fetchall()
    if rows:
        lines.append("### Win Rate by Direction")
        for r in rows:
            dir_name, total, wins, avg_pnl, total_pnl = r
            wr = (wins / total * 100) if total > 0 else 0
            lines.append(f"- {dir_name or 'unknown'}: {wr:.0f}% ({wins}/{total}), avg=${avg_pnl:+.2f}, total=${total_pnl:+.2f}")

    # 2. Win rate by hold time bracket
    rows = db._conn.execute("""
        SELECT
            CASE
                WHEN hold_time_seconds < 1800 THEN '<30min'
                WHEN hold_time_seconds < 7200 THEN '30min-2h'
                ELSE '2h+'
            END as bracket,
            COUNT(*) as total,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
            COALESCE(AVG(pnl_usd), 0) as avg_pnl
        FROM outcomes
        GROUP BY bracket
        ORDER BY MIN(hold_time_seconds)
    """).fetchall()
    if rows:
        lines.append("\n### Win Rate by Hold Time")
        for r in rows:
            bracket, total, wins, avg_pnl = r
            wr = (wins / total * 100) if total > 0 else 0
            lines.append(f"- {bracket}: {wr:.0f}% ({wins}/{total}), avg=${avg_pnl:+.2f}")

    # 3. Win rate by entry confidence (from decision_snapshot)
    # confidence is stored in the decisions table, join on symbol+timestamp proximity
    rows = db._conn.execute("""
        SELECT
            CASE
                WHEN d.confidence >= 0.7 THEN 'high (≥0.7)'
                WHEN d.confidence >= 0.4 THEN 'medium (0.4-0.7)'
                ELSE 'low (<0.4)'
            END as conf_bracket,
            COUNT(*) as total,
            SUM(CASE WHEN o.pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
            COALESCE(AVG(o.pnl_usd), 0) as avg_pnl
        FROM outcomes o
        LEFT JOIN decisions d ON d.symbol = o.symbol
            AND ABS(strftime('%s', d.timestamp) - strftime('%s', o.timestamp)) < 300
        WHERE d.confidence IS NOT NULL
        GROUP BY conf_bracket
    """).fetchall()
    if rows:
        lines.append("\n### Win Rate by Entry Confidence")
        for r in rows:
            bracket, total, wins, avg_pnl = r
            wr = (wins / total * 100) if total > 0 else 0
            lines.append(f"- {bracket}: {wr:.0f}% ({wins}/{total}), avg=${avg_pnl:+.2f}")

    # 4. Biggest loss patterns — exit reasons that dominate losses
    rows = db._conn.execute("""
        SELECT exit_reason,
               COUNT(*) as total_losses,
               COALESCE(SUM(pnl_usd), 0) as total_loss_usd,
               COALESCE(AVG(pnl_usd), 0) as avg_loss
        FROM outcomes
        WHERE pnl_usd <= 0
        GROUP BY exit_reason
        ORDER BY total_loss_usd ASC
        LIMIT 5
    """).fetchall()
    if rows:
        lines.append("\n### Biggest Loss Patterns (by exit reason)")
        for r in rows:
            reason, count, total_loss, avg_loss = r
            lines.append(f"- {reason}: {count} losses, total=${total_loss:+.2f}, avg=${avg_loss:+.2f}")

    # 5. Overall stats summary
    stats = db.get_performance_stats()
    lines.append(
        f"\n### Overall Stats\n"
        f"- Win rate: {stats['win_rate']:.0f}% ({stats['wins']}/{stats['total_trades']})\n"
        f"- Avg win: ${stats['avg_win']:+.2f} | Avg loss: ${stats['avg_loss']:+.2f}\n"
        f"- Total PnL: ${stats['total_pnl']:+.2f}\n"
        f"- Best single: ${stats['avg_win']:+.2f} | Worst single: ${stats['max_drawdown']:+.2f}"
    )

    return "\n".join(lines) if lines else ""


class ReflectionAgent:
    def __init__(self, config_path: str = "config.json"):
        with open(config_path) as f:
            self.config = json.load(f)

        self.api_base = self.config["llm"]["api_base"]
        self.model = self.config["llm"]["reflection_model"]
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.db_path = self.config["db_path"]
        self.memory_path = Path(self.config.get("strategy_memory_file", "state/strategy_memory.md"))

    async def reflect(self):
        """Run reflection on recent trade outcomes."""
        log.info("Starting reflection...")

        # Import DB
        sys.path.insert(0, str(Path(__file__).parent))
        from db import DecisionDB

        db = DecisionDB(self.db_path)
        outcomes = db.get_recent_outcomes(limit=20)

        if len(outcomes) < 3:
            log.info(f"Only {len(outcomes)} outcomes, need at least 3. Skipping reflection.")
            db.close()
            return

        # Format outcomes for prompt
        outcome_lines = []
        for o in outcomes:
            emoji = "🟢" if o.get("pnl_usd", 0) > 0 else "🔴"
            outcome_lines.append(
                f"{emoji} {o['symbol']} {o.get('direction', '?')} → "
                f"${o.get('pnl_usd', 0):+.2f} ({o.get('pnl_pct', 0):+.1f}%) "
                f"held {o.get('hold_time', '?')} "
                f"exit: {o.get('exit_reason', '?')}"
            )

        # Gather quantitative stats
        quant_stats = _gather_quantitative_stats(db)
        log.info(f"Quantitative stats:\n{quant_stats}")

        prompt = REFLECTION_PROMPT.format(
            outcomes="\n".join(outcome_lines),
            quantitative_stats=quant_stats,
        )

        # Call LLM
        try:
            response = await self._call_llm(prompt)
            log.info(f"Reflection result:\n{response}")

            # Sanitize before writing to strategy memory (prevent prompt injection)
            safe_response = strip_injection_patterns(response)

            # Read existing memory (if any) and append new reflections with timestamp
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            new_entry = f"## Learned Patterns ({timestamp})\n\n{safe_response}\n"

            existing_content = ""
            if self.memory_path.exists():
                existing_content = self.memory_path.read_text()

            # Append new entry (file grows over time, preserving history)
            combined = existing_content.rstrip() + "\n\n" + new_entry if existing_content.strip() else new_entry
            self.memory_path.write_text(combined)
            log.info(f"Strategy memory updated: {self.memory_path}")

        except Exception as e:
            log.error(f"Reflection failed: {e}")
        finally:
            db.close()

    async def _call_llm(self, prompt: str) -> str:
        """Call the reflection model."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a trading analyst. Extract actionable patterns from trade outcomes. Be concise and specific.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 512,
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        return data["choices"][0]["message"]["content"]


async def main():
    config_path = os.environ.get("AI_TRADER_CONFIG", "config.json")
    agent = ReflectionAgent(config_path)
    await agent.reflect()


if __name__ == "__main__":
    asyncio.run(main())
