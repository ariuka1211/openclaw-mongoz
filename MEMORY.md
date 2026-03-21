# MEMORY.md

## Active Projects

- **Trading Bot** — Lighter DEX, Python executor + TS scanner/AI. Runs via `lighter-scanner.service`. Bot: `projects/autopilot-trader/executor/bot.py`, Scanner: `projects/autopilot-trader/signals/scripts/opportunity-scanner.ts`, AI: `projects/autopilot-trader/signals/ai-trader/`. Logs: `projects/autopilot-trader/executor/bot.log`, `projects/autopilot-trader/signals/scanner.log`
- **Trade Learning Loop** — SQLite journal → reflection (every 3 days) → `strategy_memory.md`. Signal analyzer → `state/signal_weights_suggested.json` (human review only)
- **Memory System** — layered pipeline: session → daily files → MEMORY.md. 3 cron jobs (extract 2hr, cleanup daily, archive weekly). QMD: 92 docs indexed
- **Watchdog** — `watchdog-daemon.sh start|stop|status`. Monitors gateway, OpenRouter, sub-agents. Telegram alerts

## Rules (red lines)
- Check existing code before spawning subagents
- NEVER debug/edit code in main session → spawn immediately
- NEVER restart gateway without explicit permission
- Update memory after every agent completion or milestone
- Agents never push to main. Branch + PR only. Format: `[agent-id] description`
- **ASK BEFORE ACTING.** Show what you're about to do, wait for yes. This rule has been broken 40+ times in a week. John is angry and frustrated. Not another apology — actual behavior change.

## Constraints
- Don't commit secrets to git
- Verify SSH before relying on it
- No public tweets about security findings

## Recent
- **2026-03-21:** Git workflow established, agent workspaces merged (PR #5). QMD cleaned (194→92 docs). LCM disabled. Workspace files trimmed (README.md, package.json deleted). Backups → `~/.openclaw/backups/`
- **2026-03-20:** Bot deployed 00:06 UTC. Close/reopen loop fixed (cooldown). Geo-restriction resolved (`mainnet.zklighter.elliot.ai` works from US VPS). Prompt rewritten with research-backed structure

## Environment
- VPS: 4 vCPU, 8GB RAM, 80GB disk. OpenClaw at `/root/.openclaw`, Gateway port 5705
- GitHub: `ariuka1211/openclaw-mongoz`, SSH key `~/.ssh/id_ed25519`
- Telegram: @Openclawtestingbot
