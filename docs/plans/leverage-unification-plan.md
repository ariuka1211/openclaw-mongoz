# Leverage Unification Plan

> **Goal:** One leverage number, enforced on the exchange, used everywhere.
> Currently: 7 different leverage calculations, none enforced on exchange.
> After: 1 config value → `update_leverage()` on exchange → read back for DSL/ROE.

---

## Current State — The Problem

### 7 Leverage Numbers That Don't Agree

| # | Where | What | Source |
|---|-------|------|--------|
| 1 | `scanner/config.ts` | `maxLeverageCap: 20` | Hardcoded |
| 2 | `scanner/position-sizing.ts` | `actualLeverage = posSize / equity` | Derived at runtime |
| 3 | `ai-decisions/safety.py` | `max_leverage: 20` | `config.json` safety block |
| 4 | `ai-decisions` (LLM) | `"leverage": 3-10` | AI decides per trade |
| 5 | `bot/config.yml` | `default_leverage: 10.0` | Config (tracking only) |
| 6 | `bot/dsl.py` DSLState | `effective_leverage = notional / equity` | Calculated at open |
| 7 | Exchange (Lighter) | Auto: `notional / equity` | Uncapped cross margin |

**None of these enforce anything on the exchange.** Result: a 50× BTC position opened unchallenged.

### How Leverage Is Used (and Misused)

| Consumer | Uses | How |
|----------|------|-----|
| `position_tracker.py` `add_position()` | `cfg.default_leverage` (fallback), `equity` (for effective_lev) | Creates DSLState with `leverage` + `effective_leverage` |
| `dsl.py` `current_roe()` | `effective_leverage` | `move% × effective_leverage` |
| `dsl.py` `evaluate()` | `effective_leverage` | Hard SL ROE calc |
| `shared_utils.py` `log_outcome()` | `equity`, then `dsl_state.leverage`, then `cfg.default_leverage` | 3-path cascade fallback |
| `prompt_builder.py` `_calc_roe()` | `equity` or `position.leverage` | 2-path fallback |
| `result_writer.py` | `dsl_state.effective_leverage` or `cfg.default_leverage` | 2-path fallback |
| `execution_engine.py` tick | `account_equity` / `notional` | Recalculates effective_leverage per tick |
| `signal_handler.py` open | `min(cfg.default_leverage, 10)` | Passes as `leverage` param |
| `executor.py` AI open | `min(ai_decision_leverage, 10)` | Passes as `leverage` param |

### What's Wrong

1. **Exchange never gets a leverage limit** — `update_leverage()` never called
2. **Two "leverages" per position** — `leverage` (config) vs `effective_leverage` (cross-margin)
3. **3 fallback chains** for ROE calculation — each slightly different
4. **AI decides leverage** — but AI doesn't know equity, cross-margin state, or exchange limits
5. **Scanner outputs leverage** — but it's derived from sizing, not a decision
6. **`leverage` field in AI decision schema** — complex, unnecessary, poorly calibrated

---

## Target State

```
                    ┌─────────────────────────────────┐
                    │  bot/config.yml                  │
                    │  default_leverage: 10.0          │
                    │  (THE single source of truth)    │
                    └──────────┬──────────────────────┘
                               │
                    ┌──────────▼──────────────────────┐
                    │  bot/api/lighter_api.py          │
                    │  open_position()                 │
                    │                                  │
                    │  1. update_leverage(market, 10)  │  ◄── enforce on exchange
                    │  2. create_market_order(...)     │
                    │  3. read IMF from response       │  ◄── confirm it worked
                    └──────────┬──────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼─────┐  ┌──────▼──────┐  ┌──────▼───────┐
     │  DSLState     │  │ outcome     │  │ AI result    │
     │  .leverage=X  │  │ .roe =      │  │ .leverage=X  │
     │  (single val) │  │  pnl% × X   │  │ (from IMF)   │
     └──────────────┘  └─────────────┘  └──────────────┘
```

### Design Principles

