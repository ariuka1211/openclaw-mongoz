# Position Sizing Plan — Fixed USD Per Position

**Date:** 2026-03-26
**Goal:** Cap each position at a fixed USD notional value. Leverage is irrelevant — we control exposure purely by position size.

## Core Rule

```
Each position's notional value = max_position_usd (default $15)
Margin locked = max_position_usd / exchange_leverage
DSL ROE = price_move_pct × actual_exchange_leverage
```

- At 10x leverage: margin = $1.50 per position, 2% price move = 20% ROE
- At 5x leverage: margin = $3.00 per position, 2% price move = 10% ROE
- At 3x leverage: margin = $5.00 per position, 2% price move = 6% ROE

The exchange decides leverage. We decide how much USD to put on the line.
DSL uses actual exchange leverage for ROE calculations so tiers are calibrated per-market.

---

## Current Architecture (what we built in Phases A-D)

### Scanner (TypeScript)
- `config.ts`: `maxLeverageCap: 20` — hard leverage cap
- `position-sizing.ts:61`: `maxAllowedPosition = accountEquity * maxLeverage` — leverage-based cap
- Scanner uses exchange IMF to compute `exchangeMaxLeverage = 10000 / default_initial_margin_fraction`
- Scanner's `positionSizeUsd` is risk-based but capped at `equity × maxLeverage`

### Bot (Python) — 3 key entry points for sizing

| Entry Point | File | Current Sizing Logic |
|---|---|---|
| Scanner signals | `signal_handler.py:128` | `size_usd = opp.positionSizeUsd * scale` (no hard cap) |
| AI decisions | `executor.py:38-44` | `max_size = balance * 10` (10x equity = 1000% — WRONG) |
| Exchange enforcement | `lighter_api.py:470` | `set_leverage(market_id, 10.0)` before every open |

### DSL
- `dsl.py:51`: `leverage: float = 10.0` — used for ROE calculation (`move * leverage`)
- `dsl.py:101`: `hard_sl_roe = -abs(hard_sl_pct) * leverage` — DSL thresholds in ROE terms
- Leverage here is for DSL tier calibration, NOT position sizing. Must stay.

### AI Decisions
- Leverage removed from decision schema (Phase C) — no changes needed

### Dashboard
- Shows `leverage` from position data — cosmetic, can stay

---

## What Changes

### 1. Config — Replace `default_leverage` with two fields

**File:** `bot/config.py`
- Remove: `default_leverage: float = 10.0` (as a sizing concept)
- Add: `max_position_usd: float = 15.0` — hard cap per position notional
- Add: `dsl_leverage: float = 10.0` — ONLY for DSL ROE calculations
- Validate: `max_position_usd > 0`, `dsl_leverage >= 1`

`default_leverage` rename to `dsl_leverage` makes it obvious this value is ONLY for DSL ROE calculations (`current_roe()`, trailing stops), not for position sizing or exchange enforcement.

**File:** `bot/config.yml`
- Replace `default_leverage: 10.0` with:
  ```yaml
  max_position_usd: 15.0    # Each position notional cap
  dsl_leverage: 10.0         # DSL ROE calc only (not position sizing)
  ```

### 2. Bot — Enforce position cap in both entry points

**File:** `bot/core/signal_handler.py`
- After line 128 (`size_usd = opp.positionSizeUsd * scale`), add cap:
  ```python
  if size_usd > cfg.max_position_usd:
      size_usd = cfg.max_position_usd
      logging.info(f"📐 Capped position to ${cfg.max_position_usd:.2f}")
  ```

**File:** `bot/core/executor.py`
- Line 38-44: Change from `balance * 10` (10x equity) to fixed cap:
  ```python
  max_size = bot.cfg.max_position_usd
  if size_usd > max_size:
      size_usd = max_size
  ```

### 3. Remove `set_leverage()` from open flow

**File:** `bot/api/lighter_api.py`
- Line 470: Remove the `set_leverage()` call from `open_position()` entirely
- Lines 419-465: Keep the `set_leverage()` method (available if needed later), just don't call it

### 4. DSL — Use actual exchange leverage (not config)

**File:** `bot/dsl.py`
- `DSLState.leverage` stays — but now receives exchange's actual leverage, not config value
- `current_roe()` uses `self.leverage` — correct, no change
- `hard_sl_roe` uses `state.leverage` — correct, no change

**File:** `bot/core/position_tracker.py`
- `add_position()` receives actual leverage from exchange and passes to DSL
- When opening: leverage comes from signal/exchange data (`initial_margin_fraction → 100/IMF`)
- When resuming (state reload): use saved leverage from state file

**File:** `bot/core/signal_handler.py`
- After `verify_position_opened()`: extract `initial_margin_fraction` from verified position
- Compute: `actual_leverage = 100 / IMF` (or fallback to `cfg.dsl_leverage`)
- Pass actual leverage to `tracker.add_position()`

