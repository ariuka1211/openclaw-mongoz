# Position Sizing Plan — Fixed USD Per Position (REVISED)

**Date:** 2026-03-26 (Updated)
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

## Current Architecture Analysis

### DSL Leverage Sources (Critical Issue Found)
The plan missed a key detail: **where does the leverage value come from in different flows?**

Current sources:
1. `signal_handler.py:169` → `add_position(leverage=None)` → uses `cfg.default_leverage`
2. `execution_engine.py:149` → `add_position(leverage=pos.get("leverage"))` → uses exchange value
3. `executor.py:102` → `add_position(leverage=None)` → uses `cfg.default_leverage`

The plan said "pass actual leverage" but didn't specify HOW to get it in each flow.

### Exchange Leverage Extraction Points
- `lighter_api.py:185` — already extracts from `initial_margin_fraction` during position sync
- `verifier.py:54` — returns raw position dict from `get_positions()`, includes IMF
- Scanner — has `default_initial_margin_fraction` but we're changing the cap logic

---

## Revised Plan — Phases

### Phase 1: Config Changes (Safe)

**Files:** `bot/config.py`, `bot/config.yml`
- Remove: `default_leverage: float = 10.0`
- Add: `max_position_usd: float = 15.0`
- Add: `dsl_leverage: float = 10.0` (fallback for when exchange leverage unavailable)

### Phase 2: Position Sizing Caps (Safe)

**Files:** `bot/core/signal_handler.py`, `bot/core/executor.py`
- Add position cap at `cfg.max_position_usd` in both signal and AI flows
- NO leverage logic changes yet

### Phase 3: Remove Exchange Leverage Enforcement (Medium Risk)

**Files:** `bot/api/lighter_api.py`
- Remove `set_leverage()` call from `open_position()`

### Phase 4: DSL Leverage Sources (Complex — needs careful analysis)

**Problem:** Different flows need to extract leverage differently:

| Flow | File | Current Leverage | Needs to Extract From |
|---|---|---|---|
| Scanner signals | `signal_handler.py` | `cfg.default_leverage` | `verified_pos` after `verify_position_opened()` |
| AI decisions | `executor.py` | `cfg.default_leverage` | `verified_pos` after `verify_position_opened()` |
| Exchange sync | `execution_engine.py` | Exchange via IMF | Already works (keeps as-is) |
| State reload | `state_manager.py` | Saved value | Saved value (no change) |

**Files:** `signal_handler.py`, `executor.py`, `core/verifier.py`
- Modify `verify_position_opened()` to extract and return leverage from IMF
- Update signal + AI flows to pass extracted leverage to `add_position()`

### Phase 5: Scanner Position Cap (Low Risk)

**Files:** `scanner/src/config.ts`, `scanner/src/position-sizing.ts`
- Add `maxPositionUsd: 15` cap

### Phase 6: Tests (Critical)

**Files:** All test files
- Update `default_leverage` references → `dsl_leverage` 
- Add `max_position_usd` validation tests
- Update position sizing tests
- Verify DSL leverage sources work correctly

---

## Key Insight: `verify_position_opened()` Enhancement

The missing piece: `verify_position_opened()` returns a raw position dict but doesn't extract leverage. We need to enhance it:

```python
# Current
verified_pos = {"size": 0.1, ...}  # raw dict

# Enhanced  
verified_pos = {"size": 0.1, "leverage": 10.0, ...}  # with extracted leverage
```

This way both signal and AI flows can get actual exchange leverage after verification.

---

## Risk Assessment Updated

- **Phase 1-2**: Low risk — pure additions, no behavior change
- **Phase 3**: Medium risk — removes exchange leverage enforcement 
- **Phase 4**: High risk — changes DSL leverage sources, affects ROE calculations
- **Phase 5**: Low risk — scanner cap only
- **Phase 6**: Critical — tests prevent regressions

**Phase 4 is the most complex** and needs careful subagent verification since it touches DSL calibration.

---

## Implementation Strategy

1. **Phase 1-2 together** (safe config + sizing caps)
2. **Phase 3 standalone** (remove set_leverage)
3. **Phase 4 with heavy verification** (DSL leverage sources)
4. **Phase 5 standalone** (scanner)
5. **Phase 6 throughout** (tests with each phase)

Each phase gets its own subagent with verification of:
- All tests pass
- No unexpected behavior changes
- Leverage values are correct in each flow