1. **Exchange enforces leverage** — `update_leverage()` before every open
2. **One `leverage` field** — from exchange IMF, no dual tracking
3. **AI does NOT choose leverage** — removed from decision schema
4. **Scanner does NOT output leverage** — outputs position size only
5. **One ROE formula** — `price_move% × leverage` (where leverage = exchange IMF)
6. **Config has one value** — `default_leverage: 10.0`

---

## Phase 1: Exchange Enforcement — Set Leverage on Open

**Risk: Medium | Files: 2 | Tests: add to test_lighter_api.py**

### 1a. Add `set_leverage()` to LighterAPI

**File:** `bot/api/lighter_api.py`

Add a new method after `open_position()`:

```python
async def set_leverage(self, market_id: int, leverage: float) -> bool:
    """Set leverage cap on the exchange via IMF. Call BEFORE open_position().
    
    Args:
        market_id: Market to set leverage for
        leverage: Desired leverage (e.g., 10.0 for 10x)
    
    Returns:
        True if leverage was set successfully, False otherwise.
    """
    await self._ensure_signer()
    from lighter.signer_client import SignerClient
    try:
        tx_info, api_response, error = await self._signer.update_leverage(
            market_index=market_id,
            margin_mode=SignerClient.CROSS_MARGIN_MODE,
            leverage=leverage,
        )
        if error:
            logging.error(f"❌ set_leverage({market_id}, {leverage}x) failed: {error}")
            return False
        
        # Log quota impact
        quota_val, _ = self._extract_quota_from_response(api_response) if api_response else (None, None)
        if quota_val is not None:
            self._update_quota_cache(quota_val)
        
        logging.info(f"✅ Leverage set: {leverage}x for market {market_id} (quota={self._volume_quota_remaining})")
        return True
    except Exception as e:
        logging.error(f"❌ set_leverage({market_id}, {leverage}x) exception: {e}")
        return False
```

### 1b. Call `set_leverage()` Before Every Open

**File:** `bot/api/lighter_api.py` → `open_position()`

At the top of `open_position()`, after `_ensure_signer()`, add:

```python
# Enforce leverage cap on exchange before placing order
if not await self.set_leverage(market_id, self.cfg.default_leverage):
    logging.error(f"❌ Aborting open: could not set {self.cfg.default_leverage}x leverage for market {market_id}")
    return False
```

**Important:** This means `open_position()` now also needs `default_leverage` accessible. It already has `self.cfg`, so this works.

### 1c. Tests

- Mock `_signer.update_leverage` to return success/failure
- Verify `open_position()` aborts if `set_leverage()` fails
- Verify quota cache is updated after `set_leverage()`

---

## Phase 2: Simplify DSLState — One Leverage Field

**Risk: High (DSL is critical) | Files: 4 | Tests: update test_dsl.py, test_position_tracker.py**

### 2a. Collapse DSLState fields

**File:** `bot/dsl.py`

```python
# BEFORE
@dataclass
class DSLState:
    leverage: float = 10.0              # Config leverage (for display/reference)
    effective_leverage: float = 10.0    # Cross margin effective leverage (notional/equity)

# AFTER
@dataclass
class DSLState:
    leverage: float = 10.0              # Exchange-enforced leverage (from IMF)
```

Remove `effective_leverage` field. `current_roe()` already uses `self.effective_leverage` — change to `self.leverage`.

```python
# BEFORE
def current_roe(self, price: float) -> float:
    ...
    return move * self.effective_leverage

# AFTER
def current_roe(self, price: float) -> float:
    ...
    return move * self.leverage
```

Also update `evaluate()`:
```python
# BEFORE
hard_sl_roe = -abs(cfg.hard_sl_pct) * state.effective_leverage
# AFTER
hard_sl_roe = -abs(cfg.hard_sl_pct) * state.leverage
```

### 2b. Simplify `position_tracker.add_position()`

**File:** `bot/core/position_tracker.py`

