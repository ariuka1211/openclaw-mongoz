# Session — 2026-03-24 22:31 MDT

## What Happened
John asked for a full reassessment of cross margin / DSL / ROE logic after the recent fixes. Conducted end-to-end audit.

## Critical Bug Found & Fixed
**DSL was using config leverage (5x/10x) instead of effective leverage (notional/equity) for ROE calculations.** This caused DSL to be ~3x too aggressive — triggering tiers and locking floors based on inflated ROE numbers.

Example: ETHFI with $100 notional on $59.62 equity → effective leverage 1.68x, not 5x.

## Changes Made
### dsl.py
- Added `effective_leverage` field to `DSLState` (default 10.0 for backwards compat)
- `current_roe()` now uses `self.effective_leverage` instead of `self.leverage`
- Hard SL uses `effective_leverage` + 0.001% floating point tolerance

### bot.py
- `add_position()`: calculates effective_leverage = notional / equity when creating DSL state
- Equity refresh: recalculates effective_leverage for ALL positions on balance update
- `_write_ai_result()` + `_refresh_position_context()`: write `effective_leverage` to ai-result.json
- State save/restore: persists `effective_leverage` with backwards compat fallback
- Position reconcile: recalculates effective_leverage on exchange sync
- Tier lock alert: uses effective_leverage for ROE→price conversion

### context_builder.py (AI trader)
- Already correct — `_calc_roe()` computes effective_leverage from notional/equity independently

## Other Finding
- Per-position `sl_pct` (set by AI) is dead code when DSL is enabled — DSL uses config default 1.25% for all positions. Could wire AI's stop_loss_pct to override DSL hard SL per-position in future.

## Bot Status
- 6 positions running: ENA, APT, BCH, ETHFI, RIVER, MON
- All longs except RIVER and MON (shorts)
- Changes NOT yet applied — bot needs restart
- 2 files changed: dsl.py, bot.py

## Next Steps
- Restart bot to apply effective leverage fixes
- Consider wiring per-position sl_pct to DSL hard SL
- Monitor that DSL tiers now fire at correct ROE levels
