# MEMORY.md

## Active Projects

### Crypto Perps Trading Bot (2026-03-18→active)
- Exchange: Lighter (L2 orderbook DEX on StarkEx)
- Architecture: TypeScript SDK + Node.js
- Strategies: Funding Rate Arbitrage + Momentum
- Status: Project skeleton built, pending John review and npm install
- Path: `/root/.openclaw/workspace/lighter-bot/`
- Bot URL: https://app.lighter.xyz/bot/6621225285
- Geo: US-restricted, Netherlands VPS proxy needed
- API keys in config.yaml (not committed)

### Lighter Trading Bot (2026-03-19→active)
- Path: `/root/.openclaw/workspace/lighter-copilot/` (Python bot) + `/root/.openclaw/workspace/lighter-bot/` (TypeScript scanner)
- Scanner: 5 signals (funding 35%, volume 15%, momentum 15%, MA 20%, OB 15%), OKX klines for 81 markets
- Position sizing: risk-based (5% equity per trade), auto-scales to actual balance
- Safety: NaN guards, liq dist ≥ 2× SL, max 20× leverage, max 3 concurrent positions
- Bot: reads `signals.json` each tick, opens positions, DSL manages exits
- **Critical fix (2026-03-20):** Close/reopen loop caused by Lighter API lag — API still reported positions as "open" after AI closed them. Fixed by adding cooldown checks to API-detected position tracking.
- AI trader: change detection on state hash (opportunities + positions), only calls LLM when state changes
- Cooldown: 30min after AI close, blocks both signal-based AND API-detected reopens
- **Prompt engineering (2026-03-20):** Rewrote system.txt with research-backed structure: funding arb mechanics, confidence calibration rubric (5 bands), position sizing table (score→size), decay-based cut losers (+0.1 confidence per cycle). Context builder: funding direction, win rate, time since last decision.
- Cron: scanner runs every 5 min (`*/5 * * * *`)
- Deployed: 2026-03-20 00:06 UTC
- Bot PID: check with `pgrep -f "python3 bot.py"`
- Logs: `/root/.openclaw/workspace/lighter-copilot/bot.log`
- Scanner log: `/root/.openclaw/workspace/lighter-bot/scanner.log`

### Trade Learning Loop (2026-03-20→active)
- **Outcome logging** (`bot.py`): Writes closed trade details to SQLite journal (entry, exit, PnL, hold time, exit reason) for all 5 close paths
- **Reflection agent** (`reflection.py`): LLM reviews trade outcomes, extracts patterns (win rate by direction/hold time/confidence, biggest loss patterns), updates `strategy_memory.md`
- **Signal weight analyzer** (`signal_analyzer.py`): Correlates 5 signal components with trade outcomes, suggests weight adjustments → `state/signal_weights_suggested.json` (human review, not auto-applied)
- **Reflection cron:** Every 3 days at 03:00 UTC via `run_reflection.sh`
- **Rate limiting:** `price_call_delay` 5.0s between API calls, API lag warning dedup (1/min/symbol), Telegram alert timeout

### Memory System (2026-03-18→active)
- Format: MEMORY.md + /projects + /daily
- Spans: memory-spans.jsonl for project-level tracking
- Auto-maintenance via cron (05:00 UTC)
- Manual: /memory:status, /memory:maintain

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

## Recent Decisions (2026-03-20)

- **Close/reopen loop fix:** Lighter API lags after position close, still reports "open". Cooldown check on API-detected positions fixes this.
- **AI trader optimization:** State hash (top 10 opportunities + positions) prevents unnecessary LLM calls when state is static. Rounding scores/sizes reduces noise.
- **Price poll interval:** 60s (was 5s) to reduce 429 rate limits.
- **Cooldown:** 30min after AI close, blocks both signal and API-based reopens.

- **Geo-restriction resolved (2026-03-20):** `api.lighter.xyz` is old/dead. Current endpoint `mainnet.zklighter.elliot.ai` works from US VPS directly. No proxy/VPN needed.
- **Surfshark VPN failed:** Cloudflare blocks all Surfshark APIs from this VPS. Not viable.
- **Logging bug fixed (2026-03-20):** Import-time logging (DecisionDB) triggered lastResort handler before basicConfig, silencing INFO logs. Fixed by moving basicConfig to top of module.
- **MORPHO close loop fixed (2026-03-20):** execute_sl() returned True on submit not fill. Added fill verification (poll API 3×, 5s apart), close attempt tracking (max 3), and 15-min cooldown with Telegram alert.
- **Scanner confusion:** Two scanner systems exist — `run-osa.sh` (hourly Python) vs TypeScript `opportunity-scanner.ts` (produces signals.json). TypeScript scanner is the signals.json producer, runs via cron `*/5 * * * *`.

## Recent Decisions (2026-03-19)

- Lighter bot starts as Read-Only Phase 1 (no trading, just monitoring)
- Use Lighter SDK directly, not CCXT (L2 orderbook DEX needs native support)
- Bot needs ~0.0049 ETH minimum for meaningful funding arb
- 12 markets available, 114 brokers
- Multi-bot Telegram alerts: Core Infra, Trading, Agents (separate bots)
- Watchdog: inotifywait for sub-agent detection, enriched completion alerts

## Rules (post-mortem 2026-03-19)
- **Always check existing code before spawning subagents for research/build.** Run `find /root/.openclaw/workspace -not -path "*/node_modules/*" | grep -i <keyword>` first. Include existing file paths in spawn prompts.
- **Extend existing systems, don't rebuild.** lighter-copilot/ has the full trading bot. New features wire into it.
- **Default to delegation.** Research → Blitz, Code → Mzinho, Infra → Techno4k. I coordinate and synthesize. Don't do research/coding myself unless it's trivial.
- 🔴 **NEVER debug/edit code in main session.** If task involves Python, .py files, logs, or service restarts → spawn agent immediately. No "quick checks." No "I'll just do it." If agent fails → respawn with tighter scope. Never take over.
- 🔴 **"Wrap up" = update MEMORY.md + all 3 agents' memories + session-current.md.**

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