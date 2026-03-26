"""Bot protocol — IPC channel for sending decisions, checking results, emergency halt."""
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ai-trader.ipc")

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import atomic_write, safe_read_json


class BotProtocol:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    async def check_result(self, decision_id: str) -> dict | None:
        """Check bot result file for correlation with sent decision.

        Returns result dict if found with matching processed_decision_id, else None.
        """
        if not self.ai_trader.result_file.exists():
            return None
        result = safe_read_json(self.ai_trader.result_file)
        if not result:
            return None
        # IPC-02: Correlate via processed_decision_id
        if result.get("processed_decision_id") != decision_id:
            log.debug(f"Result file exists but processed_decision_id={result.get('processed_decision_id')} "
                       f"doesn't match sent {decision_id}")
            return None
        return result

    async def send_decision(self, decision: dict, equity: float = 1000) -> bool:
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
            ack_path = str(self.ai_trader.decision_file) + ".ack"
            prev_decision_id: str | None = None

            # Step 1-2: Check if current decision is still being processed
            if self.ai_trader.decision_file.exists():
                current = safe_read_json(self.ai_trader.decision_file)
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
                                if age_seconds > self.ai_trader._ack_timeout_seconds:
                                    log.warning(
                                        f"⚠️ Decision {current_id} is stale (age={age_seconds:.0f}s > "
                                        f"timeout={self.ai_trader._ack_timeout_seconds}s), forcing overwrite"
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
            self.ai_trader.decision_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.ai_trader.decision_file, output)

            # Step 6: Clean up ACK file — safe because bot already consumed it (we verified ACK matches)
            try:
                if Path(ack_path).exists():
                    Path(ack_path).unlink()
            except Exception:
                pass

            # IPC-02: Track sent decision_id for result correlation
            self.ai_trader._last_sent_decision_id = decision_id

            log.info(f"📤 Decision written [{decision_id}]: {decision.get('action')} {decision.get('symbol', '')}"
                     + (f" (prev={prev_decision_id})" if prev_decision_id else ""))

            # Note: record_order() is called in cycle_runner after bot confirms success

            return True
        except Exception as e:
            log.error(f"Failed to write decision: {e}")
            return False

    async def emergency_halt(self, reason: str):
        """Write close_all decision, then set emergency_halt flag.

        HIGH-8 fix: Result file is kept as the source of truth. On retry,
        positions are re-read from the result file (bot updates it after
        each close), so we only send close_all for positions still shown as open.
        """
        if self.ai_trader.emergency_halt:
            log.debug(f"Emergency halt already triggered, ignoring: {reason}")
            return
        log.critical(f"🚨 Emergency halt: {reason}")
        try:
            # Read current positions from result file for the close_all
            # HIGH-8: On retry, result file will have updated positions (bot removes closed ones)
            current_positions = []
            if self.ai_trader.result_file.exists():
                result_data = safe_read_json(self.ai_trader.result_file)
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
                "stop_loss_pct": None,
                "reasoning": f"Emergency halt triggered: {reason}",
                "confidence": 1.0,
                "requested_size_usd": None,
                "positions": current_positions,  # Informational only — bot closes ALL from result file
            }
            # Clean up ACK file so bot can process emergency halt immediately
            ack_path = str(self.ai_trader.decision_file) + ".ack"
            try:
                if Path(ack_path).exists():
                    Path(ack_path).unlink()
            except Exception:
                pass
            atomic_write(self.ai_trader.decision_file, close_all)
            log.info(f"📤 close_all decision written to bot ({len(current_positions)} positions) — polling for confirmation...")

            # Poll for bot confirmation, with retries using latest reported positions
            confirmed = False
            for attempt in range(30):  # 30 * 2s = 60s max
                await asyncio.sleep(2)
                if self.ai_trader.result_file.exists():
                    result = safe_read_json(self.ai_trader.result_file)
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
                if (attempt + 1) % 3 == 0 and self.ai_trader.result_file.exists():
                    updated_result = safe_read_json(self.ai_trader.result_file)
                    if updated_result:
                        remaining = updated_result.get("positions", [])
                        if remaining and remaining != current_positions:
                            log.info(f"HIGH-8: Retrying close_all with {len(remaining)} remaining positions (was {len(current_positions)})")
                            current_positions = remaining
                            close_all["decision_id"] = str(uuid.uuid4())[:8]
                            close_all["positions"] = current_positions
                            atomic_write(self.ai_trader.decision_file, close_all)
            if confirmed:
                self.ai_trader.emergency_halt = True
            else:
                log.critical("🚨 close_all NOT confirmed by bot within 60s — NOT setting emergency_halt, will retry next cycle")
        except Exception as e:
            log.error(f"Failed to write close_all decision: {e}")
            # Don't set emergency_halt — will retry next cycle
