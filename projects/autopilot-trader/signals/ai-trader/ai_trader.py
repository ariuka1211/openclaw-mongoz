"""
AI Trader — Main daemon
Async loop, 3-min cycles, systemd lifecycle.
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from context_builder import ContextBuilder
from db import DecisionDB
from llm_client import LLMClient
import dashboard
from safety import SafetyLayer

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import atomic_write, safe_read_json


# ── Logging setup ────────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ai-trader.log"),
    ],
)
log = logging.getLogger("ai-trader")


def load_prompts() -> tuple[str, str]:
    """Load system and decision prompt templates."""
    prompt_dir = Path("prompts")
    system_prompt = (prompt_dir / "system.txt").read_text()
    decision_prompt = (prompt_dir / "decision.txt").read_text()
    return system_prompt, decision_prompt


def parse_decision_json(raw: str) -> dict:
    """Parse LLM response into a decision dict. Handles markdown code blocks and extra text."""
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    # Find the JSON object using brace-depth tracking
    start = text.find("{")
    if start == -1:
        log.error(f"No JSON object found in LLM response: {raw[:300]}")
        return {"action": "hold", "reasoning": "No JSON found in response", "confidence": 0}

    depth = 0
    end = None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        log.error(f"Unmatched braces in LLM response: {raw[:300]}")
        return {"action": "hold", "reasoning": "Unmatched JSON braces", "confidence": 0}

    json_text = text[start:end]

    try:
        decision = json.loads(json_text)
        return decision
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse LLM JSON: {e}\nRaw: {raw[:500]}")
        return {"action": "hold", "reasoning": f"JSON parse error: {e}", "confidence": 0}


class AITrader:

    def __init__(self, config: dict):
        self.config = config
        self.db = DecisionDB(config["db_path"])
        # Resolve relative paths relative to config file directory
        _config_dir = os.path.dirname(os.path.abspath(config["_config_path"]))

        self.context_builder = ContextBuilder(config, self.db, config_dir=_config_dir)
        self.safety = SafetyLayer(config, self.db)
        self.llm = LLMClient(config["llm"])
        self.system_prompt, self.decision_template = load_prompts()

        self.cycle_interval = config.get("cycle_interval_seconds", 180)
        self.MAX_CONSECUTIVE_FAILURES = config.get("max_consecutive_failures", 5)
        self.max_rejection_halt_count = config.get("safety", {}).get("max_rejection_halt_count", 15)
        self.rejection_halt_window_minutes = config.get("safety", {}).get("rejection_halt_window_minutes", 30)
        self._last_purge_time: float = 0
        self.running = True
        self.emergency_halt = False
        self.consecutive_failures = 0
        self.last_cycle_time: float = 0
        # Change detection — only call LLM when something actually changed
        self._last_state_hash: str | None = None
        self._cycles_skipped: int = 0
        # IPC-02: Track last sent decision_id for result correlation
        self._last_sent_decision_id = None
        # ACK timeout — if decision not ACKed after this many seconds, treat as stale
        self._ack_timeout_seconds = config.get("ack_timeout_seconds", 300)
        # Track last processed outcome timestamp for loss detection (CRITICAL-5)
        self._last_processed_outcome_ts: str | None = None

        # Paths
        self.decision_file = Path(_config_dir) / config["decision_file"]
        self.result_file = Path(_config_dir) / config.get("result_file", "../ai-result.json")

        log.info(f"AI Trader initialized")
        log.info(f"  Models: {config['llm']['primary_model']} / {config['llm']['fallback_model']}")
        log.info(f"  DB: {config['db_path']}")
        log.info(f"  Decision file: {self.decision_file}")
        log.info(f"  Kill switch: {self.MAX_CONSECUTIVE_FAILURES} failures, {self.max_rejection_halt_count} rejections/{self.rejection_halt_window_minutes}min")

    async def run_forever(self):
        """Main daemon loop — runs until shutdown or emergency halt."""
        log.info("🚀 AI Trader starting...")

        # BUG 4: Clean up stale IPC files from previous run
        cleaned = []
        for fpath in [self.decision_file, Path(str(self.decision_file) + ".ack"), self.result_file]:
            if fpath.exists():
                try:
                    fpath.unlink()
                    cleaned.append(fpath.name)
                except Exception:
                    pass
        if cleaned:
            log.info(f"🧹 IPC startup cleanup: removed {', '.join(cleaned)}")
        else:
            log.info("🧹 IPC startup cleanup: no stale files found")

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        cycle_count = 0
        while self.running and not self.emergency_halt:
            start = time.time()
            cycle_id = str(uuid.uuid4())[:8]
            cycle_count += 1

            try:
                await self.execute_cycle(cycle_id)
                self.consecutive_failures = 0
                log.info(f"Cycle {cycle_id} complete ({cycle_count} total)")
            except Exception as e:
                self.consecutive_failures += 1
                self.safety._last_failure_time = time.time()
                log.error(
                    f"Cycle {cycle_id} failed ({self.consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}): {e}",
                    exc_info=True,
                )
                self.db.log_alert("error", f"Cycle failed: {e}")

                if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    await self._emergency_halt(f"{self.MAX_CONSECUTIVE_FAILURES}+ consecutive failures")
                    log.critical(f"🚨 Emergency halt — {self.MAX_CONSECUTIVE_FAILURES}+ consecutive failures")
                    self.db.log_alert("critical", f"Emergency halt — {self.MAX_CONSECUTIVE_FAILURES}+ consecutive failures")

            # If already halted, skip kill switch check and break out
            if self.emergency_halt:
                break

            # Check kill switches
            signals, signals_config = self.context_builder.read_signals()
            equity = signals_config.get("accountEquity", 0)
            kill_triggers = self.safety.check_kill_switch(
                self.consecutive_failures,
                self.db.count_recent_rejections(self.rejection_halt_window_minutes),
                equity=equity,
            )
            if kill_triggers:
                await self._emergency_halt(f"Kill switch: {'; '.join(kill_triggers)}")
                log.critical(f"🚨 Kill switch triggered: {kill_triggers}")
                self.db.log_alert("critical", f"Kill switch: {'; '.join(kill_triggers)}")

            # Periodic DB purge (~every 24h)
            if time.time() - self._last_purge_time > 86400:
                self._last_purge_time = time.time()
                try:
                    self.db.purge_old_data(keep_days=7)
                except Exception as e:
                    log.warning(f"DB purge failed: {e}")

            self.last_cycle_time = time.time()

            # Sleep until next cycle
            elapsed = time.time() - start
            sleep_time = max(0, self.cycle_interval - elapsed)
            if self.running and not self.emergency_halt:
                log.info(f"Next cycle in {sleep_time:.0f}s")
                await asyncio.sleep(sleep_time)

        # Cleanup
        if self.emergency_halt:
            # Give bot time to process the close_all decision before we exit
            log.info("Waiting 60s for bot to process close_all...")
            await asyncio.sleep(60)
        await self.llm.close()
        self.db.close()

        if self.emergency_halt:
            log.critical("🛑 AI Trader halted (emergency). Manual restart required.")
        else:
            log.info("AI Trader stopped gracefully.")

    async def execute_cycle(self, cycle_id: str):
        """Execute one trading cycle — skip LLM if nothing changed."""
        log.info(f"--- Cycle {cycle_id} starting ---")

        # 1. Gather context
        # MED-22: Stat mtime BEFORE reading to guard against scanner overwriting mid-read.
        # If file changes between stat and read, retry once to ensure consistency.
        _signals_mtime_before = None
        try:
            _signals_mtime_before = os.path.getmtime(self.context_builder.signals_file)
        except OSError:
            pass  # File doesn't exist yet, read_signals() will handle it

        signals, signals_config = self.context_builder.read_signals()
        if not signals:
            log.info("No signals available, holding")
            return

        # Verify signals weren't overwritten between mtime check and read
        if _signals_mtime_before is not None:
            try:
                _signals_mtime_after = os.path.getmtime(self.context_builder.signals_file)
                if _signals_mtime_after != _signals_mtime_before:
                    log.info("Signals file changed during read, retrying once...")
                    signals, signals_config = self.context_builder.read_signals()
            except OSError:
                pass

        # HIGH-2: Check signals freshness — skip cycle if data is stale
        try:
            signals_age = time.time() - os.path.getmtime(self.context_builder.signals_file)
            if signals_age > 600:  # 10 minutes
                log.warning(f"⚠️ Signals are stale (age={signals_age:.0f}s > 600s) — skipping cycle to avoid trading on frozen data")
                return
        except OSError:
            pass  # Can't stat file, proceed with caution

        positions = self.context_builder.read_positions()

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

        if state_hash == self._last_state_hash:
            self._cycles_skipped += 1
            log.info(f"⏸️ No changes since last cycle (skipped {self._cycles_skipped} consecutive), holding")
            return

        # State changed — reset skip counter and call LLM
        if self._cycles_skipped > 0:
            log.info(f"🔄 State changed after {self._cycles_skipped} skipped cycles")
        self._cycles_skipped = 0
        self._last_state_hash = state_hash
        history = self.context_builder.read_recent_decisions(limit=20)
        outcomes = self.context_builder.read_recent_outcomes(limit=10)

        # CRITICAL-5: Check for new losses and record them for cooldown gate
        for outcome in outcomes:
            ts = outcome.get("timestamp", "")
            if ts and self._last_processed_outcome_ts and ts <= self._last_processed_outcome_ts:
                continue
            pnl_usd = outcome.get("pnl_usd", 0)
            if pnl_usd is not None and pnl_usd < 0:
                log.info(f"📉 Loss detected: {outcome.get('symbol')} pnl=${pnl_usd:+.2f} — recording for cooldown")
                self.safety.record_loss()
            # Update tracker to newest outcome timestamp seen
            if ts and (self._last_processed_outcome_ts is None or ts > self._last_processed_outcome_ts):
                self._last_processed_outcome_ts = ts

        memory = self.context_builder.read_strategy_memory()

        equity = signals_config.get("accountEquity", 1000)

        # 2. Build prompt
        context = self.context_builder.build_prompt(
            signals, positions, history, outcomes, memory, signals_config
        )
        user_prompt = self.decision_template.replace("{context}", context)

        # 3. Call LLM
        t0 = time.time()
        raw_response = await self.llm.call(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
        )
        latency_ms = int((time.time() - t0) * 1000)

        # 4. Parse
        decision = parse_decision_json(raw_response)
        log.info(
            f"LLM decision: {decision.get('action')} {decision.get('symbol', '')} "
            f"conf={decision.get('confidence', 0):.2f}"
        )

        # 5. Safety check
        safe, reasons = self.safety.validate(decision, positions, signals, equity)

        if safe:
            log.info(f"✅ Safety approved: {decision.get('action')} {decision.get('symbol', '')}")
        else:
            log.warning(f"⚠️ Safety rejected: {reasons}")

        # 6. Execute (if approved and not hold)
        executed = False
        if safe and decision.get("action") != "hold":
            executed = await self._send_to_bot(decision, equity)
            if executed:
                # Poll for bot result with timeout (limit orders may take 10-30s to fill)
                result = None
                for _ in range(15):  # 15 * 2s = 30s max
                    await asyncio.sleep(2)
                    result = await self._check_bot_result(self._last_sent_decision_id)
                    if result is not None:
                        break

                # BUG 5: Clean up result file after successful match
                if result is not None and self.result_file.exists():
                    try:
                        self.result_file.unlink()
                        log.debug("🧹 Result file cleaned up after successful read")
                    except Exception:
                        pass

                # BUG 7: Fix false success — if no result after polling, mark as not executed
                if result is None:
                    log.warning(f"⚠️ Bot did not confirm decision {self._last_sent_decision_id} within 30s timeout — executed=False")
                    executed = False
                elif result.get("success") is False:
                    log.warning(f"Bot reported validation failure: {result.get('decision_action')} {result.get('decision_symbol')} -- not retrying")
                    executed = False
                elif executed and decision.get("action") == "open":
                    # Only count opens toward rate limit after bot confirms success
                    self.safety.record_order()

        # 7. Log to SQLite
        self.db.log_decision(
            cycle_id=cycle_id,
            decision=decision,
            safety_approved=safe,
            safety_reasons=reasons,
            executed=executed,
            positions_snapshot=positions,
            signals_snapshot=signals[:10],  # Top 10 only to save space
            latency_ms=latency_ms,
        )

        log.info(f"--- Cycle {cycle_id} done (latency={latency_ms}ms, executed={executed}) ---")
        
        # LOW: Update dashboard cycle timestamp
        dashboard.set_cycle_time()

    async def _check_bot_result(self, decision_id: str) -> dict | None:
        """Check bot result file for correlation with sent decision.

        Returns result dict if found with matching processed_decision_id, else None.
        """
        if not self.result_file.exists():
            return None
        result = safe_read_json(self.result_file)
        if not result:
            return None
        # IPC-02: Correlate via processed_decision_id
        if result.get("processed_decision_id") != decision_id:
            log.debug(f"Result file exists but processed_decision_id={result.get('processed_decision_id')} "
                       f"doesn't match sent {decision_id}")
            return None
        return result

    async def _send_to_bot(self, decision: dict, equity: float = 1000) -> bool:
        """Write decision to shared JSON file for bot to consume.

        IPC protocol (atomic, race-safe):
        1. Read current decision + ACK status
        2. If current decision not ACKed -> skip (bot still processing)
        3. Build new decision, include prev_decision_id for bot-side verification
        4. Re-verify ACK still matches (guards against bot ACKing between steps 1 and 4)
        5. Atomic write via os.replace (temp file -> decision file)
        6. Delete ACK file (bot already consumed it)
        """
        try:
            ack_path = str(self.decision_file) + ".ack"
            prev_decision_id: str | None = None

            # Step 1-2: Check if current decision is still being processed
            if self.decision_file.exists():
                current = safe_read_json(self.decision_file)
                if current:
                    current_id = current.get("decision_id", "")
                    try:
                        acked_id = Path(ack_path).read_text().strip() if Path(ack_path).exists() else ""
                    except Exception:
                        acked_id = ""

                    if current_id and acked_id != current_id:
                        # Check if decision is stale (ACK timeout)
                        try:
                            current_ts = current.get("timestamp", "")
                            if current_ts:
                                decision_dt = datetime.fromisoformat(current_ts.replace("Z", "+00:00"))
                                age_seconds = (datetime.now(timezone.utc) - decision_dt).total_seconds()
                                if age_seconds > self._ack_timeout_seconds:
                                    log.warning(
                                        f"⚠️ Decision {current_id} is stale (age={age_seconds:.0f}s > "
                                        f"timeout={self._ack_timeout_seconds}s), forcing overwrite"
                                    )
                                    prev_decision_id = None  # Skip prev tracking for stale override
                                    # Don't return False — allow write to proceed
                                else:
                                    log.info(f"⏳ Bot hasn't processed decision {current_id} yet (acked={acked_id}), skipping write")
                                    return False
                            else:
                                log.info(f"⏳ Bot hasn't processed decision {current_id} yet (acked={acked_id}), skipping write")
                                return False
                        except (ValueError, TypeError):
                            log.info(f"⏳ Bot hasn't processed decision {current_id} yet (acked={acked_id}), skipping write")
                            return False

                    # Capture ACKed ID for inclusion in new decision (bot-side verification)
                    if current_id and acked_id == current_id:
                        prev_decision_id = current_id

            # Step 3: Build new decision
            decision_id = str(uuid.uuid4())[:8]
            output = {
                "decision_id": decision_id,
                "prev_decision_id": prev_decision_id,  # IPC: lets bot verify it ACKed the right one
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": decision.get("action"),
                "symbol": decision.get("symbol"),
                "direction": decision.get("direction"),
                "size_pct_equity": decision.get("size_pct_equity"),
                "leverage": decision.get("leverage"),
                "stop_loss_pct": decision.get("stop_loss_pct"),
                "reasoning": decision.get("reasoning", ""),
                "confidence": decision.get("confidence", 0),
            }
            # Convert size_pct_equity to requested_size_usd for the bot (only for open actions)
            if decision.get("action") == "open" and decision.get("size_pct_equity") is not None:
                output["requested_size_usd"] = equity * decision["size_pct_equity"] / 100
            else:
                output["requested_size_usd"] = None

            # Step 4: Re-verify ACK before atomic write (guards against TOCTOU race)
            if prev_decision_id:
                try:
                    acked_id_now = Path(ack_path).read_text().strip() if Path(ack_path).exists() else ""
                except Exception:
                    acked_id_now = ""
                if acked_id_now != prev_decision_id:
                    log.info(f"⏳ ACK changed after check (was={prev_decision_id}, now={acked_id_now}), skipping write")
                    return False

            # Step 5: Atomic write — temp file then os.replace
            self.decision_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.decision_file, output)

            # Step 6: Clean up ACK file — safe because bot already consumed it (we verified ACK matches)
            try:
                if Path(ack_path).exists():
                    Path(ack_path).unlink()
            except Exception:
                pass

            # IPC-02: Track sent decision_id for result correlation
            self._last_sent_decision_id = decision_id

            log.info(f"📤 Decision written [{decision_id}]: {decision.get('action')} {decision.get('symbol', '')}"
                     + (f" (prev={prev_decision_id})" if prev_decision_id else ""))

            # Note: record_order() is called in execute_cycle after bot confirms success

            return True
        except Exception as e:
            log.error(f"Failed to write decision: {e}")
            return False

    def _request_shutdown(self):
        log.info("Shutdown requested...")
        self.running = False

    async def _emergency_halt(self, reason: str):
        """Write close_all decision, then set emergency_halt flag.

        HIGH-8 fix: Result file is kept as the source of truth. On retry,
        positions are re-read from the result file (bot updates it after
        each close), so we only send close_all for positions still shown as open.
        """
        if self.emergency_halt:
            log.debug(f"Emergency halt already triggered, ignoring: {reason}")
            return
        log.critical(f"🚨 Emergency halt: {reason}")
        try:
            # Read current positions from result file for the close_all
            # HIGH-8: On retry, result file will have updated positions (bot removes closed ones)
            current_positions = []
            if self.result_file.exists():
                result_data = safe_read_json(self.result_file)
                if result_data:
                    current_positions = result_data.get("positions", [])

            close_all = {
                "decision_id": str(uuid.uuid4())[:8],
                "prev_decision_id": None,  # Emergency halt bypasses normal IPC flow
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "close_all",
                "symbol": None,
                "direction": "long",
                "size_pct_equity": None,
                "leverage": 1,
                "stop_loss_pct": None,
                "reasoning": f"Emergency halt triggered: {reason}",
                "confidence": 1.0,
                "requested_size_usd": None,
                "positions": current_positions,
            }
            # Clean up ACK file so bot can process emergency halt immediately
            ack_path = str(self.decision_file) + ".ack"
            try:
                if Path(ack_path).exists():
                    Path(ack_path).unlink()
            except Exception:
                pass
            atomic_write(self.decision_file, close_all)
            log.info(f"📤 close_all decision written to bot ({len(current_positions)} positions) — polling for confirmation...")

            # Poll for bot confirmation, with retries using latest reported positions
            confirmed = False
            for attempt in range(30):  # 30 * 2s = 60s max
                await asyncio.sleep(2)
                if self.result_file.exists():
                    result = safe_read_json(self.result_file)
                    if result and result.get("processed_decision_id") == close_all["decision_id"]:
                        if result.get("success"):
                            confirmed = True
                            log.info("✅ Bot confirmed close_all execution")
                            # HIGH-8: Do NOT delete result file — keep as source of truth
                        else:
                            log.warning("⚠️ Bot reported close_all failed")
                        break
                # HIGH-8: Re-read positions from result file on each retry.
                # Bot updates it after closing each position, so we only
                # resend close_all for positions that are still shown as open.
                if (attempt + 1) % 3 == 0 and self.result_file.exists():
                    updated_result = safe_read_json(self.result_file)
                    if updated_result:
                        remaining = updated_result.get("positions", [])
                        if remaining and remaining != current_positions:
                            log.info(f"HIGH-8: Retrying close_all with {len(remaining)} remaining positions (was {len(current_positions)})")
                            current_positions = remaining
                            close_all["decision_id"] = str(uuid.uuid4())[:8]
                            close_all["positions"] = current_positions
                            atomic_write(self.decision_file, close_all)
            if confirmed:
                self.emergency_halt = True
            else:
                log.critical("🚨 close_all NOT confirmed by bot within 60s — NOT setting emergency_halt, will retry next cycle")
        except Exception as e:
            log.error(f"Failed to write close_all decision: {e}")
            # Don't set emergency_halt — will retry next cycle


def main():
    config_path = os.environ.get("AI_TRADER_CONFIG", "config.json")
    if not Path(config_path).exists():
        log.error(f"Config not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    config["_config_path"] = config_path
    trader = AITrader(config)
    asyncio.run(trader.run_forever())


if __name__ == "__main__":
    main()