```python
# BEFORE
def add_position(self, market_id, symbol, side, entry, size, leverage=None, sl_pct=None):
    lev = leverage or self.cfg.default_leverage
    notional = abs(size * entry)
    if self.account_equity > 0 and notional > 0:
        eff_lev = notional / self.account_equity
    else:
        eff_lev = lev
    dsl_state = DSLState(
        side=side, entry_price=entry,
        leverage=lev, effective_leverage=eff_lev, ...

# AFTER
def add_position(self, market_id, symbol, side, entry, size, leverage=None, sl_pct=None):
    lev = leverage or self.cfg.default_leverage
    dsl_state = DSLState(
        side=side, entry_price=entry,
        leverage=lev, ...
```

Remove the `effective_leverage` derivation. The `leverage` param passed in IS the exchange leverage.

### 2c. Simplify `position_tracker` check_triggers()

In `check_triggers()`, the trailing TP ROE calculation recalculates effective_leverage. Replace:

```python
# BEFORE
notional = abs(pos.size * pos.entry_price)
if self.account_equity > 0 and notional > 0:
    effective_lev = notional / self.account_equity
else:
    effective_lev = self.cfg.default_leverage
return ("trailing_activated", {"roe": pnl_pct * effective_lev, ...})

# AFTER
lev = pos.dsl_state.leverage if pos.dsl_state else self.cfg.default_leverage
return ("trailing_activated", {"roe": pnl_pct * lev, ...})
```

### 2d. Fix `execution_engine.py` tick refresh

**File:** `bot/core/execution_engine.py`

Lines ~93-98 and ~120-126: The tick loop recalculates `effective_leverage` from `notional/equity` every tick. This was compensating for the lack of exchange enforcement. With `set_leverage()` on exchange, the IMF is set. We should read it from the exchange position data instead.

```python
# BEFORE (lines 93-98)
_pos.dsl_state.effective_leverage = _notional / new_balance
# and (lines 120-126)
existing.dsl_state.effective_leverage = notional / self.tracker.account_equity
elif pos.get("leverage"):
    existing.dsl_state.effective_leverage = pos["leverage"]
existing.dsl_state.leverage = pos.get("leverage", existing.dsl_state.leverage)

# AFTER
# Read leverage from exchange position data (set by update_leverage)
if pos.get("leverage"):
    existing.dsl_state.leverage = pos["leverage"]
# No more effective_leverage — it's just leverage now
```

### 2e. Fix `execution_engine.py` sync reconciliation

Line ~161: `tracker.add_position(..., leverage=pos.get("leverage"))` — unchanged (already passes leverage).

### 2f. Tests

- Update all `DSLState(... effective_leverage=...)` → `DSLState(... leverage=...)`
- Update `test_position_tracker.py` effective_leverage tests → test that leverage param is stored
- Verify `current_roe()` still produces correct ROE values
- Verify DSL tier triggers still fire at expected ROE levels

---

## Phase 3: Unify ROE Calculation — One Formula

**Risk: Medium | Files: 3 | Tests: add shared_utils tests**

### 3a. Simplify `shared_utils.log_outcome()`

**File:** `bot/core/shared_utils.py`

```python
# BEFORE — 3 fallback paths
equity = tracker.account_equity
if equity > 0 and size_usd > 0:
    actual_leverage = size_usd / equity
elif pos.dsl_state:
    actual_leverage = pos.dsl_state.leverage
else:
    actual_leverage = cfg.default_leverage
roe_pct = pnl_pct * actual_leverage

# AFTER — 1 path
leverage = pos.dsl_state.leverage if pos.dsl_state else cfg.default_leverage
roe_pct = pnl_pct * leverage
```

### 3b. Simplify `prompt_builder._calc_roe()`

**File:** `ai-decisions/context/prompt_builder.py`

```python
# BEFORE — cross margin + fallback
notional = position.get("position_size_usd", position.get("size_usd", 0))
if equity > 0 and notional > 0:
    effective_lev = notional / equity
    return raw * effective_lev
leverage = position.get("leverage", 1.0)
return raw * leverage

# AFTER — 1 path
leverage = position.get("leverage", 1.0)
return raw * leverage
```

