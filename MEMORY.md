# MEMORY.md

## Active Projects

### Crypto Perps Trading Bot (2026-03-18→active)
- Exchange: Lighter (L2 orderbook DEX on StarkEx)
- Architecture: TypeScript SDK + Node.js
- Strategies: Funding Rate Arbitrage + Momentum
- Status: Project skeleton built, pending John review and npm install
- Path: `/root/.openclaw/workspace/signals/`
- Bot URL: https://app.lighter.xyz/bot/6621225285
- Geo: US-restricted, Netherlands VPS proxy needed
- API keys in config.yaml (not committed)

### Trading Bot (2026-03-19→active)
- Path: `/root/.openclaw/workspace/executor/` (Python bot) + `/root/.openclaw/workspace/signals/` (TypeScript scanner + AI trader)
- Scanner: 5 signals (funding 35%, volume 15%, momentum 15%, MA 20%, OB 15%), OKX klines for 81 markets
- Position sizing: risk-based (5% equity per trade), auto-scales to actual balance
- Safety: NaN guards, liq dist ≥ 2× SL, max 20× leverage, max 3 concurrent positions
- Bot: reads `signals.json` each tick, opens positions, DSL manages exits
- **Critical fix (2026-03-20):** Close/reopen loop caused by Lighter API lag — API still reported positions as "open" after AI closed them. Fixed by adding cooldown checks to API-detected position tracking.
- AI trader: change detection on state hash (opportunities + positions), only calls LLM when state changes
- Cooldown: 30min after AI close, blocks both signal-based AND API-detected reopens
- **Prompt engineering (2026-03-20):** Rewrote system.txt with research-backed structure: funding arb mechanics, confidence calibration rubric (5 bands), position sizing table (score→size), decay-based cut losers (+0.1 confidence per cycle). Context builder: funding direction, win rate, time since last decision.
- Cron: scanner runs every 5 min (via lighter-scanner.service daemon)
- Deployed: 2026-03-20 00:06 UTC
- Bot PID: check with `pgrep -f "python3 bot.py"`
- Logs: `/root/.openclaw/workspace/executor/bot.log`
- Scanner log: `/root/.openclaw/workspace/signals/scanner.log`

### Trade Learning Loop (2026-03-20→active)
- **Outcome logging** (`executor/bot.py`): Writes closed trade details to SQLite journal (entry, exit, PnL, hold time, exit reason) for all 5 close paths
- **Reflection agent** (`signals/ai-trader/reflection.py`): LLM reviews trade outcomes, extracts patterns (win rate by direction/hold time/confidence, biggest loss patterns), updates `strategy_memory.md`
- **Signal weight analyzer** (`signals/ai-trader/signal_analyzer.py`): Correlates 5 signal components with trade outcomes, suggests weight adjustments → `state/signal_weights_suggested.json` (human review, not auto-applied)
- **Reflection cron:** Every 3 days at 03:00 UTC via `run_reflection.sh`
- **Rate limiting:** `price_call_delay` 5.0s between API calls, API lag warning dedup (1/min/symbol), Telegram alert timeout

### Memory System (2026-03-18→active)
- Format: MEMORY.md + session transcripts + session-current.md + daily files
- Layered pipeline: session → daily files → MEMORY.md (curated, ~200 lines)
- Scripts: memory-session-extract.sh (every 2hrs), memory-nightly-cleanup.sh (daily), memory-archive-dailies.sh (weekly)
- Cron: 3 jobs (extract, cleanup, archive)
- **QMD (2026-03-21):** Reinstalled, configured, tested. Workspace collection with 92 docs indexed. Search working clean after removing 102 backup duplicates and LCM remnants.
- **Backups relocated (2026-03-21):** `workspace/backups/` → `~/.openclaw/backups/` — removes backups from QMD index. `backup-memory.sh` updated.
- **LCM disabled (2026-03-21):** lossless-claw plugin killed — 22% of summaries were noise. Replaced with legacy context engine + layered memory pipeline.
- **Wrap-up:** kept manual (not automated). Learning-capture + MEMORY.md editorial judgment stays with Maaraa.

### Watchdog Daemon (2026-03-18→active)
- Location: `/root/.openclaw/workspace/watchdog-daemon.sh`
- Monitors: gateway health, OpenRouter, provider rotation, errors, sub-agents
- Alerts: Telegram bot (token in script)
- Recent fixes (2026-03-19): inotifywait detection, enriched alerts, deduplication
- Run: `./watchdog-daemon.sh start|stop|status`

## Environment

### GitHub (johnmath)
- SSH key: ~/.ssh/id_ed25519 (passphrase in memory)
- Email: johnmath@protonmail.com
- PAT token available in memory for HTTPS push
- New SSH key added to GitHub for this VPS (2026-03-19)

### VPS
- 4 vCPU, 8GB RAM, 80GB disk
- OpenClaw at /root/.openclaw
- Gateway runs on port 5705
- MCP servers: web_search (Brave API), web_fetch (Jina), gateway-management

### Telegram Bot
- @Openclawtestingbot (Test bot for John)
- Bot token: in memory file (not in public memory)
- Connected to OpenClaw Gateway

## Recent Decisions (2026-03-21)

