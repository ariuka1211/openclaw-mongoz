"""
AI decision processing extracted from SignalProcessor.

Reads ai-decision.json, validates, and dispatches to executor functions.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json
from core.executor import execute_ai_open, execute_ai_close, execute_ai_close_all
from core.result_writer import write_ai_result


def validate_ai_decision(decision: dict) -> str | None:
    """Validate AI decision fields. Returns error string if invalid, None if OK."""
    action = decision.get("action")
    if action not in ("open", "close", "close_all", "hold"):
        return f"Invalid action: {action!r}"

    symbol = decision.get("symbol")
    if action in ("open", "close"):
        if not symbol or not isinstance(symbol, str) or not symbol.strip():
            return f"Missing or invalid symbol: {symbol!r}"

    if action == "open":
        # IPC-03: use requested_size_usd (new) with fallback to size_usd (legacy)
        size_usd = decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)
        if not isinstance(size_usd, (int, float)) or size_usd <= 0:
            return f"Invalid size_usd: {size_usd!r}"
        direction = decision.get("direction")
        if direction not in ("long", "short"):
            return f"Invalid direction: {direction!r}"
        confidence = decision.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                return f"Invalid confidence: {confidence!r} (expected 0.0-1.0)"

    if action == "close":
        confidence = decision.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                return f"Invalid confidence: {confidence!r} (expected 0.0-1.0)"

    return None


async def process_ai_decision(bot, cfg, api, tracker, alerter):
    """Read AI decision file and execute if valid."""
    path = Path(bot._ai_decision_file)
    if not path.exists():
        return

    decision = safe_read_json(path)
    if decision is None:
        # MED-5: File exists but read returned None (atomic write in progress).
        # Retry once after 0.5s to avoid dropping decisions on first tick post-restart.
        if path.exists():
            await asyncio.sleep(0.5)
            decision = safe_read_json(path)
        if decision is None:
            return

    # Only process new decisions
    ts = decision.get("timestamp", "")
    if ts == bot._last_ai_decision_ts:
        return
    bot._last_ai_decision_ts = ts
    bot._signal_processed_this_tick = True

    # HIGH-7: Reject stale AI decisions (>10 minutes old)
    try:
        decision_time = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
    except (ValueError, AttributeError):
        decision_time = None
    if decision_time:
        age_seconds = (datetime.now(timezone.utc) - decision_time).total_seconds()
        if age_seconds > 600:
            logging.warning(f"⚠️ AI decision rejected: stale (age={age_seconds:.0f}s, max=600s)")
            write_ai_result(bot, cfg, api, tracker, decision, success=False)
            return

    # Validate decision
    validation_error = validate_ai_decision(decision)
    if validation_error:
        logging.warning(f"⚠️ AI decision rejected: {validation_error}")
        write_ai_result(bot, cfg, api, tracker, decision, success=False)
        return

    action = decision.get("action")
    if action not in ("open", "close", "close_all"):
        return  # hold or unknown — do nothing

    # BUG 2: Check prev_decision_id for gap detection
    prev_id = decision.get("prev_decision_id")
    if prev_id:
        ack_path = str(path) + ".ack"
        try:
            acked_id = Path(ack_path).read_text().strip() if Path(ack_path).exists() else ""
        except Exception:
            acked_id = ""
        if acked_id and prev_id != acked_id:
            logging.warning(
                f"⚠️ AI decision prev_decision_id={prev_id} doesn't match last ACKed={acked_id} "
                f"— potential decision gap, processing anyway"
            )

    # BUG 3: Check if we already processed this decision (duplicate execution guard)
    decision_id = decision.get("decision_id", "")
    result_path = Path(bot._ai_result_file)
    if result_path.exists() and decision_id:
        existing_result = safe_read_json(result_path)
        if existing_result and existing_result.get("processed_decision_id") == decision_id:
            logging.info(f"⏩ Decision {decision_id} already processed (result file exists), skipping execution")
            return

    # Execute inside try/except — ACK + result are written AFTER execution
    # completes (success or failure), NOT if an uncaught exception occurs.
    try:
        if action == "close_all":
            success = await execute_ai_close_all(bot, cfg, api, tracker, alerter, decision)
        elif action == "open":
            success = await execute_ai_open(bot, cfg, api, tracker, alerter, decision)
        elif action == "close":
            success = await execute_ai_close(bot, cfg, api, tracker, alerter, decision)
        else:
            success = True

        # HIGH-3: Write ACK BEFORE result — ACK = "I consumed this decision."
        # If bot crashes between ACK and result write, AI trader won't re-send.
        # Result is supplementary (positions context). ACK is essential.
        ack_path = str(path) + ".ack"
        with open(ack_path, "w") as f:
            f.write(decision.get("decision_id", ""))
        write_ai_result(bot, cfg, api, tracker, decision, success=success)
        # BUG 3: Save state immediately after ACK so _last_ai_decision_ts persists before ACK
        bot._save_state()
    except Exception as e:
        logging.error(f"❌ AI decision execution crashed — NOT writing ACK: {e}", exc_info=True)
        # Do NOT write result or ACK — the AI trader will re-deliver the decision