The `leverage` field in position data now comes from `result_writer.py` which writes `dsl_state.leverage` (exchange IMF).

### 3c. Simplify `result_writer.py`

**File:** `bot/core/result_writer.py`

```python
# BEFORE (2 places)
"leverage": pos.dsl_state.effective_leverage if pos.dsl_state else cfg.default_leverage,

# AFTER
"leverage": pos.dsl_state.leverage if pos.dsl_state else cfg.default_leverage,
```

### 3d. Simplify `position_tracker.py` check_triggers()

Already handled in Phase 2c.

---

## Phase 4: Remove Leverage from AI Decisions

**Risk: Medium | Files: 4 | Tests: update test_safety.py, test_prompt_builder.py, test_bot_protocol.py**

### 4a. Remove `leverage` from AI decision schema

**File:** `ai-decisions/safety.py`

In `_validate_schema()`, remove `"leverage"` from open_required:
```python
# BEFORE
open_required = ["symbol", "direction", "size_pct_equity", "leverage", "stop_loss_pct"]
# AFTER
open_required = ["symbol", "direction", "size_pct_equity", "stop_loss_pct"]
```

In `_validate_open()`, remove all leverage validation:
```python
# REMOVE these lines
leverage = decision.get("leverage", 0)
...
if leverage <= 0:
    reasons.append(...)
elif leverage > self.max_leverage:
    reasons.append(...)
...
if leverage > 0 and sl_pct is not None:
    max_safe_sl = 100.0 / leverage / 2.0
    ...
```

Also remove `max_leverage` from `__slots__` and `__init__`.

### 4b. Remove `leverage` from IPC protocol

**File:** `ai-decisions/ipc/bot_protocol.py`

```python
# BEFORE
output = {
    ...
    "leverage": decision.get("leverage"),
    ...
}
# AFTER — remove the leverage line entirely
output = {
    ...
    # leverage removed — bot enforces on exchange
    ...
}
```

### 4c. Update prompt template

Remove any leverage-related instructions from the LLM prompt. The prompt should no longer ask the AI to specify leverage. Remove from `prompt_builder.py`:

