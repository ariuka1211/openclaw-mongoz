# EDGE-03: Don't Clear Positions on API/Network Failure

## Problem

When `api.get_positions()` fails (network timeout, proxy error, etc.), it returned an empty list `[]`. The bot interpreted this as "no positions" and wiped the entire tracker. On the next successful sync, positions reappeared but with **fresh DSL state** — all trailing stop loss protection, high water marks, and tier locks were lost.

This is a critical data-loss path: a transient network blip could cause the bot to drop all position state, then re-track positions with no memory of where the trailing stop was.

## Root Cause

`LighterAPI.get_positions()` caught all exceptions and returned `[]`:

```python
except Exception as e:
    logging.error(f"Failed to fetch positions: {e}")
    return []  # ← indistinguishable from "no positions"
```

The caller in `_tick()` used the result directly:

```python
live_positions = await self.api.get_positions()
live_mids = {p["market_id"] for p in live_positions}
# Then: if position not in live_mids → remove from tracker
```

## Fix

### 1. `get_positions()` returns `None` on failure

Changed return type to `list[dict] | None`. On API/network error, returns `None` instead of `[]`. This creates a clear distinction:
- `[]` = API succeeded, no open positions (correct behavior)
- `None` = API failed, position state unknown (preserve tracker)

### 2. `_tick()` skips position sync on failure

When `get_positions()` returns `None`:
- **Don't** detect new positions
- **Don't** detect closed positions (the "position closed on exchange" logic)
- **Don't** update mark prices
- **Do** run DSL evaluation on existing positions (using last known prices)
- **Do** run signal processing and order execution

### 3. Consecutive failure counter with alerts

- First failure: `logging.warning` — expected transient issues
- After 3 consecutive failures: Telegram alert + `logging.error`
- On recovery: `logging.info` + reset counter to 0

### 4. State preservation

The tracker's existing positions, DSL states, high water marks, and tier locks are completely untouched during API failures. When the API recovers, normal sync resumes with full state intact.

## Files Changed

- `projects/autopilot-trader/executor/bot.py`
  - `LighterAPI.get_positions()`: return `None` instead of `[]` on exception
  - `LighterCopilot.__init__()`: added `_position_sync_failures` counter and threshold
  - `LighterCopilot._tick()`: handle `None` by skipping position sync loops
- `docs/fix-edge-03.md`: this summary
