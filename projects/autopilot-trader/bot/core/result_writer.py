"""
AI result file writing functions extracted from SignalProcessor.

Write execution results and refresh position context for the AI trader.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json


def write_ai_result(bot, cfg, api, tracker, decision: dict, success: bool):
    """Write execution result for the AI trader to read."""
    try:
        positions = []
        for mid, pos in tracker.positions.items():
            current_price = api.get_mark_price(mid) if api else None
            positions.append({
                "market_id": mid,
                "symbol": pos.symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "current_price": current_price if current_price and current_price > 0 else pos.entry_price,
                "size": pos.size,
                "leverage": pos.dsl_state.effective_leverage if pos.dsl_state else cfg.default_leverage,
                "position_size_usd": pos.size * pos.entry_price,
            })
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processed_decision_id": decision.get("decision_id"),
            "processed_timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_action": decision.get("action"),
            "decision_symbol": decision.get("symbol"),
            "success": success,
            "positions": positions,
        }
        # Atomic write (same pattern as ai-trader)
        tmp = str(bot._ai_result_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(result, f, indent=2)
        os.replace(tmp, str(bot._ai_result_file))
        # HIGH-10: Mark result as fresh — refresh should not overwrite until next tick
        bot._result_dirty = True
    except Exception as e:
        logging.warning(f"Failed to write AI result: {e}")


def refresh_position_context(bot, cfg, api, tracker):
    """MED-4: Write updated positions to result file between AI decisions.

    Preserves the last processed_decision_id so AI trader can still correlate,
    but updates positions to reflect DSL/SL/TP closes that happened since
    the last AI decision was processed.
    """
    # HIGH-10: Skip if a fresh AI result was just written — don't overwrite
    # the AI trader's result before it has a chance to read it.
    if bot._result_dirty:
        logging.debug("Refresh skipped: result is dirty (fresh AI result not yet consumed)")
        return
    try:
        existing = safe_read_json(Path(bot._ai_result_file))
        last_decision_id = existing.get("processed_decision_id") if existing else None

        positions = []
        for mid, pos in tracker.positions.items():
            current_price = api.get_mark_price(mid) if api else None
            positions.append({
                "market_id": mid,
                "symbol": pos.symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "current_price": current_price if current_price and current_price > 0 else pos.entry_price,
                "size": pos.size,
                "leverage": pos.dsl_state.effective_leverage if pos.dsl_state else cfg.default_leverage,
                "position_size_usd": pos.size * pos.entry_price,
            })
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processed_decision_id": last_decision_id,  # Keep last AI decision ID for correlation
            "processed_timestamp": existing.get("processed_timestamp") if existing else datetime.now(timezone.utc).isoformat(),
            "decision_action": existing.get("decision_action") if existing else "refresh",
            "decision_symbol": existing.get("decision_symbol") if existing else None,
            "success": existing.get("success", True) if existing else True,
            "positions": positions,
        }
        tmp = str(bot._ai_result_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(result, f, indent=2)
        os.replace(tmp, str(bot._ai_result_file))
    except Exception as e:
        logging.debug(f"Failed to refresh position context: {e}")
