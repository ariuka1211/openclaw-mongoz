# Refactor Plan: Flatten autopilot-trader into top-level services

## Problem
`signals/` is a catch-all containing 3 separate services + IPC files + two competing dashboards (FastAPI embedded in ai-trader, Flask standalone).

## Current Structure
```
autopilot-trader/
├── executor/           # bot.py (clean, mostly self-contained)
├── shared/             # ipc_utils.py
├── signals/
│   ├── scripts/        # scanner (TS/Bun) ← service #1
│   ├── ai-trader/      # ai_trader.py + context_builder + etc ← service #2
│   │   ├── dashboard.py      # FastAPI dashboard (embedded)
│   │   ├── static/index.html # Dashboard frontend
│   │   └── state/            # AI trader state
│   ├── dashboard/      # Flask dashboard ← service #3 (separate app)
│   │   ├── app.py
│   │   ├── api/        # portfolio, scanner, trader, system routers
│   │   └── index.html
│   ├── signals.json    # IPC
│   ├── ai-decision.json # IPC
│   └── ai-result.json  # IPC
```

## Target Structure
```
autopilot-trader/
├── executor/           # bot.py — stays
├── scanner/            # opportunity-scanner.ts, scripts/ — moved from signals/scripts/
├── ai-trader/          # ai_trader.py, context_builder, etc — moved from signals/ai-trader/
├── dashboard/          # ONE dashboard app — merge both into Flask version
│   ├── app.py
│   ├── api/
│   ├── static/
│   └── index.html
├── shared/             # ipc_utils.py — stays, add path constants
├── ipc/                # signals.json, ai-decision.json, ai-result.json
└── docs/
```

## Decision: Which Dashboard to Keep?
- `signals/dashboard/` (Flask) is the unified dashboard with modular API routers — **KEEP**
- `signals/ai-trader/dashboard.py` (FastAPI) is embedded, limited — **REMOVE**
- `signals/ai-trader/static/index.html` — move useful parts to `dashboard/static/` if any unique content, then delete

## Step-by-Step Plan

### Step 1: Create new directories
- `scanner/`, `ai-trader/`, `ipc/`, `dashboard/static/`

### Step 2: Move scanner files
- `signals/scripts/*.ts` → `scanner/`
- `signals/scripts/*.sh` → `scanner/`
- `signals/scanner-daemon.sh` → `scanner/`
- Update hardcoded paths in scanner-daemon.sh (LOG path, cd path)
- Update `signals.json` write path in opportunity-scanner.ts (change from relative `signals.json` to `../ipc/signals.json` or use env var)

### Step 3: Move ai-trader files
- `signals/ai-trader/*.py` → `ai-trader/`
- `signals/ai-trader/config.json` → `ai-trader/`
- `signals/ai-trader/state/` → `ai-trader/state/`
- Update config.json paths:
  - `"../signals.json"` → `"../ipc/signals.json"`
  - `"../ai-decision.json"` → `"../ipc/ai-decision.json"`
  - `"../ai-result.json"` → `"../ipc/ai-result.json"`
- Remove `dashboard.py` from ai-trader (use separate dashboard service)
- Remove `import dashboard` from ai_trader.py + remove dashboard startup code
- Remove `static/` dir from ai-trader

### Step 4: Move IPC files
- `signals/signals.json` → `ipc/signals.json`
- `signals/ai-decision.json` → `ipc/ai-decision.json`
- `signals/ai-result.json` → `ipc/ai-result.json`

### Step 5: Move & consolidate dashboard
- `signals/dashboard/*` → `dashboard/`
- Move `signals/ai-trader/static/index.html` content into `dashboard/static/` if unique
- Update PROJECT_ROOT / path references in dashboard API modules:
  - `signals/signals.json` → `../ipc/signals.json`
  - `signals/ai-decision.json` → `../ipc/ai-decision.json`
  - `signals/ai-result.json` → `../ipc/ai-result.json`

### Step 6: Update executor paths
- `executor/bot.py`:
  - `"../signals/ai-decision.json"` → `"../ipc/ai-decision.json"`
  - `"../signals/ai-result.json"` → `"../ipc/ai-result.json"`
  - `"../signals/signals.json"` → `"../ipc/signals.json"`
  - `"../signals/ai-trader"` → `"../ai-trader"`

### Step 7: Update systemd units
- `lighter-scanner.service`:
  - ExecStart: update scanner-daemon.sh path
  - Update scanner-daemon.sh content (cd + LOG paths)
- `ai-trader.service`:
  - WorkingDirectory: `/root/.openclaw/workspace/projects/autopilot-trader/ai-trader`
  - ExecStart stays same (ai_trader.py)
- Create `dashboard.service` if not exists (for Flask dashboard)

### Step 8: Add path constants to shared/
- Add `paths.py` to `shared/` with IPC_DIR, PROJECT_ROOT constants
- Optional: update services to use shared paths (low priority, can defer)

### Step 9: Delete empty `signals/` dir
- After all files moved, remove `signals/` entirely

### Step 10: Restart all services
- `systemctl daemon-reload`
- `systemctl restart lighter-scanner`
- `systemctl restart ai-trader`
- `systemctl restart lighter-bot`
- Verify all 3 come up clean

## Files That Need Path Updates (Complete List)

| File | Old Path | New Path |
|------|----------|----------|
| `executor/bot.py` L155-158 | `../signals/ai-decision.json` etc | `../ipc/...` |
| `signals/ai-trader/config.json` | `../signals.json` | `../ipc/signals.json` |
| `signals/ai-trader/config.json` | `../ai-decision.json` | `../ipc/ai-decision.json` |
| `signals/ai-trader/config.json` | `../ai-result.json` | `../ipc/ai-result.json` |
| `signals/ai-trader/ai_trader.py` | `import dashboard` + startup | Remove |
| `signals/scanner-daemon.sh` | cd + LOG paths | Update to scanner/ |
| `signals/scripts/opportunity-scanner.ts` | writes `signals.json` | writes `../ipc/signals.json` |
| `signals/dashboard/api/scanner.py` | `signals/signals.json` | `../ipc/signals.json` |
| `signals/dashboard/api/portfolio.py` | `signals/signals.json` | `../ipc/signals.json` |
| `signals/dashboard/api/trader.py` | `signals/ai-decision.json` | `../ipc/ai-decision.json` |
| `signals/dashboard/api/trader.py` | `signals/ai-result.json` | `../ipc/ai-result.json` |
| `signals/dashboard/api/system.py` | `signals/signals.json` | `../ipc/signals.json` |
| `signals/dashboard/api/system.py` | `signals/ai-decision.json` | `../ipc/ai-decision.json` |
| systemd `lighter-scanner.service` | scanner path | `scanner/scanner-daemon.sh` |
| systemd `ai-trader.service` | WorkingDirectory | `.../autopilot-trader/ai-trader` |

## Risks & Mitigations
- **Risk:** Relative paths break on move → **Mitigation:** Audit every path reference before moving, update in one pass
- **Risk:** IPC race during move → **Mitigation:** Move IPC files last, before service restart
- **Risk:** Dashboard loses features → **Mitigation:** Diff both dashboards first, merge unique endpoints
- **Risk:** systemd cache stale → **Mitigation:** daemon-reload before restart
