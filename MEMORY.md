# MEMORY.md

## Active Projects

### Trading Bot (2026-03-19→active)
- **Exchange:** Lighter (L2 orderbook DEX on StarkEx). Endpoint: `mainnet.zklighter.elliot.ai` (works from US VPS directly, no proxy needed)
- **Architecture:** Python executor (`executor/bot.py`) + TypeScript scanner/AI trader (`signals/`)
- **Scanner:** 5 signals (funding 35%, volume 15%, momentum 15%, MA 20%, OB 15%), OKX klines for 81 markets → `signals.json` every 5min via `lighter-scanner.service`
- **Bot:** reads `signals.json` each tick, opens positions, DSL manages exits. PID: `pgrep -f "python3 bot.py"`
- **AI trader:** change detection on state hash, only calls LLM when state changes. Cooldown: 30min after AI close
- **Position sizing:** risk-based (5% equity per trade), auto-scales to actual balance
- **Safety:** NaN guards, liq dist ≥ 2× SL, max 20× leverage, max 3 concurrent positions
- **Prompt:** system.txt — funding arb mechanics, confidence calibration (5 bands), position sizing table, decay-based cut losers
- **Logs:** `executor/bot.log`, `signals/scanner.log`
- **Bot URL:** https://app.lighter.xyz/bot/6621225285

### Trade Learning Loop (2026-03-20→active)
- **Outcome logging** (`executor/bot.py`): SQLite journal — entry, exit, PnL, hold time, exit reason for all close paths
- **Reflection** (`signals/ai-trader/reflection.py`): LLM reviews outcomes, extracts patterns, updates `strategy_memory.md`. Cron: every 3 days at 03:00 UTC
- **Signal analyzer** (`signals/ai-trader/signal_analyzer.py`): correlates signal components with outcomes → `state/signal_weights_suggested.json` (human review, not auto-applied)
- **Rate limiting:** `price_call_delay` 5.0s, API lag warning dedup (1/min/symbol)

### Memory System (2026-03-18→active)
- **Format:** MEMORY.md + session transcripts + session-current.md + daily files
- **Pipeline:** session → daily files → MEMORY.md (curated, ~200 lines)
- **Scripts:** memory-session-extract.sh (every 2hrs), memory-nightly-cleanup.sh (daily), memory-archive-dailies.sh (weekly)
- **Cron:** 3 jobs (extract, cleanup, archive)
- **QMD (2026-03-21):** 92 docs indexed. Backups moved to `~/.openclaw/backups/`. LCM disabled.
- **Wrap-up:** manual (Maaraa editorial judgment, not automated)

### Watchdog Daemon (2026-03-18→active)
- `watchdog-daemon.sh start|stop|status`
- Monitors: gateway health, OpenRouter, provider rotation, errors, sub-agents
- Alerts: Telegram bot (token in script)

## Environment

### GitHub (johnmath)
- SSH key: `~/.ssh/id_ed25519` (passphrase in memory)
- Email: johnmath@protonmail.com
- PAT token in memory for HTTPS push

### VPS
- 4 vCPU, 8GB RAM, 80GB disk
- OpenClaw at `/root/.openclaw`, Gateway port 5705

### Telegram
- @Openclawtestingbot, token in memory file (not public)

## Recent Decisions

**2026-03-21:**
- Agent workspace files merged to main (PR #5, 49 files). Under `agents/blitz`, `agents/coder`, `agents/techno4k`.
- Git workflow: agents commit locally with attribution, Maaraa reviews before pushing. Format: `[agent-id] description`. Agents never push. Always branch + PR.
- Branch naming: `<agent-id>/<verb>-<description>`. Future PRs: small and focused.
- Secrets excluded: auth-profiles.json, models.json, API keys — all in .gitignore.
- Unified repo: `executor/` + `signals/` in openclaw-mongoz workspace. Abandoned lighter-copilot repo.
- Unified repo: `executor/` + `signals/` in openclaw-mongoz workspace. Abandoned lighter-copilot repo.
- QMD audit: cleaned 102 backup duplicates + LCM remnants (194 → 92 docs). Backups → `~/.openclaw/backups/`.
- LCM disabled: 22% noise. Replaced with legacy context engine + layered memory pipeline.
- Wrap-up stays manual: MEMORY.md needs editorial judgment.

**2026-03-20:**
- Close/reopen loop fix: Lighter API lags after close, still reports "open". Cooldown on API-detected positions.
- AI trader: state hash prevents unnecessary LLM calls. Price poll: 60s (was 5s).
- MORPHO close loop: execute_sl() returned True on submit not fill. Fixed with fill verification + attempt tracking + cooldown.
- Geo-restriction resolved: `api.lighter.xyz` dead. `mainnet.zklighter.elliot.ai` works from US VPS. No VPN/proxy needed.
- Prompt engineering: rewrote system.txt with research-backed structure.
- Deployed bot: 2026-03-20 00:06 UTC.

**2026-03-19:**
- Lighter bot starts Read-Only Phase 1 (monitoring, no trading)
- Use Lighter SDK directly, not CCXT
- Bot needs ~0.0049 ETH minimum for funding arb
- 12 markets, 114 brokers
- Agent delegation: Maaraa uses `sessions_spawn` (not `sessions_send`)
- Opportunity scanner: Lighter-only coins, position sizing with liq protection

## Rules
- **Always check existing code before spawning subagents.** `find ... | grep -i <keyword>` first.
- **Extend existing systems, don't rebuild.**
- **Default to delegation.** Research → Blitz, Code → Mzinho, Infra → Techno4k.
- 🔴 **NEVER debug/edit code in main session.** Spawn immediately.
- 🔴 **"Wrap up" = update MEMORY.md + agents' memories + session-current.md.**
- 🔴 **NEVER dismiss options without investigating.** Check docs/config first.
- 🔴 **Update memory after every agent completion, decision, or milestone. No batching.**

## Constraints
- Don't commit secrets to git
- Don't push corrupted memory files
- Always verify SSH connectivity before relying on it
- No public tweets about security findings
