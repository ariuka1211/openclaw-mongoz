# MEMORY.md

## Active Projects
- **Trading Bot** — Lighter DEX, Python executor + TS scanner/AI. Services: lighter-scanner, lighter-bot, ai-trader. Full architecture: `projects/autopilot-trader/docs/`
- **Token Tracking** — Added to ai-trader: tiktoken estimation, per-section breakdown, DB persistence. Kilo gateway has ~13K+ hidden tokens per call.
- **Watchdog** — `watchdog-daemon.sh start|stop|status`. Monitors gateway, OpenRouter, sub-agents

## Open Items
- Integration layer: 11 unfixed issues (3 high, 4 medium, 4 low) — executed=True without bot confirmation, signals staleness detection, bot crash gap
- Lighter volume_quota_remaining always returns None on mainnet — quota guard code is dead

## Rules (red lines)
- **ASK BEFORE ACTING** — show what you'll do, wait for yes. No exceptions.
- **NEVER GUESS FINANCIAL DATA** — verify from .env/source files only. Account 719758, L1: 0x1D73456fA182B669783c5adaaB965AbB1A373bEE
- Don't commit secrets to git
- Don't exfiltrate private data. Ever.
- `trash` > `rm`
- NEVER debug/edit code in main session → spawn immediately
- NEVER spend John's money without asking
- NEVER edit openclaw.json, install skills, restart gateway without explicit permission

## Environment
- VPS: 4 vCPU, 8GB RAM, 80GB disk. OpenClaw at `/root/.openclaw`, Gateway port 5705
- GitHub: `ariuka1211/openclaw-mongoz`
- Telegram: @Openclawtestingbot
