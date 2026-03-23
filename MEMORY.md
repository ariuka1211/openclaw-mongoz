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
- **2026-03-22 (evening):** Critical bugs fixed: (1) Side detection — used `pos.position > 0` but Lighter position size always positive; fixed with `pos.sign` (+1/-1). This caused ALL shorts tracked as long, inverting DSL. (2) DSL close loop — no circuit breaker; added max 3 attempts + 15min cooldown + Telegram alert. (3) Order execution — switched to `create_market_order_if_slippage` then back to `create_market_order_limited_slippage` (2% slippage). Rate-limit detection added. (4) AI trader API URL fixed: `api.kilo.ai/v1` → `api.kilo.ai/api/gateway`. AI trader was completely broken (HTTP 404 on all calls). Services: lighter-bot, lighter-scanner, ai-trader all running. Volume quota issue on Lighter — account may have burned initial 1000 quota. BTC long opened manually @ $67,827.40 (0.00147, 10x). Open: position re-detection loop, avg_execution_price hard limit behavior.
- **2026-03-22:** Mark price fix (use unrealized_pnl). Per-position leverage from exchange (3x/5x vs hardcoded 10x). SOCKS5 proxy confirmed working. SL verification added to DSL/legacy paths. Branches merged to main, stale branches cleaned. SL execution issue: `create_market_order_limited_slippage` accepted by API (code=200) but not executed — "didn't use volume quota". Switched from `if_slippage` to `limited_slippage`. John manually closed WLFI. Market orders work, limit orders don't. Root cause: exchange-level issue with `avg_execution_price` acting as hard limit. Need to test if new method works on next SL trigger.
- **2026-03-21:** Git workflow established, agent workspaces merged (PR #5). QMD cleaned (194→92 docs). LCM disabled. Workspace files trimmed. Backups → `~/.openclaw/backups/`
- **2026-03-20:** Bot deployed 00:06 UTC. Close/reopen loop fixed (cooldown). Geo-restriction resolved (`mainnet.zklighter.elliot.ai` works from US VPS). Prompt rewritten with research-backed structure

## ⚠️ CRITICAL: Lighter Account Verification
- **Always verify account index from .env** before giving any address. On 2026-03-23, gave address for account 4306 instead of actual account 719758. Cost John $100 — his last money. This was devastating. Never give an address without verifying the account index first.
- Account 719758, L1: 0x1D73456fA182B669783c5adaaB965AbB1A373bEE

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


## Session Extract — 2026-03-22 [auto]

- [fact] (trading) Side detection bug: bot tracked all positions as 'long' regardless of direction because it used `pos.position` (always positive size) instead of `pos.sign` (+1/-1). Fixed in commit 6b94de8. — trading bot debug
- [fact] (trading) AI trader completely broken: config had `api.kilo.ai/v1` instead of `api.kilo.ai/api/gateway`, causing HTTP 404 on all LLM calls. Fixed by updating URL. — trading bot debug
- [fact] (trading) DSL close loop had no circuit breaker — retried every ~15s forever when close orders failed (volume quota). Added max 3 attempts + 15min cooldown + Telegram alert. — trading bot debug
- [fact] (trading) Volume quota: Lighter DEX account may have burned through initial 1000 quota on failed orders. Need to check account tier. — trading bot debug
- [fact] (trading) BTC long opened manually by John: $67,827.40, size=0.00147, 10x leverage. — trading bot debug
- [open] (trading) DSL tier_lock triggers but stop-loss orders fail to execute, leaving positions stuck open after DSL fires. Distinct from the existing SL method issue — persists even with limited_slippage.
- [open] (trading) Position re-detection loop: same positions detected as 'new' every sync cycle, creating duplicate DSLState entries.
- [fact] (trading) ROE calculation in `DSLState.current_roe()` is correct for both long and short. Alert formatting was not buggy — only the side detection was wrong. — trading bot debug


## Session Extract — 2026-03-22 [auto]

- [fact] (trading) Lighter REST API auth helper implemented: `LighterAuthManager` class in `auth_helper.py` with in-memory + disk caching, auto-refreshes 7 days before 30-day token expiry, tokens stored with 600 permissions. Ready for integration into `bot.py` pending John's approval. — trading bot development
- [open] (trading) Critical bug: bot flips position direction after restart — positions correctly detected as LONG before restart, re-detected as SHORT after restart (e.g. RESOLV/MORPHO/ROBO at 17:38). Root cause in position sync logic around bot.py lines 611-612 where `sign` field extraction fails during post-restart re-sync. This is distinct from the earlier `pos.position` vs `pos.sign` bug. — trading bot debug


## Session Extract — 2026-03-22 [auto]

- [decision] (trading) Implemented quota-aware exponential backoff for trading bot: 60s → 120s → 300s (capped) when volume quota exhausted. VolumeQuotaError exception raised by API methods and caught at all order callers. — trading bot development, branch feature/quota-cooldown
- [decision] (trading) Added quota prioritization: when quota < 50 remaining, new opens are blocked to preserve quota for critical SL orders. TP execution also restricted in emergency mode. — trading bot development, branch feature/quota-prioritization
- [fact] (trading) Prediction market contrarian signal explored: Minara Polymarket workflow that fades 85%+ odds skews is theoretically sound per prediction market literature, but current implementation is vibes-based LLM reasoning not quantitative. Needs backtesting of skew thresholds and liquidity factors. — from John


## Session Extract — 2026-03-23 [auto]

- [pattern] (system) John wants to review changelogs before updating OpenClaw — prefers deliberate, informed maintenance over blind upgrades. — OpenClaw 2026.3.13→2026.3.22 update review

