# MEMORY.md

## Active Projects

- **Trading Bot** — Lighter DEX, Python executor + TS scanner/AI. Runs via `lighter-scanner.service`. Bot: `projects/autopilot-trader/executor/bot.py`, Scanner: `projects/autopilot-trader/signals/scripts/opportunity-scanner.ts`, AI: `projects/autopilot-trader/signals/ai-trader/ai_trader.py`. Services: lighter-scanner, lighter-bot, ai-trader. Logs: `projects/autopilot-trader/executor/bot.log`, `projects/autopilot-trader/signals/scanner.log`. **2026-03-23 audit:** 9 bugs fixed (safety crash, reflection env var, confidence scale, ignored stop_loss_pct, pnl_pct/roe_pct mix, parse errors, race conditions). File bus hardened (safe_read_json, decision_id tracking, atomic writes). DB auto-purge added.
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

- [decision] (system) Gitignore updated to include auth-profiles.json and models.json to prevent credential leaks.
- [pattern] (system) MEMORY.md functions as a pointer/summary, not a complete record. Implementation details are in code, history in daily files.
- [decision] (system) Restructured trading bot files: `executor/` and `signals/` moved into `projects/autopilot-trader/`.
- [decision] (system) Agents `blitz`, `coder`, and `techno4k` have been fully removed from config and filesystem, leaving only the main agent.
- [lesson] (system) Always ask for permission before initiating system-level changes (config edits, file deletions, restarts) to honor AGENTS.md rules and maintain trust.
- [lesson] (system) Heavy work should be delegated to subagents, not performed in the main session.
- [fix] (trading) `lighter-bot.service` paths updated from `executor/` to `projects/autopilot-trader/executor/`.
- [decision] (system) Scripts in workspace/scripts/ reorganized into 5 subdirs: memory/, search/, monitoring/, learning/, git/. All references (crontab, systemd, TOOLS.md, AGENTS.md) updated.
- [decision] (system) Memory scripts switched from OpenRouter to Kilo Gateway (api.kilo.ai) with KILOCODE_API_KEY and model xiaomi/mimo-v2-pro.
- [open] (system) API key location mismatch: memory-session-extract.sh sources $W/.env for KILOCODE_API_KEY, but the key actually lives in scripts/memory/.env. Need to copy key to workspace root .env or update the script path.
- [fact] (system) memory-session-extract.sh returns exit code 1 despite successful output — processes transcripts and extracts items correctly but exits non-zero.

## Session Extract — 2026-03-22 [auto]

- [fact] (trading) Side detection bug: bot tracked all positions as 'long' regardless of direction because it used `pos.position` (always positive size) instead of `pos.sign` (+1/-1). Fixed in commit 6b94de8.
- [fact] (trading) AI trader completely broken: config had `api.kilo.ai/v1` instead of `api.kilo.ai/api/gateway`, causing HTTP 404 on all LLM calls. Fixed by updating URL.
- [fact] (trading) DSL close loop had no circuit breaker — retried every ~15s forever when close orders failed (volume quota). Added max 3 attempts + 15min cooldown + Telegram alert.
- [fact] (trading) Volume quota: Lighter DEX account may have burned through initial 1000 quota on failed orders. Need to check account tier.
- [fact] (trading) BTC long opened manually by John: $67,827.40, size=0.00147, 10x leverage.
- [open] (trading) DSL tier_lock triggers but stop-loss orders fail to execute, leaving positions stuck open after DSL fires. Distinct from the existing SL method issue — persists even with limited_slippage.
- [open] (trading) Position re-detection loop: same positions detected as 'new' every sync cycle, creating duplicate DSLState entries.
- [fact] (trading) ROE calculation in `DSLState.current_roe()` is correct for both long and short. Alert formatting was not buggy — only the side detection was wrong.
- [fact] (trading) Lighter REST API auth helper implemented: `LighterAuthManager` class in `auth_helper.py` with in-memory + disk caching, auto-refreshes 7 days before 30-day token expiry. Ready for integration into `bot.py` pending John's approval.
- [open] (trading) Critical bug: bot flips position direction after restart — positions correctly detected as LONG before restart, re-detected as SHORT after restart (e.g. RESOLV/MORPHO/ROBO at 17:38). Root cause in position sync logic around bot.py lines 611-612 where `sign` field extraction fails during post-restart re-sync.
- [decision] (trading) Implemented quota-aware exponential backoff: 60s → 120s → 300s (capped) when volume quota exhausted. VolumeQuotaError raised by API methods and caught at all order callers.
- [decision] (trading) Added quota prioritization: when quota < 50 remaining, new opens blocked to preserve quota for critical SL orders. TP execution also restricted in emergency mode.
- [fact] (trading) Prediction market contrarian signal explored: Minara Polymarket workflow that fades 85%+ odds skews is theoretically sound per prediction market literature, but current implementation is vibes-based LLM reasoning, not quantitative. Needs backtesting.

