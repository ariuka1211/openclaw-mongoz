# MEMORY.md

## Active Projects
- **Trading Bot** — Lighter DEX, Python executor + TS scanner/AI. Services: lighter-scanner, lighter-bot, ai-trader. Full architecture: `projects/autopilot-trader/docs/`
- **Token Tracking** — tiktoken estimation, per-section breakdown, DB persistence. Kilo gateway has ~13K+ hidden tokens per call (persistent session cache, not a bug).
- **Watchdog** — `watchdog-daemon.sh start|stop|status`. Monitors gateway, OpenRouter, sub-agents.

## Rules
See **AGENTS.md** for all hard rules and session flow. Key red lines:
- Ask before acting. Never guess financial data. Never debug in main session.
- Account 719758, L1: 0x1D73456fA182B669783c5adaaB965AbB1A373bEE

