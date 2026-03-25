# Session — 2026-03-25 11:29-11:39 MDT

## What Happened
- Investigated `bot/scanner/` folder — confirmed it was orphaned dead code from the refactor
- Audited top-level `scanner/` folder — found 3 dead files (correlation-guard.ts, funding-monitor.ts, cleanup-old-signals.sh)
- Moved dead files to `archives/` (both `bot/scanner/` → `archives/bot-scanner/` and scanner dead files → `archives/scanner/`)
- Relocated `oi-snapshot.json` from repo root to `signals/` + updated path ref in opportunity-scanner.ts
- Deleted outdated `refactor-plan.md`
- Restarted scanner service — running clean

## Pushed
- `4b4b5f6` — scanner cleanup: archive dead files, relocate oi-snapshot.json, update path refs

## Pending
- Backtesting implementation (triple barrier method) — still not started
- Uncommitted equity/SL fixes from earlier sessions — need checking if already on main
