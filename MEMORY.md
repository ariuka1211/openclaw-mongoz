# MEMORY.md

## Active Projects
- **Trading Bot V1** — Lighter DEX, Python executor + TS scanner/AI. Services: scanner, bot, ai-decisions. Full architecture: `projects/autopilot-trader/docs/`
- **Trading Bot V2** — Complete rewrite as separate repo at `projects/autopilot-trader-v2/`. 39 Python files + 11 test files (100 tests green). 6 phases complete (interfaces → strategies → bot → scanner → AI → orchestrator). NOT tracked in openclaw-mongoz repo. Pushed to `github.com/ariuka1211/autopilot-trader-v2` (7 commits). Plan-verified and modular. Still missing: real DataCollector (Lighter API), Telegram alerts, AIDecisionEngine (real LLM), TradingView webhook.
- **Grid Bot** — BTC-only AI-driven grid trading bot on Lighter DEX. Runs daily at 06:00 UTC. Uses AI to set swing-based grid levels. Services: analyst, calculator, grid-manager. Location: `projects/btc-grid-bot/`. Fully operational with capital safety checks and Telegram alerts.
- **Token Tracking** — tiktoken estimation, per-section breakdown, DB persistence. Kilo gateway has ~13K+ hidden tokens per call (persistent session cache, not a bug).
- **Watchdog** — `watchdog-daemon.sh start|stop|status`. Monitors gateway, OpenRouter, sub-agents.

## Rules
See **AGENTS.md** — 7 safety rules, session flow, code flow. Key: ask before acting, spawn subagents, verify work, no polling loops, stop = stop.

### Auto-Ingest Rule (Second Brain)
When John sends a link or article, automatically save it to `projects/second-brain/raw/` and ingest into wiki (source summary, entities, concepts, cross-links, update index, log) — **no asking, no reminding needed**. Report what was created after ingestion.
This rule applies across ALL sessions, not just this one. Next session's me must honor it.


<!-- promoted from memory/2026-03-21.md on 2026-03-25 -->
### Archived: 2026-03-21
## Trading Research Session


<!-- promoted from memory/2026-03-22.md on 2026-03-25 -->
### Archived: 2026-03-22
## Volume Quota / Rate Limiting Investigation & Fixes
## Additional Sessions


<!-- promoted from memory/2026-03-23.md on 2026-03-25 -->
### Archived: 2026-03-23
## ai-trader + bot audit + fixes
## Additional Sessions

