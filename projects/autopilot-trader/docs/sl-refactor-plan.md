# Stop Loss Refactor Plan: Remove ROE, Use Price Movement %

**Created:** 2026-03-27
**Author:** Subagent (plan-sl-refactor)
**Status:** DRAFT — awaiting John's review
**Branch:** `sl-no-roe` (to be created)

---

## Problem Statement

The bot uses **cross margin** — positions share one equity pool. ROE (return on equity per position) is an isolated-margin concept that doesn't apply here. The current system multiplies price movement by leverage to get "ROE," then compares against ROE thresholds. This is unnecessary — just compare price movement % directly.

**The fix:** Remove `× leverage` from all stop loss calculations. Config tier values become pure price movement %. Same trigger behavior, no fake metric.

Example: A position enters at $80,000. Hard SL fires at $79,000 (-1.25%). Tier 1 fires at $80,240 (+0.3%). Leverage is irrelevant.

### What Changes

| Aspect | Before (ROE-based) | After (Price-based) |
|--------|-------------------|-------------------|
| DSLState high water | `high_water_roe: float` | `high_water_price: float` (kept), `high_water_move_pct: float` (new) |
| DSLState locked floor | `locked_floor_roe: float` | `locked_floor_pct: float` (raw price %) |
| DSLTier trigger | `trigger_pct` (ROE%) | `trigger_pct` (price move %) |
| DSLTier buffer | `trailing_buffer_roe` (ROE%) | `trailing_buffer_pct` (price move %) |
| Hard SL | `hard_sl_pct × leverage → ROE` | `hard_sl_pct` (price %, direct) |
| Stagnation threshold | `stagnation_roe_pct` (ROE%) | `stagnation_move_pct` (price move %) |
| DB storage | `roe_pct` column | `price_move_pct` column (add, keep roe_pct for history) |
| Alerts | `PnL: $X (Y% ROE @ Zx)` | `PnL: $X (Y% move)` |

### What Stays the Same

- **DSL tiered trailing logic** — the algorithm is sound; only the units change
- **Trailing SL** (`evaluate_trailing_sl`) — already uses raw price %, no changes
- **Breach counting / stagnation timer** — same mechanics, different units
- **TrackedPosition structure** — `entry_price`, `size`, `high_water_mark` all stay
- **All exit triggers** — tier_lock, stagnation, hard_sl, trailing_sl still fire, just evaluated against price %

---

## 1. Dependency Map

### Imports Between Modified Files

```
config.py ← used by position_tracker.py, execution_engine.py, state_manager.py, bot.py
config.yml ← loaded by config.py (YAML)
dsl.py ← used by position_tracker.py, models.py (DSLState), state_manager.py (DSLState)
models.py ← used by position_tracker.py, execution_engine.py, shared_utils.py, state_manager.py
position_tracker.py ← used by execution_engine.py, state_manager.py, bot.py
execution_engine.py ← used by bot.py
shared_utils.py ← used by signal_processor.py (which is thin wrapper to execution_engine)
state_manager.py ← used by execution_engine.py, bot.py

ai-decisions/safety.py ← independent (no bot imports)
ai-decisions/context/data_reader.py ← reads result file from bot
ai-decisions/context/prompt_builder.py ← uses _calc_roe (internal method)
ai-decisions/context/stats_formatter.py ← reads DB (no ROE logic)
ai-decisions/db.py ← defines outcomes table schema
```

### Key Dependency Edges

| Source | Target | What's Shared |
|--------|--------|--------------|
| `dsl.py` | `position_tracker.py` | `DSLState`, `DSLTier`, `DSLConfig`, `evaluate_dsl` |
| `dsl.py` | `state_manager.py` | `DSLState` (serialization/deserialization) |
| `models.py` | `position_tracker.py` | `TrackedPosition` |
| `models.py` | `execution_engine.py` | `TrackedPosition` |
| `models.py` | `shared_utils.py` | `TrackedPosition` (in `log_outcome`) |
| `config.py` | `position_tracker.py` | `BotConfig` fields (stagnation_roe_pct, hard_sl_pct, dsl_tiers) |
| `config.py` | `execution_engine.py` | `BotConfig` (alert formatting uses roe_pct) |
| `db.py` (ai-decisions) | `shared_utils.py` (bot) | `DecisionDB.log_outcome()` (roe_pct field) |

