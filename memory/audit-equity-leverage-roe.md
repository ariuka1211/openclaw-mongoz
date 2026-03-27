# Audit Report: Equity, Leverage & ROE Calculations

**Date:** 2026-03-27  
**Scope:** Equity refresh, leverage application, ROE math across execution_engine.py, shared_utils.py, dsl.py, position_tracker.py, executor.py, safety.py, config.py

---

## 1. ROE Calculation — Do All Modules Agree?

### Verdict: ✅ Consistent formula, minor fallback divergence

All three core ROE implementations compute the same thing:

| Module | Formula | Default Leverage |
|--------|---------|-----------------|
| `_pnl_info` (execution_engine.py:29-41) | `roe = (pnl_usd / notional_usd * 100) * leverage` | `10.0` (hardcoded) |
| `log_outcome` (shared_utils.py:79-81) | `roe = pnl_pct * leverage` | `cfg.dsl_leverage` (default 10.0) |
| `DSLState.current_roe` (dsl.py:85-92) | `roe = price_move_pct * leverage` | `10.0` (field default) |

All three are mathematically equivalent: `roe_pct = (price_change / entry_price) * 100 * leverage`.

**Minor divergence:** `_pnl_info` falls back to hardcoded `10.0` while `log_outcome` falls back to `cfg.dsl_leverage`. Both are 10.0 by default, but if someone changes `dsl_leverage` in config, `_pnl_info` wouldn't pick it up. Low severity since it only affects alert display for positions without DSL state.

**Post-close inline ROE in `_process_position_tick`** (execution_engine.py, lines ~260-270 and ~400-410) duplicates the `_pnl_info` formula inline rather than calling the method. This is consistent but redundant — a maintenance risk if someone changes the formula in one place but not the other.

---

## 2. Leverage Application — Is It Always Exchange-Enforced?

### Verdict: ⚠️ Mostly correct, but fallback paths use config default

**Primary flow (correct):**
1. `execute_ai_open` (executor.py:80): `actual_leverage = await api.get_market_leverage(market_id)` — fetches real exchange leverage
2. Passed to `tracker.add_position(..., leverage=actual_leverage)`
3. `add_position` (position_tracker.py:90): `lev = leverage or self.cfg.dsl_leverage` — creates `DSLState` with that leverage
4. All subsequent ROE calculations use that stored leverage

**Fallback paths (risk of wrong leverage):**

- **API-detected positions** (execution_engine.py:123): `self.tracker.add_position(..., leverage=pos.get("leverage"))` — if exchange API doesn't return leverage, falls back to `cfg.dsl_leverage` (10.0). This could differ from actual exchange leverage.

- **Unverified position adoption** (execution_engine.py:100-101): `existing.dsl_state.leverage = pos.get("leverage")` — updates from API. Good, but same fallback issue if API doesn't include leverage.

- **`DSLState.leverage` field default** (dsl.py:65): `leverage: float = 10.0` — if DSLState is ever created without explicit leverage, it defaults to 10.0. Currently all creation paths provide leverage explicitly, so this is just a safety net.

---

## 3. ROE Consistency Across `_pnl_info`, `log_outcome`, and `current_roe`

### Verdict: ✅ They agree on meaning, ⚠️ alert code has a subtle duplicate

All three define ROE as: **return on margin deposit = raw price movement % × leverage**.

**One inconsistency in executor.py post-close alert** (executor.py:149-156): After a successful AI open with verification, the code calculates ROE inline:
```python
roe_pct = pnl_pct_val * leverage
```
But then **in the same block**, the alert shows the position as just opened — meaning ROE is always 0% (just opened). The variable is computed but shouldn't be needed here. Not a bug, but confusing code.

