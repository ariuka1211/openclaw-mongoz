# Session Handoff — 2026-04-05 (wrap)

## What happened
- User asked about portfolio status. Grid bot was running at ~$85 equity.
- Found 5 grid rolls in one session, -$0.81 realized PnL across 11 trades
- Diagnosed 3 root causes in `core/grid_manager.py` and fixed all

## 3 Critical Bugs Fixed (commit 18822ee, pushed)
1. **Orphan sell detection** — sells filling without BTC create phantom shorts on perp exchange. Now checks `position_size_btc >= size_per_level` before processing sell fill. Logs warning, records to `orphan_sells`, skips PnL tracking + replacement.
2. **USDC balance tracker** — replaced broken FIFO matching with `usdc_spent`/`usdc_received` running totals as source of truth for `realized_pnl` (long grid).
3. **Max rolls per session** — added `max_rolls_per_session` config key (default: 2). Rolls cap hit → skip roll, wait for daily reset.

## Yesterday's actual PnL (Apr 4)
- Deployment 12:40 UTC → 16:16 UTC: Realized -$0.17 (2 trades, 0 win)
- Deployment 16:16 UTC → 19:15 UTC: Realized -$0.17 (2 trades, 0 win)
- Deployment 19:15 UTC → 04:22 UTC: Realized -$0.70 (3 trades, 0 win)
- Last deployment 07:26 UTC → no result fills yet (bot restarted with fixes)

## Current State
- Bot: active, restarted with fixes (09:43 MST)
- Equity: $85.28 at reset
- Small short position detected and recovered at startup
- Running clean, no orphan sells since fix
- Branch: main (pushed, clean)

## Stress test framework
- Working version at `tools/stress_test.py` — pure Python simulation
- 6 scenarios with realistic price paths
- Uses real `calculate_grid()` from core.calculator
- Results show ~5% worst-case drawdown, safety systems work

## Issues NOT fixed (left for future)
- Grid rolls happening too frequently (5x in one session) — now limited to 2
- PnL tracking was showing wrong numbers — now uses USDC tracker
- Position recovery on restart worked but caused brief short exposure