---

## 2. Change Order

Must be changed in this order to avoid breaking at each step:

### Phase 0: Preparation (no behavior change)
1. **Create branch:** `git checkout -b sl-no-roe`

### Phase 1: Data Layer (models + DB + state)
2. **`bot/core/models.py`** — Add `high_water_move_pct: float = 0.0` to TrackedPosition. No other changes yet (DSLState changes come next).
3. **`ai-decisions/db.py`** — Add `price_move_pct` column migration in `_init_tables()`. Keep `roe_pct` column for historical data.
4. **`bot/core/state_manager.py`** — Add `price_move_pct` to serialized position data. Keep `roe_pct` for backward compat on load.

### Phase 2: DSL Engine (core logic)
5. **`bot/dsl.py`** — Major rewrite:
   - `DSLTier.trigger_pct` → comment change: now price move %, not ROE%
   - `DSLTier.trailing_buffer_roe` → rename to `trailing_buffer_pct` (price move %)
   - `DSLConfig.stagnation_roe_pct` → rename to `stagnation_move_pct`
   - `DSLState`:
     - Add `high_water_move_pct: float = 0.0`
     - Add `locked_floor_pct: float | None = None`
     - Remove `current_roe()` method → add `current_move_pct()` method (raw price movement, no leverage multiplication)
     - Keep `high_water_roe` temporarily as computed field for backward compat during migration
   - `evaluate_dsl()`:
     - Change `roe = state.current_roe(price)` → `move_pct = state.current_move_pct(price)`
     - Change `state.update_high_water(roe, price, now)` → `state.update_high_water(move_pct, price, now)`
     - Change `hard_sl_roe = -abs(cfg.hard_sl_pct) * state.leverage` → `hard_sl_floor = -abs(cfg.hard_sl_pct)` (already in price %)
     - Change tier buffer math from ROE to price %
     - Change stagnation check from `roe >= cfg.stagnation_roe_pct` → `move_pct >= cfg.stagnation_move_pct`
   - Default tier values: convert from ROE-based to price-based (÷10 if assuming 10x leverage as baseline)

### Phase 3: Config
6. **`bot/config.py`**:
   - Rename `stagnation_roe_pct` → `stagnation_move_pct`
   - Keep `dsl_leverage` field, mark as display-only
   - Update `dsl_tiers` validation: rename `trailing_buffer_roe` → `trailing_buffer_pct`
   - Update `from_yaml()` coercion list
7. **`bot/config.yml`**:
   - Rename `stagnation_roe_pct` → `stagnation_move_pct`
   - Rename `trailing_buffer_roe` → `trailing_buffer_pct` in all tiers
   - **Config value conversion:** The old config values were in ROE (price_move% × leverage). After removing leverage, the same values would mean 10x wider price moves. To keep identical behavior, the numerical values must be divided by the leverage factor. Since all positions were calibrated for 10x:
     - `trigger_pct: 3` (ROE) → `trigger_pct: 0.3` (price %)
     - `trailing_buffer_roe: 6` (ROE) → `trailing_buffer_pct: 0.6` (price %)
     - `stagnation_roe_pct: 5.0` (ROE) → `stagnation_move_pct: 0.5` (price %)
     - `hard_sl_pct: 1.25` — **NO CHANGE** (already price-based, the ROE conversion happened in dsl.py and is now removed)
   - Update comments to say "price move %" not "ROE%"

