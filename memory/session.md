# Session — 2026-03-25 10:42-11:10 MDT

## What Happened
- Major cleanup session — removed dead code, scripts, and cron jobs from earlier refactor
- Fixed stale paths from the Mar 25 directory restructure across dashboard

## Cleanup — Removed
**Cron jobs removed (3):**
- `memory-nightly-cleanup` — daily 5 AM, script already deleted
- `memory-session-extract` — every 2 hours, script in deleted `scripts/memory/`
- `reflection-agent` — every 3 days, replaced by live reflection system in context_builder.py
- `memory-archive-weekly` — disabled, script didn't exist

**Scripts deleted (6):**
- `scripts/memory/memory-nightly-cleanup.sh`
- `scripts/memory/memory-session-extract.sh`
- `scripts/monitoring/watchdog.sh` — superseded by watchdog-daemon.sh
- `scripts/monitoring/queue-watchdog.sh` — superseded by watchdog-daemon.sh
- `scripts/monitoring/healthcheck.sh` — unused

**ai-decisions dead code removed:**
- `reflection.py` — replaced by live reflection in context_builder.py
- `signal_analyzer.py` — same
- `state/signal_weights_suggested.json` — orphan artifact

**config.json cleaned:**
- Removed `reflection_model`, `dashboard`, `alerting` keys (dead references)

**Dead methods removed:**
- `context_builder.py` → `read_strategy_memory()` 
- `llm_client.py` → `call_with_model()`

**Created:**
- `dashboard.service` systemd unit — dashboard wasn't running as a service

## Path Fixes (from Mar 25 restructure)
- `dashboard/api/portfolio.py` — `executor/` → `bot/` (bot_state.json)
- `dashboard/api/trader.py` — `executor/` → `bot/` + `signals/` → `ipc/` (signals.json)
- `dashboard/api/system.py` — `executor/` → `bot/` + `signals/` → `ipc/` (ai-decision.json)
- Earlier session: `dashboard/api/trader.py` + `system.py` — `signals/ai-trader/` → `ai-decisions/`

## Tailscale
- Dashboard exposed via Tailscale serve on port 8443 (HTTPS)
- URL: https://srv1435474.tail57784d.ts.net:8443
- OpenClaw remains on default port 443

## Remaining Cron Jobs
- `tradingbot-ratelimit` — disabled
- All others removed

## Pending
- Backtesting implementation (triple barrier method) — still not started
