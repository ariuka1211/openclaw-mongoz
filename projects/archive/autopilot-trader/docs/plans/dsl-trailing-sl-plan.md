# DSL + Trailing SL Unification Plan (v2 — Re-verified)

**Created:** 2026-03-26
**Updated:** 2026-03-26 — re-verified against full codebase
**Updated:** 2026-03-26 18:38 MDT — code review issues documented, implementation started
**Updated:** 2026-03-26 18:51 MDT — COMPLETED ✅
**Status:** COMPLETE — branch `dsl-trailing-sl` pushed, ready for testing/deployment
**Scope:** Bot only — scanner and ai-decisions have zero references to DSL/trailing/SL/TP. No cross-service impact.

---

## Problem Statement

Three overlapping exit mechanisms exist in DSL mode, creating confusion and dead code:

1. **DSL** (tiered trailing stop on profits) — works well, but only protects the upside
2. **Trailing TP** (`trailing_tp_trigger_pct` / `trailing_tp_delta_pct`) — nearly dead code in DSL mode, unit mismatch (raw price % vs ROE%), redundant with DSL tiers
3. **Hard SL** (`hard_sl_pct: 1.25%`) — flat floor from entry, no adaptation to price movement

**Core gap:** No downside trailing. If price moves +2% then crashes to -0.5%, the only protection is the flat -1.25% hard SL.

---

## Design: Two-Specialist System

| Mechanism | Job | Trails |
|-----------|-----|--------|
| **DSL** | Protect & lock in profits | UP (tightens as profit grows via tiers) |
| **Trailing SL** | Limit losses, adapt to price recovery | UP from entry (ratchets with new highs) |
| **Hard SL** | Absolute worst-case floor | No (fixed at entry - X%) |

### Evaluation order (per tick, DSL mode)

```
1. DSL evaluate          → tier_lock / stagnation / hard_sl / None
2. Trailing SL evaluate  → trailing_sl / None
```

DSL has priority (tighter on profits). Trailing SL only fires if DSL didn't — catching pullbacks in the entry zone where DSL isn't active yet or has a wider floor.

### Per-position sl_pct (from AI)

The AI outputs `stop_loss_pct` per decision. Currently in DSL mode this value is **stored but never used for exits** — DSL's `hard_sl` check uses `cfg.hard_sl_pct` directly, bypassing `sl_pct`. This is a hidden bug.

**Fix:** The new trailing SL hard floor uses per-position `sl_pct` when set (AI override), falls back to `cfg.hard_sl_pct`. DSL's `hard_sl` check stays unchanged (config-based absolute floor, ROE-based). Two floors are intentional:
- DSL hard_sl: config-based, ROE calculation (e.g., -12.5% ROE at 10x leverage) — the "nuclear option"
- Trailing SL hard floor: per-position sl_pct or config fallback, raw price % — respects AI risk assessment

---

## Code Review Issues (18:38 MDT)

Found during pre-implementation review against actual codebase:

### Issue 1: step_pct=0.95% doesn't guarantee profit at first activation

**Math:** With trigger=0.5% and step=0.95%, the first trailing SL fires at:
- entry × 1.005 × 0.9905 = entry × 0.9955 → **-0.45% loss**
- Required step to break even: 0.4975% (step < trigger/(1+trigger))
- Required step to guarantee +0.1% profit: 0.398%

**Impact:** For small moves (+0.5%), trailing SL gives back more than the gain. Only on bigger runs (+2%+) does it lock meaningful profit. For positions that never reach +0.5%, the hard floor (-1.25%) is the only exit.

**Decision:** Acceptable by design. Trailing SL catches big pullbacks after significant runs. Small moves fall through to hard floor. Tightening step to ~0.4% would guarantee small profit at first activation but might trigger on normal noise. Keep 0.95% for now; John can adjust if too loose in practice.

### Issue 2: stagnation_minutes = 90 (not 60)

Config.yml had 60, plan says 90. Confirmed: changing to 90 as planned (loosened from original 60 to avoid premature exits).

### Issue 3: Bot.py logging locations

TP logging was only in the legacy `else` branch, not in the DSL branch. Added trailing SL logging to the DSL branch (3 locations: DSL mode startup, legacy mode startup, Telegram alert).

### Issue 4: config.example.yml stale

Still has `max_position_usd` (old field from position sizing refactor). Not fixing in this change — separate cleanup.

---

## Implementation Results ✅