**Post-close alert in `execute_ai_close`** (executor.py:212-214):
```python
roe = ((exit_price - pos.entry_price) / pos.entry_price * 100)
```
This is **raw price movement %, NOT ROE** — it's missing the leverage multiplication. The alert labels it as "ROE" but shows raw pnl_pct. This is **misleading** — a 2% price move at 10x leverage shows as "ROE: 2.0%" when it should be "ROE: 20.0%".

**Same bug in `execute_ai_close_all`** (executor.py:281) and the failure alert path in `execute_ai_close` (executor.py:187-191).

**Severity:** 🟡 **Medium** — alerts show wrong ROE values (off by a factor of leverage). The actual trading logic is correct; only display/alert text is affected.

---

## 4. Margin Cap Calculation in `execute_ai_open`

### Verdict: ✅ Correct

```python
balance = await bot._get_balance()
actual_leverage = await api.get_market_leverage(market_id)
max_notional = balance * cfg.max_margin_pct * actual_leverage
```

**Math check:**
- `max_margin_pct` = 0.15 (15% of equity allowed for margin)
- Max margin per position = `balance * 0.15`
- Max notional = max_margin × leverage = `balance * 0.15 * actual_leverage`
- If `size_usd` (notional from AI) exceeds this, it's capped

This is **correct**. Example with $10,000 balance and 10x leverage:
- Max margin = $1,500
- Max notional = $1,500 × 10 = $15,000
- The bot caps the order at $15,000 notional

**However:** The cap doesn't account for existing positions. If 2 positions already use the full margin budget, a 3rd position could still get the full cap. The safety layer's `max_total_exposure_pct` is supposed to catch this, but see §5 for the mismatch.

---

## 5. Safety Exposure Check Mismatch

### Verdict: 🔴 **Bug** — Notional and cash are mixed in the same sum

In `safety.py:_validate_open` (lines 122-128):
```python
current_exposure = sum(
    abs(p.get("position_size_usd", p.get("size_usd", 0))) for p in positions
)
new_exposure = equity * size_pct / 100
total_exposure_pct = (current_exposure + new_exposure) / equity * 100
```

**The problem:**
- `position_size_usd` (from result_writer.py:35) = `pos.size * pos.entry_price` = **NOTIONAL** (margin × leverage)
- `new_exposure = equity * size_pct / 100` = **CASH MARGIN** (size_pct is % of equity, not % of notional)

These are **inconsistent units** being added together.

**Example:** $10,000 equity, 10x leverage, `size_pct_equity = 5`:
- `new_exposure` = $500 (cash)
- If one existing position has $5,000 notional: `current_exposure` = $5,000
- `total_exposure_pct` = ($5,000 + $500) / $10,000 × 100 = 55% ← looks high
- But actual cash margin used = $500 (existing) + $500 (new) = $1,000 = 10% of equity

The check **overestimates** exposure when positions have leverage > 1x, making it overly conservative. This means the safety layer is **more restrictive than intended** — it may block valid trades. Not a security risk (fails safe), but it defeats the purpose of the `max_total_exposure_pct` limit.

**Fix:** Either convert both to notional (`new_exposure = equity * size_pct / 100 * estimated_leverage`) or both to margin (`current_exposure = sum(margin_used)`).

---

## 6. Hardcoded Leverage = 10.0

### Verdict: ⚠️ Two locations with hardcoded 10.0, both in fallback paths

| Location | Context | Severity |
|----------|---------|----------|
| execution_engine.py:38 `_pnl_info` | `leverage = pos.dsl_state.leverage if pos.dsl_state else 10.0` | Low — only for positions without DSL state (legacy mode) |
| dsl.py:65 `DSLState.leverage` | `leverage: float = 10.0` (field default) | Low — safety net, all creation paths provide explicit leverage |
| config.py:80 `dsl_leverage` | `dsl_leverage: float = 10.0` (config default) | Expected — this is the intended default |

The `10.0` in `_pnl_info` is the most concerning because it directly affects alert math for legacy (non-DSL) positions. If the exchange uses different leverage for such positions, the displayed ROE will be wrong. In practice, DSL is enabled by default (`dsl_enabled: bool = True`), so most positions have DSLState with the correct exchange leverage.

