# Fix BUG-06: Handle Positions Tracked Without Price Data

**Problem:** After a successful open, the bot fetches current price. If the price is valid, the position is tracked. But on the next tick, `get_price_with_mark_fallback()` might fail. If both price sources return None, the position is "orphaned" — DSL can't compute ROE, can't trigger stop-loss.

## Changes

### 1. Post-Open Price Verification (`_execute_ai_open()` and `_process_signals()`)

After a position is opened and added to the tracker, the bot now verifies it can actually fetch price data:
- **3 retry attempts** with 1-second delays (max 3 seconds total)
- If all 3 fail: position removed from tracker, Telegram alert sent

### 2. Orphan Detection in Tick Loop (`_process_position_tick()`)

When a tracked position has no price data during a tick:
- **Logs a warning** with symbol and consecutive no-price count
- **Increments counter** per market ID
- After **3 consecutive no-price ticks**: Telegram alert ("manual check required")
- Counter resets on successful price fetch

## Files Modified
- `projects/autopilot-trader/executor/bot.py`
- `docs/fix-bug-06.md`
