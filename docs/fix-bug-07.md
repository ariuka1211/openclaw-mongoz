# BUG-07: Don't manage positions the bot didn't open

## Problem
Any position detected from the API was added to the tracker and managed by the bot (trailing TP/SL, DSL). If a user opened a position manually through the Lighter UI, the bot would start managing it and potentially close it.

## Solution
Added `bot_managed_market_ids: set[int]` to track which market_ids the bot opened. API-detected positions are only managed if:
1. `track_manual_positions: bool = True` in config (old behavior), OR
2. The market_id is in `bot_managed_market_ids` (bot opened it)

## Changes
- **config**: Added `track_manual_positions: bool = False` to `BotConfig`
- **init**: Added `self.bot_managed_market_ids: set[int] = set()`
- **_process_signals()**: Add market_id to set after successful open
- **_execute_ai_open()**: Add market_id to set after successful open
- **_tick()**: Filter API-detected positions — skip if not in managed set
- **All close sites** (5 locations): `discard(mid)` before `remove_position(mid)`
- **_save_state()**: Persist sorted list to disk
- **_load_state()**: Restore set from disk

## Verification
On restart, positions loaded from saved state should also be in `bot_managed_market_ids` (persisted together).

## Config
```yaml
track_manual_positions: false  # Default: only manage bot-opened positions
```
Set to `true` to manage all positions (old behavior).