---

## 7. Edge Cases

### Division by zero ✅ Protected

- `_pnl_info`: Guards with `if pos.entry_price <= 0 or pos.size <= 0` → returns zeros
- `log_outcome`: Guards with `if notional_usd > 0`
- `DSLState.current_roe`: Guards with `if self.entry_price <= 0` → returns 0.0
- `execute_ai_open` margin cap: `balance` could be 0, making `max_notional = 0`. This would cap all positions to 0 size. The `api.open_position` call would likely reject it. No crash, but could silently fail opens if balance query returns 0.

### Negative equity ⚠️ Not handled

- `tracker.account_equity` is set from `bot._get_balance()` without checking if it's negative
- A negative balance (from liquidation or exchange debt) would make `max_notional` negative in `execute_ai_open`, which would fail to open any positions. Safely fails closed.
- Safety layer `equity` parameter defaults to 1000.0 if not provided. If 0 or negative equity is passed, `total_exposure_pct` division would produce inf/nan. The `get_daily_drawdown` method (safety.py:161) guards with `if equity <= 0: return 0.0`, but `_validate_open` does NOT guard against zero equity before dividing.

### Zero leverage ⚠️ Theoretically problematic

- `DSLState.leverage` defaults to 10.0, so zero leverage is unlikely
- But if `api.get_market_leverage()` returned 0, `execute_ai_open` would pass 0 to `add_position`, creating DSLState with leverage=0
- `current_roe` with leverage=0 always returns 0, making DSL tier triggers never fire
- `hard_sl_roe = -abs(cfg.hard_sl_pct) * 0 = 0` — the hard SL would trigger immediately (roe ≤ 0)
- This is a defensive gap: no validation that leverage > 0 before using it

### Floating point tolerance ⚠️ Minimal

- `DSLState.evaluate_dsl` (dsl.py:115): `if roe <= hard_sl_roe + 0.001` — small tolerance, reasonable
- No other explicit tolerance checks. ROE comparisons are direct `>` / `<` without epsilon. Could cause flickering tier transitions at boundary prices, but the `consecutive_breaches` mechanism (needs 2-3 breaches) mitigates this.

---

## Summary of Findings

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | Safety exposure check mixes notional + cash | 🔴 High | safety.py:122-128 |
| 2 | Post-close alerts show raw pnl_pct as "ROE" (missing ×leverage) | 🟡 Medium | executor.py:187,212,281 |
| 3 | Redundant inline ROE calculation (not using `_pnl_info`) | 🟡 Low | execution_engine.py:260-270 |
| 4 | `_pnl_info` hardcoded 10.0 vs `log_outcome` cfg.dsl_leverage fallback | 🟢 Low | execution_engine.py:38 vs shared_utils.py:79 |
| 5 | No validation that leverage > 0 before creating DSLState | 🟢 Low | position_tracker.py:91 |
| 6 | No zero-equity guard in `_validate_open` exposure calc | 🟢 Low | safety.py:125-128 |
| 7 | Safety cap more restrictive than intended (fails safe) | ℹ️ Info | safety.py exposure logic |

### Recommended Fixes (priority order)

1. **Fix safety.py exposure mismatch** — convert `new_exposure` to notional: `equity * size_pct / 100 * estimate_leverage` where `estimate_leverage` is the config default or actual leverage if available. Or convert `current_exposure` to margin equivalents.

2. **Fix post-close ROE alerts in executor.py** — multiply by leverage: `roe = pnl_pct * pos.dsl_state.leverage` instead of just `roe = pnl_pct`. Or use `_pnl_info()` for consistency.

3. **Add leverage > 0 guard** in `add_position` or `DSLState.__post_init__`.

4. **Add equity > 0 guard** in `_validate_open` before computing `total_exposure_pct`.