- Any mentions of leverage in the prompt template
- The `_calc_roe()` method can stay (it's useful for showing position context), but simplify as per Phase 3b

### 4d. Update bot `executor.py` and `signal_handler.py`

**File:** `bot/core/executor.py`

```python
# BEFORE
ai_leverage = min(float(decision.get("leverage", cfg.default_leverage)), 10)
tracker.add_position(market_id, symbol, direction, current_price, actual_size, leverage=ai_leverage, sl_pct=ai_sl_pct)

# AFTER — always use config default (enforced on exchange)
tracker.add_position(market_id, symbol, direction, current_price, actual_size, sl_pct=ai_sl_pct)
```

**File:** `bot/core/signal_handler.py`

```python
# BEFORE
tracker.add_position(mid, symbol, direction, current_price, actual_size, leverage=min(cfg.default_leverage, 10))

# AFTER
tracker.add_position(mid, symbol, direction, current_price, actual_size)
```

The `leverage` param in `add_position()` falls back to `cfg.default_leverage` if not provided.

### 4e. Tests

- Update `test_safety.py` — remove leverage validation tests
- Update `test_bot_protocol.py` — remove leverage from decision fixtures
- Update `test_prompt_builder.py` — remove leverage from position fixtures
- Verify `test_executor.py` and `test_signal_handler.py` still pass (they may need leverage param removed)

---

## Phase 5: Clean Up Scanner Leverage References

**Risk: Low | Files: 2 | Tests: update scanner tests**

### 5a. Remove leverage outputs from `position-sizing.ts`

**File:** `scanner/src/position-sizing.ts`

Remove from return type and values:
```typescript
// BEFORE
return {
  maxLeverage, positionSizeUsd, actualLeverage,
  riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct,
  pass, reason
};

// AFTER
return {
  positionSizeUsd, riskAmountUsd,
  stopLossDistanceAbs, stopLossDistancePct, liqDistPct,
  pass, reason
};
```

Keep `maxLeverage` internally for the position cap: `maxAllowedPosition = accountEquity * maxLeverage`. Just don't export it.

Remove `actualLeverage` entirely — it was only used for display. The cap logic stays:
```typescript
const maxAllowedPosition = accountEquity * maxLeverage;  // internal cap, not exported
positionSizeUsd = Math.min(positionSizeUsd, maxAllowedPosition);
```

### 5b. Remove leverage from `config.ts` display

**File:** `scanner/src/output.ts`

Remove `Max leverage: ${CONFIG.maxLeverageCap}×` from console output.

### 5c. Remove `maxLeverageCap` from CONFIG

**File:** `scanner/src/config.ts`

```typescript
// BEFORE
maxLeverageCap: 20,
// AFTER — remove or keep as internal constant
const MAX_LEVERAGE_CAP = 20;  // Internal only, not exported in signals
```

### 5d. Tests

- Update `position-sizing.test.ts` — remove leverage assertions
- Integration test: verify signals.json doesn't contain leverage fields

---

## Phase 6: Update Config & Documentation

**Risk: Low | Files: 4**

### 6a. Update config.yml comment

**File:** `bot/config.yml`

```yaml
# BEFORE
default_leverage: 10.0          # Leverage for ROE calculation

# AFTER
default_leverage: 10.0          # Exchange-enforced leverage cap (set via IMF before opens)
```

### 6b. Remove max_leverage from AI config

**File:** `ai-decisions/config.json`

Remove `"max_leverage"` from the `"safety"` block (if present). Leverage is now enforced by the bot/exchange, not the safety layer.

### 6c. Update cheatsheet.md

Update the "Key Patterns" section:
- Remove `default_leverage: 10.0` from DSL notes (it's now exchange leverage)
- Add note about `set_leverage()` call before opens

### 6d. Update autopilot-trader.md

Update architecture description to reflect unified leverage flow.

---

## Verification Checklist

After all phases:

- [ ] `open_position()` calls `set_leverage()` before `create_market_order()`
- [ ] DSLState has single `leverage` field (no `effective_leverage`)
- [ ] All ROE calculations use `dsl_state.leverage`
- [ ] No `effective_leverage` references anywhere in codebase
- [ ] AI decision schema no longer includes `leverage`
- [ ] Safety layer no longer validates `leverage`
- [ ] Scanner doesn't output `leverage` in signals.json
- [ ] `result_writer.py` writes `leverage` from DSLState (single source)
- [ ] `prompt_builder._calc_roe()` uses position's `leverage` field
- [ ] `log_outcome()` uses `dsl_state.leverage` (no fallback chain)
- [ ] All 310 tests pass
- [ ] All 3 services restart clean
- [ ] Grep for `effective_leverage` returns 0 matches (except maybe DSLState dataclass field removal)

---

## Rollback Plan

If exchange enforcement causes issues:
1. `set_leverage()` returns `False` → `open_position()` aborts (safe failure)
2. Comment out the `set_leverage()` call in `open_position()` to revert to old behavior
3. The DSL/ROE simplifications are backward-compatible with `leverage` param

## Volume Quota Impact

- `update_leverage()` = 1 signed tx per open
- Opens are infrequent (max 3 concurrent, ~1-5 per day)
- Estimated impact: +1-5 tx/day out of ~50-100 quota budget
- Acceptable tradeoff for exchange-level enforcement

## Existing Positions

- BTC at 50× stays at 50× until closed
- `set_leverage()` only affects FUTURE opens on that market
- For existing positions: DSL still works using the `leverage` from `lighter_api.py` position fetch (line 187: `100 / initial_margin_fraction`)

---

## Edge Cases & Integration Issues Found

### ⚠️ EC-1: Existing Bot State Has Busted Leverage Values

**Current state file shows:**
```
SAMSUNG: lev=0.096, dsl_eff=0.096, dsl_lev=10.0
ADA:     lev=0.100, dsl_eff=0.100, dsl_lev=5.0
TRUMP:   lev=0.100, dsl_eff=0.100, dsl_lev=3.0
```

**Why:** Top-level `leverage` in saved state is `dsl_state.effective_leverage` = `notional/equity`. With $600+ equity and $50-60 positions, that's ~0.1x. These values get loaded back as `leverage` param on restart, poisoning `add_position()`.

**Impact on plan:** Phase 2 removes `effective_leverage` but we need backward-compatible state loading. Old state files will have `"effective_leverage": 0.096` — must ignore on load.

**Fix (add to Phase 2):** In `state_manager._restore_dsl_state()`:
```python
# BEFORE
pos.dsl_state.effective_leverage = dsl_data.get("effective_leverage", dsl_data.get("leverage", ...))

# AFTER — ignore effective_leverage on load, always use config default
# (exchange IMF is the real leverage now)
pos.dsl_state.leverage = dsl_data.get("leverage", self.cfg.default_leverage)
# If old state had leverage < 1 (was effective_leverage), replace with config default
if pos.dsl_state.leverage < 1.0:
    pos.dsl_state.leverage = self.cfg.default_leverage
```

### ⚠️ EC-2: Dashboard Uses Leverage in Exposure Calculation (BROKEN)

**File:** `dashboard/api/portfolio.py:72`
```python
leverage = pos.get("leverage", 1.0)
size_usd = entry * size * leverage   # BROKEN: leverage=0.1 → size_usd = 10% of notional
```

With current state (leverage≈0.1), dashboard shows positions at 1/10th their real size. This is a **pre-existing bug**, not caused by our change, but we should fix it.

**Fix (add to Phase 6):** Dashboard should use `entry * size` for notional (exposure). Leverage is display-only.
```python
# BEFORE
size_usd = entry * size * leverage

# AFTER
size_usd = entry * size  # Notional = position value. Leverage is for display.
```

Same fix needed in:
- `dashboard/api/portfolio.py:35-36` (top-level position calc)
- `dashboard/api/portfolio.py:123-124` (total exposure)

### ⚠️ EC-3: `update_leverage` Quota — It's a Free Slot

Lighter has a "free tx window" every 15 seconds for certain operations. `update_leverage` may be a free slot (same as `create_market_order` can be). The quota extraction from the response (`_extract_quota_from_response`) should work for `update_leverage` responses too since we process the same `RespSendTx` type.

**Worst case:** If it's NOT free, it costs 1 quota. With max 3-5 opens/day, that's negligible.

**Fix:** In `set_leverage()`, log whether the response has "didn't use volume quota" message to confirm.

### ⚠️ EC-4: State Serialization Round-Trip

**Current save** (`state_manager.py:44-45`):
```python
"leverage": dsl.leverage,
"effective_leverage": dsl.effective_leverage,
```

After removing `effective_leverage` from DSLState, we must still handle old state files that have it. The `save` side is simple (just stop writing `effective_leverage`). The `load` side needs to:
1. Read `"leverage"` from state (might be old effective_leverage ≈0.1, or new config leverage)
2. If `"effective_leverage"` key exists in saved data, it's an old state — ignore it
3. Fall back to config default if leverage < 1.0

### ⚠️ EC-5: Execution Engine Tick Loop — Effective Leverage Refresh

**Current code** (`execution_engine.py:93-98`): Recalculates `effective_leverage = notional / equity` for ALL positions every tick when balance changes.

**After Phase 2:** No more `effective_leverage`. But we still need to update `leverage` from exchange data. The tick loop should:
1. Fetch live positions from exchange (has `initial_margin_fraction`)
2. Update `dsl_state.leverage` from `100 / imf` (exchange-reported)
3. Remove the `notional / equity` calculation entirely

**Important:** The tick loop also runs for positions that were opened WITHOUT `set_leverage()`. Their IMF might be exchange-default, not our desired 10x. This is fine — we read whatever the exchange says.

### ⚠️ EC-6: Unverified Position Adoption

When an unverified position is confirmed on exchange (`execution_engine.py:118-126`):
```python
existing.dsl_state.effective_leverage = notional / self.tracker.account_equity
existing.dsl_state.leverage = pos.get("leverage", existing.dsl_state.leverage)
```

**After Phase 2:** Simplify to just read from exchange:
```python
if pos.get("leverage"):
    existing.dsl_state.leverage = pos["leverage"]
```

### ⚠️ EC-7: Position Adoption From Exchange (Cold Start / Manual Opens)

When the bot adopts a position it didn't open (`execution_engine.py:240-258`):
```python
tracker.add_position(mid, ..., leverage=pos_data.get("leverage"))
```

The `leverage` from exchange is `min(100/imf, default_leverage)`. For positions opened BEFORE we started calling `set_leverage()`, the IMF is exchange-default. This is correct — we read the actual leverage, not what we wish it was.

### ⚠️ EC-8: `lighter_api.py` IMF Parsing — The `min()` Cap

**Current code** (`lighter_api.py:187`):
```python
effective_leverage = round(min(100.0 / margin_fraction, self.cfg.default_leverage), 1)
```

This caps exchange leverage at `default_leverage`. So if exchange says 50× (IMF=200), we read `min(50, 10) = 10`. After `set_leverage(10)`, IMF will be 1000, so `100/1000 = 10`. The `min()` becomes redundant but harmless.

**Keep it** as a safety net in case IMF returns unexpected values.

### ⚠️ EC-9: Multiple Positions, Same Market

`set_leverage()` sets IMF per market, not per position. If you open BTC, close it, then open BTC again — the IMF from the first `set_leverage()` call persists on the exchange. We still call `set_leverage()` before every open (redundant but safe).

If you have multiple positions in the same market (Lighter allows this with cross margin), `set_leverage()` applies to all of them. This is correct behavior.

### ⚠️ EC-10: `update_leverage` Response — Quota Extraction May Fail

The `update_leverage()` SDK method returns `(tx_info, api_response, error)`. The `api_response` is a `RespSendTx` — same type as `create_market_order`. Our `_extract_quota_from_response()` should work, but we need to handle the case where `api_response` is `None` (SDK returns None on error path).

**Fix:** Guard in `set_leverage()`:
```python
if api_response:
    quota_val, _ = self._extract_quota_from_response(api_response)
    if quota_val is not None:
        self._update_quota_cache(quota_val)
```

---

## Updated Verification Checklist

After all phases:

### Code
- [ ] `open_position()` calls `set_leverage()` before `create_market_order()`
- [ ] `set_leverage()` aborts open on failure
- [ ] DSLState has single `leverage` field (no `effective_leverage`)
- [ ] All ROE calculations use `dsl_state.leverage`
- [ ] No `effective_leverage` references in production code (except state_manager backward compat)
- [ ] AI decision schema no longer includes `leverage`
- [ ] Safety layer no longer validates `leverage`
- [ ] Scanner doesn't output `leverage` in signals.json
- [ ] Dashboard uses `entry * size` for notional (not `* leverage`)

### State
- [ ] Old state files with `effective_leverage: 0.096` load correctly (ignored)
- [ ] New state files don't write `effective_leverage`
- [ ] Leverage < 1.0 on load → replaced with config default

### Integration
- [ ] Execution engine tick updates `dsl_state.leverage` from exchange IMF
- [ ] Unverified position confirmation reads leverage from exchange
- [ ] Position adoption from exchange uses exchange-reported leverage
- [ ] `log_outcome()` uses single leverage value (no fallback chain)
- [ ] `prompt_builder._calc_roe()` uses position's `leverage` field

### Tests
- [ ] All 310 tests pass (251 bot + 59 ai-decisions)
- [ ] Scanner tests pass
- [ ] test_dsl.py: all `effective_leverage=` → `leverage=`
- [ ] test_state_manager.py: backward compat tests for old state format
- [ ] test_position_tracker.py: remove effective_leverage tests
- [ ] test_safety.py: remove leverage validation tests

### Services
- [ ] All 3 services restart clean
- [ ] Dashboard shows correct position sizes
- [ ] Grep for `effective_leverage` returns 0 matches in production code
