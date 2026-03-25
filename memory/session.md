# Session — 2026-03-25 10:10-10:38 MDT

## What Happened
- Major directory restructure of autopilot-trader. Flattened `signals/` into top-level services.
- Executed in 5 phases with subagents:
  1. Moved scanner → `scanner/`
  2. Moved ai-trader Python files → `ai-trader/`
  3. Moved IPC files → `ipc/`, updated all path references across codebase
  4. Moved Flask dashboard → `dashboard/`, deleted embedded FastAPI dashboard
  5. Deleted `signals/` dir, updated systemd units, restarted services
- Renamed directories for clarity:
  - `ai-trader/` → `ai-decisions/`
  - `executor/` → `bot/`
- Fixed stale `signals/ai-trader/` paths in dashboard API modules (trader.py, system.py) that survived the refactor
- Restored `prompts/` directory (system.txt, decision.txt) that was left behind during ai-trader move
- All 3 services restarted clean, zero errors

## Final Structure
```
autopilot-trader/
├── ai-decisions/    # LLM decision engine
├── bot/             # Trading bot executor
├── dashboard/       # Flask web dashboard
├── docs/
├── ipc/             # signals.json, ai-decision.json, ai-result.json
├── scanner/         # Signal scoring (Bun/TS)
└── shared/          # ipc_utils.py
```

## Decisions
- Kept Flask dashboard, removed FastAPI (embedded in old ai-trader)
- Kept systemd service names unchanged (ai-trader.service, lighter-bot.service) — only updated paths
- Left Python logger names as "ai-trader.*" (namespace strings, not paths)

## Pending
- Backtesting implementation (triple barrier method) — not started
- Commit and push all changes