**Completed:** 2026-03-26 18:51 MDT  
**Branch:** `dsl-trailing-sl` (fb6211c)  
**Tests:** 108/108 pass (including 45 DSL + 9 new trailing SL tests)  
**Files changed:** 13 (591 insertions, 362 deletions)

### What was built:

1. **`evaluate_trailing_sl()` function in `dsl.py`** — handles both long and short sides with hard floor check, activation logic, ratcheting, and trigger detection
2. **Position tracker overhaul** — removed trailing TP, added trailing SL evaluation in both DSL and legacy modes
3. **Execution engine** — removed trailing_take_profit handler, added trailing_sl exit handler with same DSL-style error handling
4. **State management** — migrated trailing_active → trailing_sl_activated with backward compatibility
5. **Configuration** — added trailing_sl_trigger_pct=0.5%, trailing_sl_step_pct=0.95%, stagnation_minutes=90
6. **Comprehensive test coverage** — all obsolete tests updated, new trailing SL tests added

### Verification completed:

- **Manual code review:** All old methods removed, new logic integrated
- **Import/syntax checks:** All modules pass basic Python validation  
- **Test execution:** 108/108 tests pass (config, models, DSL, position tracker)
- **Subagent verification:** Both implementation subagents completed successfully

### Next steps for John:

1. **Merge branch:** Review PR and merge `dsl-trailing-sl` → `main`
2. **Deploy bot:** Restart the trading bot to pick up new config fields
3. **Monitor behavior:** Watch trailing SL activation and exits in practice
4. **Tune if needed:** Adjust trigger_pct or step_pct based on real performance

**Ready for production deployment.**

---

## What Gets Removed

### Trailing TP (dead code in DSL mode)

| Scenario | Trailing TP behavior | DSL equivalent | Verdict |
|----------|---------------------|----------------|---------|
| HW at 8% ROE, pullback | Fires at 7.3% ROE | Floor at 2% ROE (tier 1) | TP tighter, but narrow window |
| HW at 15% ROE, pullback | Fires at 9% ROE | Floor at 3% ROE (tier 2) | DSL catches first |
| HW at 25% ROE, pullback | Fires at 19% ROE | Floor at 5% ROE (tier 3) | DSL catches first |

Trailing TP is dead code for any position that runs past tier 1. Remove entirely.

### Dead code identified

These are ONLY used in the legacy action section (after DSL mode returns), never in DSL mode:
- `compute_tp_price()` — trailing TP price calculation
- `trailing_active` field — trailing TP activation flag  
- All `trailing_tp_trigger_pct` / `trailing_tp_delta_pct` references
- `compute_sl_price()` — legacy SL price calculation (calls `_get_sl_pct()`)
- `_get_sl_pct()` — resolves per-position vs config SL %
- `trailing_activated` tuple action — informational alert for TP activation
- `trailing_take_profit` action — TP execution

---

## Detailed Changes by File

### 1. `bot/config.py`

**Remove fields:**
```python
trailing_tp_trigger_pct: float = 3.0   # DELETE
trailing_tp_delta_pct: float = 1.0     # DELETE
```

**Add fields:**
```python
trailing_sl_trigger_pct: float = 0.5   # Start trailing after +0.5% price move
trailing_sl_step_pct: float = 0.95     # Trail by 0.95% from new high
```

**Change:**
```python
stagnation_minutes: int = 90           # was 60
```

**Update `from_yaml()`** — line 148, the float coercion list:
```python
# Remove: "trailing_tp_trigger_pct", "trailing_tp_delta_pct"
# Add: "trailing_sl_trigger_pct", "trailing_sl_step_pct"
```

**Update `validate()`** — line 80:
```python
# Remove: ("trailing_tp_trigger_pct", "trailing_tp_delta_pct") validation
# Add: trailing_sl_trigger_pct (>= 0), trailing_sl_step_pct (> 0, <= 5)
# Change: stagnation_minutes minimum from 0 to 10 (loosened from 60 but still reasonable)
```

### 2. `bot/config.yml`

```yaml
# Remove:
trailing_tp_trigger_pct: 1.0
trailing_tp_delta_pct: 1.0

# Add:
trailing_sl_trigger_pct: 0.5    # Start trailing after +0.5% price move
trailing_sl_step_pct: 0.95      # Trail by 0.95% from new high

# Change:
stagnation_minutes: 90          # was 60
```

### 3. `bot/config.example.yml`

Mirror the same changes as config.yml.

### 4. `bot/core/models.py`