### Phase 4: Position Tracker + Execution Engine
8. **`bot/core/position_tracker.py`**:
   - `add_position()`: Remove `leverage` param from DSLState construction (or keep as display-only)
   - `update_price()`:
     - Replace `roe = pos.dsl_state.current_roe(price)` → `move_pct = pos.dsl_state.current_move_pct(price)`
     - Update all log messages: ROE → price move %
     - Tier lock alert: remove leverage-based floor_price calculation (use locked_floor_pct directly)
     - Stagnation alert: replace ROE with price move %
   - Keep `evaluate_trailing_sl()` call unchanged (already uses price %)
9. **`bot/core/execution_engine.py`**:
   - `_pnl_info()`: Remove `roe_pct` from return dict → add `price_move_pct`
   - Update ALL alert messages:
     - Remove `({roe_pct:+.1f}% ROE @ {leverage:.0f}x)` patterns
     - Replace with `({price_move_pct:+.2f}% move)` or keep just `$pnl_usd`
   - Stagnation status alert: remove ROE, show price move %
   - DSL exit alerts: remove ROE, show price move %
   - Trailing SL alerts: already mostly price-based, remove ROE references
   - Legacy alerts: remove ROE
10. **`bot/core/shared_utils.py`**:
    - `log_outcome()`: Remove `roe_pct` calculation, add `price_move_pct`
    - Update DB insert to use `price_move_pct` instead of `roe_pct`
    - Update log message to remove ROE reference

### Phase 5: AI Trader (display/prompt changes)
11. **`ai-decisions/context/prompt_builder.py`**:
    - `_calc_roe()` → rename to `_calc_move_pct()`, remove leverage multiplication
    - Update position display: `(ROE: {roe:+.1f}%)` → `(move: {move_pct:+.2f}%)`
    - Update outcome display: `(ROE {roe:+.1f}%)` → `(move {move_pct:+.2f}%)`
