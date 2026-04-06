# Phase 2: Short Grid Direction

## Problem
Bot currently only grids long. In strong downtrends it "catches knives" — fills buys all the way down with no short exposure. When AI says "bearish, pause" the bot does nothing instead of profiting from the drop.

## Solution
Instead of pausing in bearish conditions, **flip the grid short**. Same mechanics, opposite direction:
- **Long grid (current):** Buy below, sell above → profit in ranges/up
- **Short grid (new):** Sell below, buy above → profit in ranges/down

Lighter is a perp exchange — `is_ask=True` on a sell order opens a short position (no existing long to close). Positions net (can't hold long + short simultaneously). Trailing stop works both ways.

## Architecture

### What changes: direction field propagates through
```
analyst.py → returns {"direction": "long"|"short"|"pause", ...}
     ↓
main.py → check direction before deploy
     ↓
grid.py → deploy() respects direction, deploy_short_grid()
     ↓
check_fills() → reversed PnL matching
     ↓
state → stores grid_direction
```

### What stays the same:
- Trailing stop loss ✅
- Volume spike cooldown ✅
- OI divergence ✅
- Funding rate awareness (even MORE useful for shorts) ✅
- Time-awareness ✅
- Compounding ✅
- Rolling grid (uses short levels) ✅
- Config format ✅
- Telegram alerts ✅

---

## File Changes

### 1. `analyst.py` — Add `direction` to LLM output (~60 lines changed)

**Current output:**
```json
{
  "buy_levels": [...],
  "sell_levels": [...],
  "pause": false,
  ...
}
```

**New output:**
```json
{
  "direction": "long"|"short"|"pause",
  "buy_levels": [...],  // levels ABOVE price for shorts, BELOW for longs
  "sell_levels": [...], // levels BELOW price for shorts, ABOVE for longs
  "range_low": ...,
  "range_high": ...,
  "pause": false,       // keep for backward compat
  ...
}
```

**Changes:**
- `build_prompt()`: Add instruction about grid direction. Explain that instead of "pause" in downtrends, the Analyst should set `"direction": "short"` with appropriate levels. For short grids: sell levels are BELOW current price (to open shorts), buy levels are ABOVE (to close them at profit).
- `run_analyst()`: Add `direction` to output validation and default handling. Map pause → `"direction": "pause"`.
- Prompt section for short grids: explain the concept to the LLM clearly so it generates correct level ordering.

### 2. `grid.py` — Short grid deployment + fill handling (~200 lines changed/added)

**New: `deploy_short_grid()` method**
Mirror of `deploy()` but:
- Places sell orders BELOW current price (open shorts)
- Places buy orders ABOVE current price (close shorts)
- Validates capital with short-specific logic
- State stores `"grid_direction": "short"`

**Modified: `deploy()`**
- Accept optional `direction: str` parameter (default `"long"`)
- If direction == `"short"` → route to `deploy_short_grid()`

**Modified: `check_fills()`**
- If `grid_direction == "short"`: reverse the fill logic:
  - Sell filled below → record as "short opened" pending
  - Buy filled above → match to pending short, compute PnL: `(sell_price - buy_price) * size`
- Otherwise same logic as before

**Modified: `_place_replacement()`**
- If short mode: sell filled → place replacement sell BELOW; buy filled → place replacement buy ABOVE
- Same retry logic, just mirrored levels

**New: `roll_short_grid()`**
- Mirror of `roll_grid()` for short direction
- Or: reuse existing `roll_grid()` with direction-aware level generation

**State additions:**
- `"grid_direction": "long"|"short"` (default "long")
- All other fields stay the same

### 3. `calculator.py` — No significant changes needed (~10 lines added)

The capital calculator already computes `size_per_level` as: `(equity * max_exposure * safety) / (num_levels * price)`. This works for shorts too because:
- Perp shorts use the same margin as longs
- Max exposure is total notional, not directional
- Size calculation is symmetric

**Changes:**
- Add `direction` parameter to `calculate_grid()` for logging/metadata
- Return `"direction"` in output dict

### 4. `main.py` — Direction-aware startup + run_loop (~80 lines changed/added)

**Modified: `startup()`**
- After `run_analyst()`, check `levels["direction"]`
- If `"long"` → normal deploy
- If `"short"` → pass direction to `gm.deploy(..., direction="short")`
- If `"pause"` → existing pause behavior

**Modified: `handle_resume()`**
- Read `grid_direction` from state
- Resume with same direction

**Modified: `check_trend()`**
- New logic: if bearish trend (>3% below EMA50), instead of returning False, return `"short"` to trigger short grid
- If neutral → return `"long"`
- If VERY bearish (>5% below EMA50 or extreme volume spike) → return `"pause"` (even shorts are risky with potential short squeezes)

**Modified: `run_loop()`**
- Pass direction to `deploy()` calls

### 5. `config.yml` — Optional additions (~5 lines added)

Add short-specific config if needed:
```yaml
short_grid:
  max_exposure_multiplier: 2.0  # More conservative for shorts
  max_levels: 6                  # Slightly fewer levels
```

---

## Execution Plan

**Subagent 1: analyst.py changes**
- Update prompt to explain direction awareness
- Add direction field to output + validation
- Test with `--test` mode

**Subagent 2: grid.py changes**
- Add `deploy_short_grid()` method
- Modify `check_fills()` to handle short mode
- Modify `_place_replacement()` for short mode
- Add `grid_direction` to state management
- Test one-sided roll in short mode

**Subagent 3: main.py + calculator.py + config**
- Direction-aware startup flow
- Modified trend check (returns "short" instead of False)
- Resume with direction awareness
- Calculator direction parameter

**Subagent 4: testing + docs**
- Test analyst outputs with short direction
- Test short grid deployment (dry-run / paper)
- Update cheatsheet.md with short grid reference

---

## Risk Considerations

1. **Short squeeze risk** — BTC can gap up 10%+ faster than it drops. The trailing stop (4%) and trend pause (>5% below EMA = pause) are critical guards.

2. **Funding rates** — When funding flips negative (shorts pay longs), short grids become expensive. The funding rate adjustment already handles this.

3. **Max exposure** — Consider lower max exposure for shorts (2.0x vs 3.0x) since short risk is theoretically unlimited.

4. **Direction transition** — If AI switches from long → short → long in consecutive days, we need to cleanly flatten position before new deploy. The existing `get_btc_balance()` check handles this.

## Rollback
If something goes wrong, revert the git commit and redeploy. State file can be deleted to force clean restart.
