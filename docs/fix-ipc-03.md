# IPC-03: Rename `size_usd` for clarity in IPC messages

## Problem
The `size_usd` field meant different things in AI decision vs bot result files:
- **Decision file** (`ai-decision.json`): `size_usd` = requested trade size (what the AI wants to open)
- **Result file** (`ai-result.json`): `size_usd` = actual position notional value (size × entry_price)

This ambiguity made maintenance confusing.

## Changes

### ai_trader.py — `_send_to_bot()`
- `output["size_usd"]` → `output["requested_size_usd"]`
- This is the field the AI trader writes to tell the bot how much to open

### bot.py — `_validate_ai_decision()`
- `decision.get("size_usd", 0)` → `decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)`
- Backward compat: falls back to legacy field name

### bot.py — `_execute_ai_open()`
- Same change as `_validate_ai_decision()` with fallback

### bot.py — `_write_ai_result()`
- `"size_usd": pos.size * pos.entry_price` → `"position_size_usd": pos.size * pos.entry_price`
- This is the actual position notional value written back to the AI trader

### Backward Compatibility
All reads check the new field name first, then fall back to the old name. This prevents breakage during transition when old decision files might still be in the queue.

## Files Changed
- `projects/autopilot-trader/signals/ai-trader/ai_trader.py`
- `projects/autopilot-trader/executor/bot.py`
