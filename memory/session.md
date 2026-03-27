# Session Handoff — 2026-03-26 22:42 MDT

## ✅ COMPLETED: Telegram Alert ROE/PnL Leverage Fix

### Problem Found
Telegram alerts and DSL logic used hardcoded `cfg.dsl_leverage` (10.0) for ROE calculations instead of actual exchange leverage. Positions at 25x showed ROE 2.5x too low; at 3x showed 3.3x too high. DSL tier triggers, stagnation, and hard SL all miscalibrated.

### Root Cause
Both signal_handler.py and executor.py fetched real exchange leverage via `get_market_leverage()` for the margin cap, but then passed `leverage=cfg.dsl_leverage` (10.0) to `add_position()`.

### Fix Applied
Changed all 4 `add_position()` calls to use `actual_leverage` instead of `cfg.dsl_leverage`:
- `signal_handler.py` lines 158, 176
- `executor.py` lines 104, 120

### Verified
- `grep cfg.dsl_leverage` on both files → 0 hits
- `grep actual_leverage` → 10 hits (4 fixed + 6 original)
- 105 tests pass, 1 pre-existing failure (unrelated)
- Bot restarted via systemctl, running clean

### Open Items
None.

## Session Context
- Branch: fix/leverage-in-alerts
- No other changes on working tree except memory files
