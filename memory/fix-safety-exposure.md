# Fix: Safety Exposure Check — Notional vs Margin Bug

**Date:** 2026-03-27
**File:** `ai-decisions/safety.py` — `_validate_open` method

## Problem

The exposure check mixed notional values with cash margin:
- `position_size_usd` = `size × entry_price` = NOTIONAL (e.g., $80,000 for 1 BTC at $80K)
- `new_exposure` = `equity × size_pct / 100` = CASH MARGIN (e.g., $500 for 5% of $10K)
- Adding them together overestimates exposure by the leverage factor

## Before (Buggy)

```python
current_exposure = sum(
    abs(p.get("position_size_usd", p.get("size_usd", 0))) for p in positions
)
new_exposure = equity * size_pct / 100
total_exposure_pct = (current_exposure + new_exposure) / equity * 100
```

## After (Fixed)

```python
# Guard: can't calculate exposure without valid equity
if equity <= 0:
    reasons.append("cannot calculate exposure: equity <= 0")
else:
    # Convert each position's notional to margin used = notional / leverage
    current_margin = 0.0
    for p in positions:
        notional = abs(p.get("position_size_usd", p.get("size_usd", 0)))
        leverage = p.get("leverage", 1) or 1  # DB fallback positions may lack leverage
        current_margin += notional / leverage
    new_margin = equity * size_pct / 100
    total_exposure_pct = (current_margin + new_margin) / equity * 100
    if total_exposure_pct > self.max_total_exposure_pct:
        reasons.append(
            f"total exposure {total_exposure_pct:.1f}% > max {self.max_total_exposure_pct}%"
        )
```

## Test Scenarios

### Scenario 1: Bug trigger case (10x leverage, $10K equity)
- Existing position: 0.1 BTC at $80K → notional = $8,000, leverage = 10x → margin = $800
- New position: 5% of equity → margin = $500
- **Before:** ($8,000 + $500) / $10,000 × 100 = 85% → **BLOCKED** ❌
- **After:** ($800 + $500) / $10,000 × 100 = 13% → **PASSES** ✅

### Scenario 2: Leverage = 1 (spot-like, no leverage)
- Existing position: 0.1 BTC at $80K → notional = $8,000, leverage = 1 → margin = $8,000
- New position: 5% of equity → margin = $500
- **After:** ($8,000 + $500) / $10,000 × 100 = 85% → **BLOCKED** ✅ (correct behavior)

### Scenario 3: Multiple positions (10x leverage each)
- Position A: 0.05 BTC at $80K → margin = $400
- Position B: 0.02 ETH at $3K → margin = $60
- New position: 5% → margin = $500
- **After:** ($400 + $60 + $500) / $10,000 × 100 = 9.6% → **PASSES** ✅

### Scenario 4: equity <= 0 guard
- equity = 0 or equity < 0
- **After:** "cannot calculate exposure: equity <= 0" → **BLOCKED** ✅

### Scenario 5: DB fallback positions (no leverage field)
- Position from DB fallback has `leverage` = None (or missing)
- `leverage = p.get("leverage", 1) or 1` → defaults to 1 (conservative)
- Margin = notional / 1 = notional (treats as unleveraged) → **conservative** ✅

## Other Code Paths — Not Affected

1. **Daily drawdown check** — has its own `if equity > 0` guard, unchanged
2. **`_validate_close`** — doesn't do exposure math, unchanged
3. **`validate` (entry point)** — unchanged, passes through to `_validate_open`
4. **`check_kill_switch`** — unrelated to position exposure, unchanged
5. **`get_daily_drawdown`** — unchanged

## Leverage Source

Positions come from `bot/core/result_writer.py` which sets:
```python
"leverage": pos.dsl_state.leverage if pos.dsl_state else cfg.dsl_leverage
```
This field is always present for bot-written positions. DB fallback positions in `data_reader.py` lack this field, hence the `or 1` default.