**Remove:**
```python
trailing_active: bool = False          # DELETE — trailing TP concept gone
```

**Add:**
```python
trailing_sl_activated: bool = False    # Has trailing SL been triggered (price moved past trigger)
```

**Keep (unchanged):**
```python
trailing_sl_level: float | None = None  # Trailing SL price level (ratchets)
sl_pct: float | None = None             # Per-position SL % from AI (used for hard floor)
```

### 5. `bot/dsl.py`

**No changes to `evaluate_dsl()`** — DSL stays focused on profit protection.

**Add new function:**
```python
def evaluate_trailing_sl(
    side: str,
    entry_price: float,
    price: float,
    high_water_price: float,
    trailing_sl_level: float | None,
    trailing_sl_activated: bool,
    trigger_pct: float,
    step_pct: float,
    hard_floor_pct: float,
) -> tuple[str | None, float | None, bool]:
    """
    Evaluate trailing stop loss for one tick.
    
    Returns (action, new_trailing_sl_level, new_trailing_sl_activated).
    action is "trailing_sl" or None.
    """
```

**Logic for LONG:**
1. Hard floor check: if price <= entry * (1 - hard_floor_pct/100) → `("trailing_sl", None, False)`
2. Activation check: if not activated and price >= entry * (1 + trigger_pct/100) → activate
3. Ratchet: if activated and high_water_price > 0:
   - candidate = high_water_price * (1 - step_pct/100)
   - new_level = max(trailing_sl_level or 0, candidate)  # never goes down
4. Trigger check: if activated and trailing_sl_level is not None and price <= trailing_sl_level → `("trailing_sl", new_level, True)`
5. Otherwise: `(None, new_level, trailing_sl_activated)`

**Logic for SHORT (mirror):**
1. Hard floor: if price >= entry * (1 + hard_floor_pct/100) → `("trailing_sl", None, False)`
2. Activation: if not activated and price <= entry * (1 - trigger_pct/100) → activate
3. Ratchet: candidate = high_water_price * (1 + step_pct/100), new_level = min(...)
4. Trigger: if activated and price >= trailing_sl_level → `("trailing_sl", new_level, True)`

### 6. `bot/core/position_tracker.py`

**Remove methods:**
- `compute_tp_price()` — trailing TP is gone

**Remove `_get_sl_pct()` method** — no longer needed (trailing SL uses config trigger/step, hard floor uses sl_pct or config directly)

**Keep `compute_sl_price()`** — rename to `_compute_hard_floor_price()` for clarity, used only in legacy mode:
```python
def _compute_hard_floor_price(self, pos: TrackedPosition) -> float:
    """Return hard floor price (per-position sl_pct or config default)."""
    sl = pos.sl_pct if pos.sl_pct is not None else self.cfg.hard_sl_pct
    if pos.side == "long":
        return pos.entry_price * (1 - sl / 100)
    else:
        return pos.entry_price * (1 + sl / 100)
```

**Rewrite `update_price()` DSL mode branch:**

Current flow (after DSL returns None):
```
→ sync high_water_mark from DSL
→ check trailing TP activation
→ check trailing TP trigger
→ return None
```

New flow (after DSL returns None):
```
→ sync high_water_mark from DSL
→ evaluate trailing SL (via dsl.evaluate_trailing_sl)
→ if trailing_sl → return "trailing_sl"
→ update trailing_sl_level and trailing_sl_activated on pos
→ return None
```

```python
# After DSL evaluation returns None:
# Sync high_water_mark for trailing SL
if pos.dsl_state and pos.dsl_state.high_water_price > 0:
    pos.high_water_mark = pos.dsl_state.high_water_price

# Evaluate trailing SL
sl_floor_pct = pos.sl_pct if pos.sl_pct is not None else self.cfg.hard_sl_pct
action, new_level, new_activated = evaluate_trailing_sl(
    side=pos.side,
    entry_price=pos.entry_price,
    price=price,
    high_water_price=pos.high_water_price if pos.dsl_state else pos.high_water_mark,
    trailing_sl_level=pos.trailing_sl_level,
    trailing_sl_activated=pos.trailing_sl_activated,
    trigger_pct=self.cfg.trailing_sl_trigger_pct,
    step_pct=self.cfg.trailing_sl_step_pct,
    hard_floor_pct=sl_floor_pct,
)
pos.trailing_sl_level = new_level
pos.trailing_sl_activated = new_activated
if action:
    logging.info(f"🔻 {pos.symbol} trailing SL triggered | Price: ${price:,.2f} | SL: ${new_level:,.2f}")
    return "trailing_sl"
return None
```