## Session Extract — 2026-03-23 [auto]

- [pattern] (system) John wants to review changelogs before updating OpenClaw — prefers deliberate, informed maintenance over blind upgrades.

## Session Extract — 2026-03-23 [auto] (ai-trader audit)

- [fact] (trading) Major ai-trader + bot audit: 13 issues found and fixed. Safety crash (self.config AttributeError), reflection env var mismatch, confidence scale mismatch (ai-trader 0-1 vs bot 0-100), stop_loss_pct from AI silently ignored by bot, pnl_pct stored ROE but named raw %. File bus hardened with safe_read_json, decision_id tracking, atomic writes. DB auto-purge added. Commit 4d2aba6.
- [fact] (trading) Bot now uses per-position sl_pct from AI decision, falls back to config default 2%. Stored on TrackedPosition, used in compute_sl_price and trailing SL computation. DSL path unchanged.
- [fact] (trading) DB schema: added roe_pct column to outcomes table (auto-migration). _log_outcome now stores raw pnl_pct and leveraged roe_pct separately. Context builder and reflection display "ROE" label.
- [fact] (trading) File bus race condition mitigation: safe_read_json with 2 retries + 100ms delay on JSONDecodeError. Applied to all shared file reads (signals.json, ai-decision.json, ai-result.json).
- [fact] (trading) Close cooldown reduced 1800s (30min) → 300s (5min). Emergency halt now sleeps 10s before shutdown to give bot time to process close_all.
- [fact] (trading) Dead debug code removed: _analyze_quota_response, _test_quota_response and all call sites. _get_fill_price reuses aiohttp session.
- [fact] (trading) parse_decision_json rewritten with brace-depth tracking (was fragile rfind('}')). _pending_sync.clear() moved from per-position loop to after position loop in _tick.


## Session Extract — 2026-03-23 [auto]

- [fact] (trading) Full codebase audit of ai-trader + executor bot + integration layer found 43 issues total (4 critical, 11 high, 15 medium, 13 low) across three parallel audit passes. Critical and high executor/ai-trader fixes merged; integration layer issues and ai-trader medium/low remain pending. — distilled audit results
- [pattern] (system) Auditors can produce false positives — verify before fixing. Example: `shared/ipc_utils.py` reported missing but actually exists at correct path. Confirm the issue is real before spawning fix work. — distilled audit results
- [open] (trading) Integration layer has 11 unfixed issues: `executed=True` set without bot confirmation, no signals staleness detection, bot crash gap between result write and ACK. 3 high, 4 medium, 4 low. — distilled audit results
- [open] (trading) AI-trader medium/low issues pending (7 medium, 4 low) — subagent spawned but completion not yet confirmed. — distilled audit results
- [fact] (trading) No cancel API exists in the codebase for stale orders — skipped the fix for cancel-before-new-SL and added a TODO instead. — distilled audit results


## Session Extract — 2026-03-23 [auto]

- [fact] (trading) 39 audit fixes (5 critical, 8 high, 12 medium, 14 low) for trading services were merged and deployed; lighter-bot and ai-trader restarted successfully post-deploy. — distilled topics
- [lesson] (system) Service files and runtime artifacts can drift from source; verify venv existence, config.yml presence, and systemd env file paths before assuming a clean restart. — distilled topics
- [pattern] (trading) Transients 'Server disconnected' warnings during initial balance/position fetch are expected when connecting via proxy; auto-retry handles it—not a code issue. — distilled topics