- **QMD audit complete:** Binary works, was unconfigured. Data had ~56% trash (102 backup duplicates, LCM remnants, venv files, stale paths). Cleaned and re-indexed: 194 → 92 docs.
- **Backups moved:** `workspace/backups/` → `~/.openclaw/backups/`. QMD has no native exclude syntax, so moving backups out of workspace is the cleanest solution.
- **Wrap-up stays manual:** Not worth automating — MEMORY.md extraction needs editorial judgment, not mechanical concatenation. Existing cron (every 2hrs extract, daily cleanup) handles the mechanical parts.

## Recent Decisions (2026-03-20)

- **Close/reopen loop fix:** Lighter API lags after position close, still reports "open". Cooldown check on API-detected positions fixes this.
- **AI trader optimization:** State hash (top 10 opportunities + positions) prevents unnecessary LLM calls when state is static. Rounding scores/sizes reduces noise.
- **Price poll interval:** 60s (was 5s) to reduce 429 rate limits.
- **Cooldown:** 30min after AI close, blocks both signal and API-based reopens.

- **Geo-restriction resolved (2026-03-20):** `api.lighter.xyz` is old/dead. Current endpoint `mainnet.zklighter.elliot.ai` works from US VPS directly. No proxy/VPN needed.
- **Surfshark VPN failed:** Cloudflare blocks all Surfshark APIs from this VPS. Not viable.
- **Logging bug fixed (2026-03-20):** Import-time logging (DecisionDB) triggered lastResort handler before basicConfig, silencing INFO logs. Fixed by moving basicConfig to top of module.
- **MORPHO close loop fixed (2026-03-20):** execute_sl() returned True on submit not fill. Added fill verification (poll API 3×, 5s apart), close attempt tracking (max 3), and 15-min cooldown with Telegram alert.
- **Scanner confusion resolved:** Two scanner systems exist — `run-osa.sh` (hourly Python, in executor/) vs TypeScript `opportunity-scanner.ts` (produces signals.json, in signals/). TypeScript scanner is the signals.json producer, runs via lighter-scanner.service daemon.

### VPN / Geo-block saga (2026-03-20)
- Lighter blocks ALL datacenter IPs on trading endpoints (CloudFront 403), not just US
- ProtonVPN WireGuard → exit IP was Datacamp (datacenter) → blocked (even Switzerland exit)
- Cloudflare WARP → US datacenter IP → blocked
- Need residential-class IP: IPRoyal residential proxy ($2.70/mo, SOCKS5) or Tailscale through home machine
- Surfshark: needs manual .conf from surfshark.com (API blocked from VPS)
- **Bot still offline** until geo-block resolved

### CI/CD & GitHub (2026-03-20)
- pytest + pytest-cov, tests restructured to tests/
- GitHub Actions: lint + test on Python 3.11/3.12
- `ariuka1211/lighter-copilot` (executor/) and `ariuka1211/openclaw-mongoz` repos created (secrets excluded)

## Recent Decisions (2026-03-19)

- Lighter bot starts as Read-Only Phase 1 (no trading, just monitoring)
- Use Lighter SDK directly, not CCXT (L2 orderbook DEX needs native support)
- Bot needs ~0.0049 ETH minimum for meaningful funding arb
- 12 markets available, 114 brokers
- Multi-bot Telegram alerts: Core Infra, Trading, Agents (separate bots)
- Watchdog: inotifywait for sub-agent detection, enriched completion alerts

## Rules (post-mortem 2026-03-19)
- **Always check existing code before spawning subagents for research/build.** Run `find /root/.openclaw/workspace -not -path "*/node_modules/*" | grep -i <keyword>` first. Include existing file paths in spawn prompts.
- **Extend existing systems, don't rebuild.** `executor/` has the full trading bot. New features wire into it.
- **Default to delegation.** Research → Blitz, Code → Mzinho, Infra → Techno4k. I coordinate and synthesize. Don't do research/coding myself unless it's trivial.
- 🔴 **NEVER debug/edit code in main session.** If task involves Python, .py files, logs, or service restarts → spawn agent immediately. No "quick checks." No "I'll just do it." If agent fails → respawn with tighter scope. Never take over.
- 🔴 **"Wrap up" = update MEMORY.md + all 3 agents' memories + session-current.md.**
- 🔴 **NEVER dismiss options without investigating.** If John asks about something, check docs/config/tool policies before saying "nothing I can do." Investigate first, answer later. (L008, 2026-03-20)

## Constraints
- No public tweets about security findings
- No pushing corrupted memory files
- Always verify SSH connectivity before relying on it
- Don't commit secrets to git
- Lighter is geo-restricted from US IPs (need Netherlands proxy)
- **HARD RULE: Update memory (session-current.md + MEMORY.md) after every agent completion, significant decision, or milestone. No batching, no "I'll do it later."**

## Recent Decisions (2026-03-19 evening)
- Agent delegation: Maaraa uses `sessions_spawn` for Blitz/Mzinho/Techno4k, not `sessions_send`
- Agent groups are for direct user interaction, not cross-agent workbenches
- `sessions_send` doesn't post to Telegram groups — only injects into sessions
- Keep openclaw.json cross-agent settings (no revert)
- Opportunity scanner: Lighter-only coins, no sentiment, position sizing with liquidation protection