"""Cycle runner — orchestrates one trading cycle: context → LLM → parse → safety → IPC → log."""

import hashlib
import json
import logging
import os
import time

from llm.parser import parse_decision_json
from llm_client import LLMStats

log = logging.getLogger("ai-trader.cycle")


class CycleRunner:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    async def execute(self, cycle_id: str):
        """Execute one trading cycle — skip LLM if nothing changed."""
        import asyncio  # local import to avoid circular; used for polling sleep

        log.info(f"--- Cycle {cycle_id} starting ---")

        # 1. Gather context
        # MED-22: Stat mtime BEFORE reading to guard against scanner overwriting mid-read.
        # If file changes between stat and read, retry once to ensure consistency.
        _signals_mtime_before = None
        try:
            _signals_mtime_before = os.path.getmtime(self.ai_trader.data_reader.signals_file)
        except OSError:
            pass  # File doesn't exist yet, read_signals() will handle it

        signals, signals_config = self.ai_trader.data_reader.read_signals()
        if not signals:
            log.info("No signals available, holding")
            return

        # Verify signals weren't overwritten between mtime check and read
        if _signals_mtime_before is not None:
            try:
                _signals_mtime_after = os.path.getmtime(self.ai_trader.data_reader.signals_file)
                if _signals_mtime_after != _signals_mtime_before:
                    log.info("Signals file changed during read, retrying once...")
                    signals, signals_config = self.ai_trader.data_reader.read_signals()
            except OSError:
                pass

        # HIGH-2: Check signals freshness — skip cycle if data is stale
        try:
            signals_age = time.time() - os.path.getmtime(self.ai_trader.data_reader.signals_file)
            # INTENTIONAL: Hardcoded to prevent trading on stale data. Safer as a fixed guard than
            # a config value that could be accidentally widened. Match scanner cycle (2-3 min).
            if signals_age > 600:  # 10 minutes
                log.warning(f"⚠️ Signals are stale (age={signals_age:.0f}s > 600s) — skipping cycle to avoid trading on frozen data")
                return
        except OSError:
            pass  # Can't stat file, proceed with caution

        positions = self.ai_trader.data_reader.read_positions()

        # Change detection — only call LLM when signals or positions actually changed
        # Only compare top 10 opportunities (what the AI sees in its prompt)
        # Round scores to reduce noise from recalculations
        top_signals = sorted(signals, key=lambda s: float(s.get("compositeScore", 0)), reverse=True)[:10]
        state_input = json.dumps({
            "signals": [(s.get("symbol"), round(float(s.get("compositeScore", 0))), s.get("direction")) for s in top_signals],
            "positions": [(p.get("symbol"), p.get("side"), round(float(p.get("size", 0)), 4)) for p in positions],
        }, sort_keys=True)
        log.debug(f"State hash: top_signals={len(top_signals)}, positions={len(positions)}")
        state_hash = hashlib.sha256(state_input.encode()).hexdigest()[:16]

        if state_hash == self.ai_trader._last_state_hash:
            self.ai_trader._cycles_skipped += 1
            log.info(f"⏸️ No changes since last cycle (skipped {self.ai_trader._cycles_skipped} consecutive), holding")
            return

        # State changed — reset skip counter and call LLM
        if self.ai_trader._cycles_skipped > 0:
            log.info(f"🔄 State changed after {self.ai_trader._cycles_skipped} skipped cycles")
        self.ai_trader._cycles_skipped = 0
        self.ai_trader._last_state_hash = state_hash
        history = self.ai_trader.db.get_recent_decisions(20)
        outcomes = self.ai_trader.db.get_recent_outcomes(10)

        # CRITICAL-5: Check for new losses and record them for cooldown gate
        for outcome in outcomes:
            ts = outcome.get("timestamp", "")
            if ts and self.ai_trader._last_processed_outcome_ts and ts <= self.ai_trader._last_processed_outcome_ts:
                continue
            pnl_usd = outcome.get("pnl_usd", 0)
            if pnl_usd is not None and pnl_usd < 0:
                log.info(f"📉 Loss detected: {outcome.get('symbol')} pnl=${pnl_usd:+.2f} — recording for cooldown")
                self.ai_trader.safety.record_loss()
            # Update tracker to newest outcome timestamp seen
            if ts and (self.ai_trader._last_processed_outcome_ts is None or ts > self.ai_trader._last_processed_outcome_ts):
                self.ai_trader._last_processed_outcome_ts = ts

        # Analyze outcomes → update pattern engine
        self.ai_trader.outcome_analyzer.analyze_and_update(outcomes, history)

        equity = signals_config.get("accountEquity", 1000)

        # 2. Build prompt
        context = self.ai_trader.prompt_builder.build_prompt(
            signals, positions, history, outcomes, signals_config
        )
        user_prompt = self.ai_trader.decision_template.replace("{context}", context)

        # 3. Call LLM
        t0 = time.time()
        result = await self.ai_trader.llm.call(
            system_prompt=self.ai_trader.system_prompt,
            user_prompt=user_prompt,
        )
        latency_ms = int((time.time() - t0) * 1000)
        raw_response = str(result)
        tokens_in = result.tokens_in
        tokens_out = result.tokens_out

        # 4. Parse
        decision = parse_decision_json(raw_response)
        log.info(
            f"LLM decision: {decision.get('action')} {decision.get('symbol', '')} "
            f"conf={decision.get('confidence', 0):.2f}"
        )

        # 5. Safety check
        safe, reasons = self.ai_trader.safety.validate(decision, positions, signals, equity)

        if safe:
            log.info(f"✅ Safety approved: {decision.get('action')} {decision.get('symbol', '')}")
        else:
            log.warning(f"⚠️ Safety rejected: {reasons}")

        # 6. Execute (if approved and not hold)
        executed = False
        if safe and decision.get("action") != "hold":
            executed = await self.ai_trader.bot_ipc.send_decision(decision, equity)
            if executed:
                # Poll for bot result with timeout (limit orders may take 10-30s to fill)
                result = None
                for _ in range(15):  # 15 * 2s = 30s max
                    await asyncio.sleep(2)
                    result = await self.ai_trader.bot_ipc.check_result(self.ai_trader._last_sent_decision_id)
                    if result is not None:
                        break

                # BUG 5: Clean up result file after successful match
                if result is not None and self.ai_trader.result_file.exists():
                    try:
                        self.ai_trader.result_file.unlink()
                        log.debug("🧹 Result file cleaned up after successful read")
                    except Exception:
                        pass

                # BUG 7: Fix false success — if no result after polling, mark as not executed
                if result is None:
                    log.warning(f"⚠️ Bot did not confirm decision {self.ai_trader._last_sent_decision_id} within 30s timeout — executed=False")
                    executed = False
                elif result.get("success") is False:
                    log.warning(f"Bot reported validation failure: {result.get('decision_action')} {result.get('decision_symbol')} -- not retrying")
                    executed = False
                elif executed and decision.get("action") == "open":
                    # Only count opens toward rate limit after bot confirms success
                    self.ai_trader.safety.record_order()

        # 7. Log to SQLite
        self.ai_trader.db.log_decision(
            cycle_id=cycle_id,
            decision=decision,
            safety_approved=safe,
            safety_reasons=reasons,
            executed=executed,
            positions_snapshot=positions,
            signals_snapshot=signals[:10],  # Top 10 only to save space
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        # Log token usage and estimated cost
        cost = (
            tokens_in * LLMStats.COST_PER_1M_INPUT / 1_000_000
            + tokens_out * LLMStats.COST_PER_1M_OUTPUT / 1_000_000
        )
        log.info(f"--- Cycle {cycle_id} done (latency={latency_ms}ms, tokens={tokens_in}→{tokens_out}, cost=${cost:.4f}, executed={executed}) ---")