**File:** `bot/core/execution_engine.py`
- When syncing positions from exchange: extract actual leverage from `initial_margin_fraction`
- Pass to `tracker.add_position()`

**File:** `bot/config.py`
- `dsl_leverage` becomes FALLBACK ONLY — used when exchange leverage can't be determined (e.g., old state files, exchange API errors)

### 5. Scanner — Position cap by fixed USD

**File:** `scanner/src/config.ts`
- Add: `maxPositionUsd: 15` (fixed cap per position)

**File:** `scanner/src/position-sizing.ts`
- Line 62: After `positionSizeUsd = riskAmountUsd / stopLossDistancePct`, add cap:
  ```typescript
  positionSizeUsd = Math.min(positionSizeUsd, CONFIG.maxPositionUsd);
  ```
- The existing `maxLeverage` / `exchangeMaxLeverage` computation can stay for info, but the sizing cap is purely USD-based.

### 6. Tests — Update for new naming

- Rename `default_leverage` references to `dsl_leverage` in test configs
- Add test for `max_position_usd` validation (> 0)
- Add test that position sizing cap works (signal_handler + executor)
- Keep `set_leverage` tests (method stays, just not called from `open_position`)

---

## What DOES NOT Change

| Component | Why |
|---|---|
| `DSLState.leverage` field | DSL ROE math still needs it — just gets actual exchange value now |
| `current_roe()` | Same formula: `move * leverage` — just uses real leverage |
| `hard_sl_roe` | Same formula: `-hard_sl_pct * leverage` — calibrated correctly per market |
| AI decision schema | Already clean (Phase C) |
| `prompt_builder.py` leverage fallback | Minor — just a display fallback |
| Dashboard `portfolio.py` | Shows exchange-reported leverage — cosmetic |

---

## File Change Summary

| File | Action |
|---|---|
| `bot/config.py` | Remove `default_leverage`, add `max_position_usd` + `dsl_leverage` (fallback) |
| `bot/core/signal_handler.py` | Cap position at `cfg.max_position_usd`, pass actual exchange leverage to tracker |
| `bot/core/executor.py:38-44` | Change cap from `balance * 10` to `cfg.max_position_usd` |
| `bot/core/execution_engine.py` | Extract actual leverage from exchange, pass to tracker |
| `bot/api/lighter_api.py:469-471` | Remove `set_leverage()` call from `open_position()` |
| `bot/config.yml` | Replace `default_leverage` with `max_position_usd` + `dsl_leverage` |
| `scanner/src/config.ts` | Add `maxPositionUsd: 15` |
| `scanner/src/position-sizing.ts` | Add cap: `min(positionSizeUsd, maxPositionUsd)` |
| Tests (bot + scanner) | Update config field names, add sizing cap tests, update leverage sources |

---

## Risk Assessment

- **`dsl_leverage` rename**: Low risk — rename only, DSL behavior unchanged
- **`max_position_usd` cap**: Low risk — additional safety check on top of existing logic
- **Remove `set_leverage()`**: Medium risk — exchange uses its own default IMF. But this is what you want (exchange decides leverage).
- **Actual exchange leverage for DSL**: Medium risk — DSL tiers now calibrate per-market. A 5x market triggers stops at different price levels than 10x. This is correct behavior but different from before.
- **Scanner cap change**: Low risk — same formula, different cap mechanism
- **$15 limit**: Risk depends on account size — on $60 = 25% per position, on $500 = 3%. Adjust as needed.

---

## Example: How Sizing + ROE Work After

**Scenario:** $60 equity, BTC signal

1. Scanner computes `positionSizeUsd = $2500` (risk-based)
2. Scanner caps at `min($2500, $15) = $15` → sends $15
3. Bot receives `$15`, opens position
4. Exchange IMF = 10% → actual_leverage = 100/10 = **10x**
5. Margin locked: $15 / 10 = **$1.50**
6. DSL gets `leverage=10.0` (from exchange, not config)

**DSL ROE after -1.25% price drop:**
- `ROE = -1.25% × 10 = -12.5%`
- `hard_sl_roe = -1.25% × 10 = -12.5%` → triggers hard stop loss

**If same $15 on a 5x ALT:**
- Exchange IMF = 20% → actual_leverage = 5x
- Margin locked: $15 / 5 = **$3.00**
- DSL gets `leverage=5.0`

**DSL ROE after -1.25% price drop:**
- `ROE = -1.25% × 5 = -6.25%`
- `hard_sl_roe = -12.5%` → NOT triggered yet (needs -2.5% price move)

This is correct: lower leverage = more margin = more room before liquidation = stops trigger at wider price levels.