**Rewrite `update_price()` legacy mode branch:**

Remove trailing TP logic entirely. Keep only:
- High water mark tracking
- Trailing SL evaluation (same `evaluate_trailing_sl()` call)
- Return "trailing_sl" or None

**Update `add_position()` logging:**
```python
# Change:
sl_source = f"AI={sl_pct}%" if sl_pct is not None else f"config={self.cfg.hard_sl_pct}%"
# To:
sl_source = f"AI={sl_pct}%" if sl_pct is not None else f"config={self.cfg.hard_sl_pct}%"
# (same, but now sl_pct actually matters for trailing SL hard floor)
```

### 7. `bot/core/execution_engine.py`

**Remove informational alert handlers:**
- `trailing_activated` — no longer emitted (was trailing TP activation alert)
- `dsl_tier_lock` — KEEP (still emitted by position_tracker)
- `dsl_stagnation_timer` — KEEP (still emitted by position_tracker)

**Remove exit action handler:**
- `trailing_take_profit` — DELETE entirely (used `execute_tp()`, quota skip logic)

**Add `trailing_sl` action handler** — insert before the "Legacy actions" section:
```python
if action == "trailing_sl":
    pnl = self._pnl_info(pos, price)
    msg = (
        f"🔻 *TRAILING SL EXIT*\n"
        f"Symbol: {pos.symbol}\n"
        f"Side: {pos.side}\n"
        f"Trigger: ${price:,.2f}\n"
        f"Entry: ${pos.entry_price:,.2f}\n"
        f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)"
    )
    logging.info(msg)
    await self.alerter.send(msg)
    
    # Same SL execution flow as DSL hard_sl / legacy stop_loss
    # Check close attempt cooldown
    cooldown_until = self.bot._dsl_close_attempt_cooldown.get(pos.symbol)
    if cooldown_until and time.monotonic() < cooldown_until:
        remaining = int(cooldown_until - time.monotonic())
        logging.info(f"🧊 Trailing SL close: {pos.symbol} in cooldown ({remaining}s) — skipping")
        return
    
    # Cancel stale SL order
    if pos.active_sl_order_id:
        await self.api._cancel_order(mid, int(pos.active_sl_order_id))
        pos.active_sl_order_id = None
    
    sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
    if sl_success and sl_coi:
        pos.active_sl_order_id = sl_coi
    if sl_success:
        position_closed = await self.bot.signal_processor._verify_position_closed(mid, pos.symbol)
        if not position_closed:
            attempts = self.bot._dsl_close_attempts.get(pos.symbol, 0) + 1
            self.bot._dsl_close_attempts[pos.symbol] = attempts
            if attempts >= self.bot._max_close_attempts:
                self.bot._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + self.bot._close_cooldown_seconds
                self.bot.signal_processor._log_outcome(pos, price, "trailing_sl", estimated=True)
                await self.alerter.send(
                    f"🚨 *TRAILING SL CLOSE FAILED ×{attempts}*\n"
                    f"{pos.side.upper()} {pos.symbol}\n"
                    f"MANUAL INTERVENTION REQUIRED."
                )
            return
        self.bot._dsl_close_attempts.pop(pos.symbol, None)
        self.bot._dsl_close_attempt_cooldown.pop(pos.symbol, None)
    else:
        attempts = self.bot._dsl_close_attempts.get(pos.symbol, 0) + 1
        self.bot._dsl_close_attempts[pos.symbol] = attempts
        delay_idx = min(attempts - 1, len(self.bot._sl_retry_delays) - 1)
        retry_delay = self.bot._sl_retry_delays[delay_idx]
        self.bot._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay
        logging.warning(f"⚠️ {pos.symbol}: trailing SL order rejected (attempt {attempts}, retry in {retry_delay}s)")
        return
    
    fill_price = await self.bot.signal_processor._get_fill_price(mid, sl_coi)
    exit_price = fill_price if fill_price else price
    self.bot.signal_processor._log_outcome(pos, exit_price, "trailing_sl")
    self.bot._recently_closed[mid] = time.monotonic() + 300
    pos.active_sl_order_id = None
    self.bot.bot_managed_market_ids.discard(mid)
    self.tracker.remove_position(mid)
    self.bot._opened_signals.discard(mid)
    # Post-close alert
    if is_long:
        pnl_usd = pos.size * (exit_price - pos.entry_price)
    else:
        pnl_usd = pos.size * (pos.entry_price - exit_price)
    await self.alerter.send(
        f"✅ *TRAILING SL → CLOSED*\n"
        f"{pos.side.upper()} {pos.symbol}\n"
        f"Entry: ${pos.entry_price:,.2f}\n"
        f"Exit: ${exit_price:,.2f}\n"
        f"PnL: ${pnl_usd:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)"
    )
    return
```

