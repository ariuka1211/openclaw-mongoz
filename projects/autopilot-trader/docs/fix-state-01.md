# Fix STATE-01: Persist Position + DSL State Across Restarts

## Problem

The bot's `_save_state()` saved cooldown timers and attempt counters but NOT position state or DSL state. On restart:

- Positions were re-detected from the exchange API (correct)
- But DSL state started from scratch (wrong): `high_water_roe=0`, `current_tier=None`, `breach_count=0`, `locked_floor_roe=None`

A position at 20% ROE with tier 3 DSL protection would lose ALL protection on restart — the DSL would start tracking from 0% ROE as if the position was just opened.

## Solution

Extended the state persistence system to include position + DSL state:

### Changes to `bot.py`

1. **`_serialize_dsl_state(dsl)`** — New helper method. Serializes a DSLState object to a JSON-compatible dict. Converts datetime fields to ISO format strings and tier objects to their trigger_pct for later lookup.

2. **`_save_state()`** — Extended to serialize all tracked positions including:
   - Position basics: `market_id`, `symbol`, `side`, `entry_price`, `size`, `leverage`, `sl_pct`
   - Legacy trailing state: `high_water_mark`, `trailing_active`, `trailing_sl_level`
   - DSL state: all DSLState fields via `_serialize_dsl_state()`

3. **`_load_state()`** — Extended to load saved positions into `self._saved_positions` (a dict keyed by market_id string). The actual restoration happens later via reconciliation.

4. **`_restore_dsl_state(dsl_data, pos)`** — New method. Restores saved DSL state fields onto a live TrackedPosition's DSLState object. Looks up the DSLTier object from the tracker's DSL config by trigger_pct. Handles datetime deserialization from ISO format strings.

5. **`_reconcile_positions(live_positions)`** — New method. Called once per tick (no-op after first successful reconciliation). Compares saved positions with live exchange positions:
   - **Both exist**: Restore DSL state from saved data onto the live position
   - **Saved but not on exchange**: Log and drop (position was closed)
   - **Exchange but not saved**: Keep with fresh DSL state (new position)

6. **`_tick()`** — Added call to `_reconcile_positions(live_positions)` after position detection, before signal processing.

### State File Format

The `state/bot_state.json` file now includes a `positions` object:

```json
{
  "last_ai_decision_ts": "...",
  "last_signal_timestamp": "...",
  "recently_closed": {...},
  "ai_close_cooldown": {...},
  "close_attempts": {...},
  "close_attempt_cooldown": {...},
  "dsl_close_attempts": {...},
  "dsl_close_attempt_cooldown": {...},
  "positions": {
    "42": {
      "market_id": 42,
      "symbol": "BTC-USD",
      "side": "long",
      "entry_price": 65000.0,
      "size": 0.1,
      "leverage": 10.0,
      "sl_pct": null,
      "high_water_mark": 67000.0,
      "trailing_active": false,
      "trailing_sl_level": null,
      "dsl": {
        "side": "long",
        "entry_price": 65000.0,
        "leverage": 10.0,
        "high_water_roe": 23.0,
        "high_water_price": 67000.0,
        "high_water_time": "2026-03-23T21:30:00+00:00",
        "current_tier_trigger": 20,
        "breach_count": 0,
        "locked_floor_roe": 19.0,
        "stagnation_active": false,
        "stagnation_started": null
      }
    }
  }
}
```

## Edge Cases Handled

- **Empty state file**: `_saved_positions` stays None, reconciliation is a no-op
- **Partial state**: Missing DSL data is skipped gracefully (None check)
- **Position on exchange but not saved**: Keeps fresh DSL state (new position opened before restart)
- **Position in saved state but not on exchange**: Logged and dropped (position was closed)
- **API failure (EDGE-03)**: `live_positions is None` → reconciliation skipped, saved state preserved for next tick
- **Multiple ticks**: `_saved_positions` is set to None after first reconciliation, preventing repeated restoration

## Testing

To test manually:
1. Start bot, open a position, wait for DSL tier to activate
2. Check `state/bot_state.json` — positions should appear with DSL state
3. Kill and restart bot
4. Verify log shows `🔄 Restored DSL state for SYMBOL: HW_ROE=+X.X%, Tier=Y, Floor=Z, Breaches=N`
5. Verify DSL protection is active (not reset to zero)
