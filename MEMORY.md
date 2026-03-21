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
- **2026-03-21:** Git workflow established, agent workspaces merged (PR #5). QMD cleaned (194→92 docs). LCM disabled. Workspace files trimmed. Backups → `~/.openclaw/backups/`
- **2026-03-20:** Bot deployed 00:06 UTC. Close/reopen loop fixed (cooldown). Geo-restriction resolved (`mainnet.zklighter.elliot.ai` works from US VPS). Prompt rewritten with research-backed structure

## Environment
- VPS: 4 vCPU, 8GB RAM, 80GB disk. OpenClaw at `/root/.openclaw`, Gateway port 5705
- GitHub: `ariuka1211/openclaw-mongoz`, SSH key `~/.ssh/id_ed25519`
- Telegram: @Openclawtestingbot

## Session Extract — 2026-03-21 [auto]

- [fact] (system) Files deleted: README.md, package.json, test/git-workflow-test.md, tools/github-watchlist.md (source)
- [fact] (system) Skills deleted: xint, lighter-copilot, review-driven-dev, systematic-debugging, skill-monitor. Kept: baseline-kit, deep-research-pro. (source)
- [fact] (system) Scripts deleted (12): check-stop.sh, emergency-stop.sh, delete-completion-spam.sh, delete-telegram-msgs.sh, responsive-template.sh, mem-version.py, mem-append.py, mem-cleanup.py, mem-profile.sh, learning-capture.sh, learning-check.sh, learning-log.sh (source)
- [decision] (system) Gitignore updated to include auth-profiles.json and models.json to prevent credential leaks. (source)
- [pattern] (system) MEMORY.md functions as a pointer/summary, not a complete record. Implementation details are in code, history in daily files. (source)
- [fact] (system) AGENTS.md now correctly states subagent spawning instead of "delegation to team". (source)
- [fact] (system) `patterns/` folder, `hooks/mem0-auto-store/`, and `prompts/cleanup.txt` have been removed. (source)
- [fact] (system) `archive/` folder (old memory-core JS project) has been deleted, superseded by shell pipeline. (source)
- [decision] (system) Restructured trading bot files: `executor/` and `signals/` moved into `projects/autopilot-trader/`. (source)
- [decision] (system) Agents `blitz`, `coder`, and `techno4k` have been fully removed from config and filesystem, leaving only the main agent. (source)
- [lesson] (philosophy) Always ask for permission before initiating system-level changes (config edits, file deletions, restarts) to honor AGENTS.md rules and maintain trust. (source)
- [lesson] (system) Heavy work should be delegated to subagents, not performed in the main session. (source)
- [fix] (trading) `lighter-bot.service` paths updated from `executor/` to `projects/autopilot-trader/executor/`. (source)
- [decision] (system) Scripts in workspace/scripts/ reorganized into 5 subdirs: memory/, search/, monitoring/, learning/, git/. All references (crontab, systemd, TOOLS.md, AGENTS.md) updated. Branch: reorg/scripts-categorize
- [decision] (system) Memory scripts (memory-llm.sh, memory-nightly-cleanup.sh, memory-session-extract.sh) switched from OpenRouter to Kilo Gateway (api.kilo.ai) with KILOCODE_API_KEY and model xiaomi/mimo-v2-pro. — John provided KILOCODE_API_KEY (JWT token for Kilo Gateway)
- [fact] (system) KILOCODE_API_KEY (JWT) provided by John for Kilo Gateway access at api.kilo.ai. Previously no Kilo API key existed on system. — from John
- [fix] (system) git-agent-commit.sh syntax fix: removed empty case patterns (bare ;;). Catch-all * exits with error by design — script intentionally cannot commit itself.
- [fact] (system) memory-archive-dailies.sh deleted as dead code — never scheduled, looked in empty memory/daily/ dir. Daily files now live directly in memory/. Script and empty dir removed.
- [fact] (system) Workspace scripts are OpenClaw infrastructure only. Autopilot-trader has its own scripts under projects/autopilot-trader/signals/scripts/ — completely separate.
- [open] (system) API key location mismatch: memory-session-extract.sh sources $W/.env for KILOCODE_API_KEY, but the key actually lives in scripts/memory/.env. Need to copy key to workspace root .env or update the script path. — end-to-end test of memory-session-extract.sh
- [fact] (system) memory-session-extract.sh returns exit code 1 despite successful output — processes transcripts and extracts items correctly but exits non-zero. — end-to-end test with 7 session transcripts