**Simplify legacy actions section:**
- Remove `trailing_take_profit` branch entirely (including quota skip logic)
- Rename `stop_loss` handling to generic SL handler (same code, different action name)
- Both `trailing_sl` (new) and legacy `stop_loss` use `execute_sl()` — merge into shared helper if desired, or keep separate for clarity

**Remove from "Legacy actions" section:**
```python
tp_price = self.tracker.compute_tp_price(pos)  # DELETE
```

### 8. `bot/core/state_manager.py`

**Save (line 85):**
```python
# Remove:
"trailing_active": pos.trailing_active,
# Add:
"trailing_sl_activated": pos.trailing_sl_activated,
```

**Load (line 398):**
```python
# Remove:
if saved_pos.get("trailing_active"):
    pos.trailing_active = True
# Add:
if saved_pos.get("trailing_sl_activated"):
    pos.trailing_sl_activated = True
# Backward compat: migrate old field
elif saved_pos.get("trailing_active"):
    pos.trailing_sl_activated = True
```

### 9. `bot/bot.py`

**Update startup logging (lines 177-178):**
```python
# Remove:
logging.info(f"   TP trigger: +{self.cfg.trailing_tp_trigger_pct}%")
logging.info(f"   TP delta: {self.cfg.trailing_tp_delta_pct}%")
# Add:
logging.info(f"   Trailing SL: trigger +{self.cfg.trailing_sl_trigger_pct}%, step {self.cfg.trailing_sl_step_pct}%")
```

**Update DSL mode logging (line ~170):**
```python
# Add after stagnation logging:
logging.info(f"   Trailing SL: trigger +{self.cfg.trailing_sl_trigger_pct}%, step {self.cfg.trailing_sl_step_pct}%")
```

**Update Telegram startup alert (line 238):**
```python
# Change:
f"TP: trail {self.cfg.trailing_tp_delta_pct}% after +{self.cfg.trailing_tp_trigger_pct}%\n"
# To:
f"Trail SL: +{self.cfg.trailing_sl_trigger_pct}% trigger, {self.cfg.trailing_sl_step_pct}% step\n"
```

### 10. `bot/core/executor.py`

**No changes needed** — `sl_pct` from AI still passed to `add_position()`. It now actually matters for trailing SL hard floor.

### 11. `bot/core/position_sizer.py`

**No changes needed** — uses `hard_sl_pct` for sizing math, independent of exit logic.

---

## Test Changes

### `tests/conftest.py`
- Remove `trailing_tp_trigger_pct=3.0` and `trailing_tp_delta_pct=1.0`
- Add `trailing_sl_trigger_pct=0.5` and `trailing_sl_step_pct=0.95`
- Change `stagnation_minutes=60` → `90`

### `tests/test_config.py`
- Remove TP field tests (`test_default_trailing_tp_trigger_pct`, `test_default_trailing_tp_delta_pct`, `test_validate_trailing_tp_trigger_pct_negative`)
- Remove TP from YAML test strings (lines 30-31, 43-44)
- Add trailing SL field defaults and validation tests
- Update `from_yaml` coercion test

### `tests/test_dsl.py`
- Keep all hard SL tests (unchanged)
- Add tests for `evaluate_trailing_sl()`:
  - Long: price rises past trigger → activates
  - Long: price rises, drops to trailing SL → fires
  - Long: price drops immediately → hard floor fires
  - Long: ratcheting (SL never goes down)
  - Long: sl_pct override (per-position hard floor)
  - Short: mirror tests
  - Edge: trigger=0 (immediate activation)

### `tests/test_position_tracker.py`
- Remove `compute_tp_price` test class (4 tests)
- Remove `trailing_activated` test (line 159-171)
- Update `update_price` DSL mode tests for trailing SL behavior
- Add trailing SL trigger tests in DSL mode

### `tests/test_models.py`
- `test_tracked_position_defaults`: `trailing_active` → `trailing_sl_activated`