12. **`ai-decisions/safety.py`** — No changes needed (doesn't reference ROE)
13. **`ai-decisions/context/data_reader.py`** — No changes needed (reads raw numbers)
14. **`ai-decisions/context/stats_formatter.py`** — No changes needed (uses pnl_usd, not ROE)

### Phase 6: Bot startup + display
15. **`bot/bot.py`** — Update startup logging:
    - Remove `dsl_leverage` from startup log (or mark deprecated)
    - Update stagnation reference from `stagnation_roe_pct` → `stagnation_move_pct`
16. **`bot/config.example.yml`** — Mirror config.yml changes

---

## 3. Backward Compatibility

### State Files (`bot_state.json`)

**Saved fields that change:**

| Old Field | New Field | Migration |
|-----------|-----------|-----------|
| `dsl.high_water_roe` | `dsl.high_water_move_pct` | On load: if old exists and new doesn't, divide by `leverage` to approximate |
| `dsl.locked_floor_roe` | `dsl.locked_floor_pct` | On load: if old exists and new doesn't, divide by `leverage` |
| `dsl.current_tier_trigger` | `dsl.current_tier_trigger` | Unchanged (same field name, different meaning) |
| `trailing_active` | `trailing_sl_activated` | Already migrated in prior refactor |

**Load migration in `state_manager.py`:**
```python
# Backward compat: migrate ROE-based DSL state to price-based
if dsl_data.get("high_water_roe") and not dsl_data.get("high_water_move_pct"):
    lev = dsl_data.get("leverage", 10.0)
    dsl_data["high_water_move_pct"] = dsl_data["high_water_roe"] / lev
    logging.info(f"Migrated high_water_roe={dsl_data['high_water_roe']} → "
                 f"high_water_move_pct={dsl_data['high_water_move_pct']:.2f}%")
if dsl_data.get("locked_floor_roe") and not dsl_data.get("locked_floor_pct"):
    lev = dsl_data.get("leverage", 10.0)
    dsl_data["locked_floor_pct"] = dsl_data["locked_floor_roe"] / lev
```

**Save in `state_manager.py`:**
```python
"dsl": {
    ...
    "high_water_move_pct": dsl.high_water_move_pct,
    "locked_floor_pct": dsl.locked_floor_pct,
    # Backward compat: keep old field names for gradual rollout
    "high_water_roe": dsl.high_water_move_pct * dsl.leverage if dsl.leverage else 0,
    "locked_floor_roe": dsl.locked_floor_pct * dsl.leverage if (dsl.locked_floor_pct and dsl.leverage) else None,
}
```

### Config Files (`config.yml`)

**Old fields silently ignored:** `stagnation_roe_pct` is not in the `fields` set if renamed. New field `stagnation_move_pct` has a default. Old `dsl_leverage` kept for display.

**Tier conversion:** Old `trigger_pct: 7` (ROE) → new `trigger_pct: 0.7` (price %). Old `trailing_buffer_roe: 5` → new `trailing_buffer_pct: 0.5`.

**Manual migration required:** John must update `config.yml` tier values. No auto-conversion in code (config is user-editable YAML, not runtime state).

### DB Entries (`trader.db`)

**`outcomes` table:** Keep `roe_pct` column for historical data. Add `price_move_pct` column. New entries write both. Old entries show `price_move_pct = NULL`.

**Migration in `db.py`:**
```python
# In _init_tables():
columns = {row[1] for row in self._conn.execute("PRAGMA table_info(outcomes)").fetchall()}
if "price_move_pct" not in columns:
    self._conn.execute("ALTER TABLE outcomes ADD COLUMN price_move_pct REAL")
```

**`log_outcome()` in `shared_utils.py`:** Write both fields:
```python
"roe_pct": roe_pct,           # kept for history
"price_move_pct": pnl_pct,    # raw price movement (what pnl_pct already is!)
```

Note: `pnl_pct` in the current DB IS the raw price movement % (see `shared_utils.py` line 140-141). The `roe_pct` is `pnl_pct * leverage`. So `price_move_pct = pnl_pct` — we're just adding an alias column and stopping the leverage multiplication for display purposes.

---

## 4. DB Migration

### Schema Changes

```sql
-- In db.py _init_tables(), add after existing migration block:
ALTER TABLE outcomes ADD COLUMN price_move_pct REAL;
```

No other schema changes needed. The `outcomes` table already has `pnl_pct` which IS the raw price move %. The new column is an explicit alias for clarity.

### Data Migration (optional, not required)

Old rows have `pnl_pct` = raw price move (correct) and `roe_pct` = `pnl_pct × leverage`. If we want to backfill `price_move_pct`:

```sql
UPDATE outcomes SET price_move_pct = pnl_pct WHERE price_move_pct IS NULL;
```

This is safe because `pnl_pct` has always been raw price movement (see `shared_utils.py` line 140-141).

### What Doesn't Change

- `decisions` table — no ROE references
- `alerts` table — no ROE references
- `patterns.json` — no ROE references (uses pnl_usd)

---

## 5. Config Migration

### Tier Value Conversion

Assuming baseline leverage of 10x (the current default):

| Old (ROE-based) | New (Price-based) | Conversion |
|-----------------|-------------------|------------|
| `trigger_pct: 3` | `trigger_pct: 0.3` | ÷ 10 |
| `trigger_pct: 7` | `trigger_pct: 0.7` | ÷ 10 |
| `trigger_pct: 12` | `trigger_pct: 1.2` | ÷ 10 |
| `trigger_pct: 15` | `trigger_pct: 1.5` | ÷ 10 |
| `trigger_pct: 20` | `trigger_pct: 2.0` | ÷ 10 |
| `trigger_pct: 30` | `trigger_pct: 3.0` | ÷ 10 |
| `trailing_buffer_roe: 6` | `trailing_buffer_pct: 0.6` | ÷ 10 |
| `trailing_buffer_roe: 5` | `trailing_buffer_pct: 0.5` | ÷ 10 |
| `trailing_buffer_roe: 4` | `trailing_buffer_pct: 0.4` | ÷ 10 |
| `trailing_buffer_roe: 3` | `trailing_buffer_pct: 0.3` | ÷ 10 |
| `trailing_buffer_roe: 2` | `trailing_buffer_pct: 0.2` | ÷ 10 |
| `trailing_buffer_roe: 1` | `trailing_buffer_pct: 0.1` | ÷ 10 |
| `stagnation_roe_pct: 5.0` | `stagnation_move_pct: 0.5` | ÷ 10 |
| `hard_sl_pct: 1.25` | `hard_sl_pct: 1.25` | **NO CHANGE** (already price-based!) |

**Important:** `hard_sl_pct` in config is ALREADY a price-based percentage. The ROE conversion happens in `dsl.py` line 101: `hard_sl_roe = -abs(cfg.hard_sl_pct) * state.leverage`. After refactor, we use `hard_sl_pct` directly without the leverage multiplication.

### Recommended New Config Values

```yaml
# DSL (Dynamic Stop Loss) — tiered trailing stop loss
dsl_enabled: true
max_risk_pct: 0.04
max_margin_pct: 0.15
min_risk_reward: 1.5
max_concurrent_signals: 3

# Deprecated: only used for display, not in SL math
dsl_leverage: 10.0

# Stagnation: exit if price hasn't made new highs for N minutes
# After reaching this % move from entry (= 5% ROE at 10x)
stagnation_move_pct: 0.5
stagnation_minutes: 90

# DSL tiers — price move % thresholds
dsl_tiers:
  - trigger_pct: 0.3       # was 3% ROE (= 0.3% price move at 10x)
    trailing_buffer_pct: 0.6  # was 6% ROE
    consecutive_breaches: 3
  - trigger_pct: 0.7
    trailing_buffer_pct: 0.5
    consecutive_breaches: 3
  - trigger_pct: 1.2
    trailing_buffer_pct: 0.4
    consecutive_breaches: 2
  - trigger_pct: 1.5
    trailing_buffer_pct: 0.3
    consecutive_breaches: 2
  - trigger_pct: 2.0
    trailing_buffer_pct: 0.2
    consecutive_breaches: 2
  - trigger_pct: 3.0
    trailing_buffer_pct: 0.1
    consecutive_breaches: 2
```

**Note:** `lock_hw_pct` is kept as fallback but `trailing_buffer_pct` takes priority. No value change needed.

---

## 6. Testing Checklist

### After Phase 2 (DSL Engine):

- [ ] `evaluate_dsl()` returns same exits as before for equivalent price moves
- [ ] Hard SL fires at `entry × (1 - hard_sl_pct/100)` for longs (no leverage multiplication)
- [ ] Tier triggers fire at correct price move % thresholds
- [ ] Trailing buffer is in price %, not ROE
- [ ] Stagnation timer activates at `stagnation_move_pct` price move
- [ ] Stagnation exit fires after `stagnation_minutes` with no new price highs
- [ ] Short positions mirror correctly (reverse signs)
- [ ] Breach counting works identically to before (just different units)

### After Phase 4 (Position Tracker + Execution Engine):

- [ ] Telegram alerts show `$PnL (X.XX% move)` instead of `$PnL (X% ROE @ 10x)`
- [ ] Tier lock alert shows floor price, not floor ROE
- [ ] Stagnation status alert shows price move %, not ROE
- [ ] DSL exit alerts show entry/exit prices and $ PnL only
- [ ] Trailing SL alerts unchanged (already price-based)
- [ ] `log_outcome()` writes both `roe_pct` and `price_move_pct` to DB

### After Phase 5 (AI Trader):

- [ ] LLM prompt shows position PnL as price move %, not ROE
- [ ] Recent outcomes section shows price move %, not ROE
- [ ] Account section still shows daily PnL in USD (unchanged)

### After Phase 6 (Full System):

- [ ] Bot starts without errors
- [ ] Existing positions survive restart (state migration works)
- [ ] DSL tiers activate at correct price levels
- [ ] Hard SL triggers at expected price level
- [ ] Stagnation timer works with new thresholds
- [ ] DB records both roe_pct and price_move_pct on close
- [ ] AI trader reads positions correctly from result file
- [ ] No regressions in trailing SL behavior

### Integration Tests:

- [ ] Start bot with existing `bot_state.json` → verify DSL state migrated
- [ ] Open position → verify DSL tiers activate at correct price levels
- [ ] Trigger hard SL → verify exit at expected price (not ROE-adjusted)
- [ ] Trigger tier lock → verify exit with correct price floor
- [ ] Stagnation → verify exit after timer with no new price highs
- [ ] Close position → verify DB has both `roe_pct` and `price_move_pct`

---

## 7. Doc Updates

| File | Changes Needed |
|------|---------------|
| `docs/autopilot-trader.md` | Section 3.2 (DSL): Replace ROE references with price move %. Update default tiers table. Update exit triggers. Section 5 (Config): Rename `stagnation_roe_pct`, update DSL tier values. Section 7 (Known Issues): Remove ROE-related confusion notes. |
| `docs/cheatsheet.md` | "Hard SL" line: remove ROE conversion note. "DSL tiers" line: update trigger values to price %. Key Patterns section: update DSL description. |
| `docs/plans/dsl-trailing-sl-plan.md` | Archive note that ROE refactoring is separate. No content changes needed. |
| `docs/position-sizing-plan.md` | If it references ROE for risk calculations, update. |
| `docs/reference/lighter-api.md` | If it documents ROE in API responses, add note about cross margin difference. |
| `README.md` (if exists) | Update architecture description if it mentions ROE. |

**Files with NO ROE references (no changes needed):**
- `docs/ideas/pocket-ideas.md`
- `docs/plans/pattern-learning-completion.md`
- `docs/archive/unified-dashboard-plan.md`
- `docs/reference/lighter-quota-research.md`

---

## 8. Risk Assessment

### 🔴 High Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Tier thresholds too tight after conversion** | Positions exit too early on small price moves | Test with paper/historical data first. Keep `trailing_buffer_pct` slightly wider than direct ÷10 conversion. John can tune after observing first few exits. |
| **State migration produces wrong HW values** | DSL floors calculated incorrectly after restart → premature exits | Add extensive logging on migration. Save both old and new fields during transition period. Test with actual `bot_state.json` before deploying. |
| **Dual-write DB inconsistency** | `roe_pct` and `price_move_pct` diverge | They won't — `price_move_pct = pnl_pct` (which already exists), and `roe_pct = pnl_pct * leverage`. Same formula, just two columns. |

### 🟡 Medium Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Config values too aggressive at price level** | DSL fires constantly, never lets positions run | Default tiers use conservative values (0.3% first trigger). John should monitor first 24h closely. Easy to widen by editing config.yml and restarting. |
| **`dsl_leverage` removal confusion** | User wonders why leverage is in config but not used in SL | Keep the field, add deprecation comment. It's still used for display in some alerts. |
| **AI trader `_calc_roe` changes affect LLM decisions** | LLM sees different position sizes in prompt, may make different decisions | The absolute `$PnL` is unchanged. Only the percentage display changes. LLM should respond to dollar amounts more than percentages anyway. Monitor first few AI cycles after deploy. |

### 🟢 Low Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Trailing SL unaffected** | None — already uses price % | No changes needed, but verify in integration tests. |
| **Legacy mode unaffected** | None — uses `_compute_hard_floor_price()` which is already price-based | No changes needed. |
| **Scanner unaffected** | None — zero references to DSL/ROE | Confirmed via grep. No changes. |

### Rollback Plan

If the refactor causes issues:
1. `git checkout main` → `systemctl restart bot`
2. Old state files are backward-compatible (we save old field names too)
3. DB has `roe_pct` column still — old data accessible
4. Config can be reverted to ROE-based values by restoring old `config.yml`

---

## 9. Implementation Notes

### The `leverage` Field on DSLState

Currently `DSLState.leverage` is used for:
1. **ROE calculation** (`current_roe()`) → removed in this refactor
2. **Hard SL ROE conversion** (`hard_sl_roe = hard_sl_pct * leverage`) → removed
3. **Alert display** (`{roe}% ROE @ {leverage}x`) → removed
4. **State serialization** → keep for backward compat
5. **Floor price calculation in tier lock alert** → simplified (use locked_floor_pct directly)

**Decision:** Keep `leverage` field on DSLState for now (it's set from exchange IMF). It's no longer used in SL math but could be useful for display. Mark as "display only" in comments. Can be fully removed in a follow-up cleanup.

### The `_calc_roe` Method in PromptBuilder

This method computes ROE for the LLM prompt using cross margin formula: `raw_move × (notional / equity)`. After refactor, replace with raw price move %. The LLM will see `BTC LONG $100 @ 87500.00 (move: +0.45%)` instead of `(ROE: +2.8%)`. This is actually MORE useful because:
- It's consistent with what the trader sees on the exchange
- The dollar amount ($100) gives scale
- The % move is independent of leverage assumptions

### Trailing Buffer vs Lock HW Pct

The current `DSLTier` has two buffer mechanisms:
- `trailing_buffer_roe`: Fixed ROE buffer from HW (floor = HW - buffer) — takes priority
- `lock_hw_pct`: Percentage of HW to lock (floor = HW × lock_pct/100) — fallback

After refactor, `trailing_buffer_pct` is a fixed price % buffer from HW. If HW is at +2.0% price move and buffer is 0.3%, floor = 1.7% price move. This is simpler and more intuitive than the ROE version.

### Default Tier Values Rationale

Current ROE tiers (at 10x leverage):
- 3% ROE = 0.3% price move → first gear
- 7% ROE = 0.7% price move → tightening
- 12% ROE = 1.2% price move → aggressive
- 15% ROE = 1.5% price move → very aggressive
- 20% ROE = 2.0% price move → maximum
- 30% ROE = 3.0% price move → extreme (rarely hit)

These are reasonable price move thresholds for crypto perps. First trigger at 0.3% catches small pullbacks. At 3.0%, the position has run significantly and deserves tight protection.

---

## 10. File-by-File Change Summary

| File | Lines Changed | Type | Risk |
|------|--------------|------|------|
| `bot/dsl.py` | ~120 | Rename + logic | 🔴 Core logic |
| `bot/config.py` | ~30 | Rename fields | 🟡 Config |
| `bot/config.yml` | ~20 | Value changes | 🟡 Config |
| `bot/core/models.py` | ~5 | Add field | 🟢 Data |
| `bot/core/state_manager.py` | ~40 | Migration + save/load | 🟡 State |
| `bot/core/position_tracker.py` | ~25 | Rename refs | 🟡 Logic |
| `bot/core/execution_engine.py` | ~60 | Alert formatting | 🟢 Display |
| `bot/core/shared_utils.py` | ~15 | DB write + log format | 🟡 Data |
| `ai-decisions/db.py` | ~5 | Schema migration | 🟢 Data |
| `ai-decisions/context/prompt_builder.py` | ~20 | Rename method + display | 🟢 Display |
| `ai-decisions/safety.py` | 0 | None | N/A |
| `ai-decisions/context/data_reader.py` | 0 | None | N/A |
| `ai-decisions/context/stats_formatter.py` | 0 | None | N/A |
| `docs/autopilot-trader.md` | ~30 | Documentation | 🟢 Docs |
| `docs/cheatsheet.md` | ~10 | Documentation | 🟢 Docs |

**Total estimated: ~480 lines changed across 15 files (11 code, 2 docs, 2 config)**