### `tests/test_state_manager.py`
- Update serialization test: `trailing_active` → `trailing_sl_activated`
- Update deserialization test with backward compat check

### `tests/test_integration.py`
- Update state dict: `trailing_active` → `trailing_sl_activated`
- Update any tests checking for `trailing_take_profit` action

### `tests/test_execution_engine.py`
- `test_process_position_tick_price_drop_triggers_stop_loss_close` — verify still works (action name unchanged in legacy mode)

---

## Migration / Backward Compatibility

### State files
Old state has `trailing_active: true/false`. Load logic migrates: if old field exists and new doesn't, copy to `trailing_sl_activated`.

### Config files
Old config has `trailing_tp_trigger_pct` / `trailing_tp_delta_pct`. These are silently ignored (not in `fields` set). New `trailing_sl_*` fields have defaults.

### AI decisions
AI still outputs `stop_loss_pct`. This now actually influences trailing SL hard floor (previously ignored in DSL mode). No IPC changes needed.

### Scanner
No changes — scanner has zero references to any of this.

---

## Verification Steps

1. **Unit tests:** `pytest tests/ -x` — all pass
2. **Trailing SL math:**
   - Long $100, trigger=0.5%, step=0.95%:
     - Price $100.5 → activates, SL = $100.5 * 0.9905 = $99.55
     - Price $102 → SL = $102 * 0.9905 = $101.03 (ratcheted up)
     - Price $101.5 → above $101.03 → hold
     - Price $100.9 → below $101.03 → trailing_sl fires (+0.9% profit)
   - Long $100, price $98.75 immediately → hard floor fires (-1.25%)
3. **DSL unchanged:** Existing tier behavior unaffected
4. **AI sl_pct integration:** Position with AI sl_pct=2.0 → hard floor at entry * 0.98 (not config's 0.9875)
5. **State round-trip:** Save → load preserves trailing_sl_level, trailing_sl_activated
6. **Service restart:** `systemctl restart bot` — no errors

---

## Files Changed (summary)

| File | Action |
|------|--------|
| `bot/config.py` | Remove 2 fields, add 2 fields, update validate + from_yaml |
| `bot/config.yml` | Remove 2 lines, add 2 lines, change 1 value |
| `bot/config.example.yml` | Mirror config.yml |
| `bot/core/models.py` | Remove `trailing_active`, add `trailing_sl_activated` |
| `bot/dsl.py` | Add `evaluate_trailing_sl()` function |
| `bot/core/position_tracker.py` | Remove `compute_tp_price()`, `_get_sl_pct()`, rewrite update_price() |
| `bot/core/execution_engine.py` | Add trailing_sl handler, remove trailing_take_profit + trailing_activated |
| `bot/core/state_manager.py` | Update save/load for new field + backward compat |
| `bot/bot.py` | Update startup logging (3 locations) |
| `bot/tests/conftest.py` | Update fixtures |
| `bot/tests/test_config.py` | Update field tests |
| `bot/tests/test_dsl.py` | Add trailing SL tests |
| `bot/tests/test_position_tracker.py` | Remove TP tests, add SL tests |
| `bot/tests/test_models.py` | Update defaults test |
| `bot/tests/test_state_manager.py` | Update serialization tests |
| `bot/tests/test_integration.py` | Update action/field tests |
| `bot/tests/test_execution_engine.py` | Verify legacy SL still works |

**Total: 17 files (all modified, 0 new — tests added to existing test_dsl.py)**

**No changes to:** scanner/, ai-decisions/, position_sizer.py, signal_handler.py, executor.py, any IPC files

---

## Implementation Order

1. **Config** (`config.py`, `config.yml`, `config.example.yml`) — add new fields, remove old, update validation
2. **Models** (`models.py`) — update TrackedPosition fields
3. **DSL** (`dsl.py`) — add `evaluate_trailing_sl()` function
4. **Tests** — add trailing SL tests to `test_dsl.py` (TDD)
5. **Position Tracker** (`position_tracker.py`) — rewrite update_price(), remove dead code
6. **Execution Engine** (`execution_engine.py`) — add trailing_sl handler, remove TP
7. **State Manager** (`state_manager.py`) — update save/load with backward compat
8. **Bot startup** (`bot.py`) — update logging
9. **Remaining tests** — update conftest, test_config, test_models, test_state_manager, test_position_tracker, test_integration, test_execution_engine
10. **Run all tests** — `pytest tests/ -x`
11. **Service restart** — `systemctl restart bot`
