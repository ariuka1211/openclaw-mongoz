# Session Handoff — 2026-03-29 23:34 MDT

## Session Summary
- Connected V2 DataCollector to Lighter REST API (raw JSON parsing, SDK model is broken)
- Discovered WebSocket works with `?readonly=true` — built custom WS client
- WS provides real-time: ticker, market_stats (OI, funding), trades, order_book
- Built ws_client.py with reconnect + exponential backoff
- Smoke test passed: WS + REST both working, 100 tests green
- Researched Senpi-ai/senpi-skills — studied their liquidation cascade detection
- Researched top GitHub crypto trading bots — saved findings to memory/2026-03-29-research.md
- Discussed AI engine architecture — concluded AI layer is unnecessary if scanner does scoring
- Discussed edge honestly — no proven edge yet, need backtesting to validate hypotheses
- John going to sleep — research saved for morning

## Current State
- V2: real Lighter data via REST (candles) + WebSocket (live price, OI, funding, trades)
- SDK: lighter-sdk 1.0.7 (latest)
- Config: uses env vars from workspace .env, config.yml in .gitignore
- 100 tests passing
- Proxy disabled (direct connection works from server)

## Key Fixes Today
1. Lighter candlestick SDK Pydantic model broken → parse raw JSON via `candles_without_preload_content()`
2. Resolution map was wrong ("5" not "5m") → fixed to "5m" format
3. WebSocket fails without auth → `?readonly=true` solves it
4. SDK WsClient missing readonly param → wrote custom ws_client.py

## Still Missing
1. **Telegram alerts** — config exists, not built
2. **AI engine** — decided it's unnecessary, scanner should do scoring instead
3. **TradingView webhook** — planned, not built
4. **Backtesting engine** — need to validate signal hypotheses
5. **README**

## Research Findings (saved)
- Top GitHub repos: Freqtrade (FreqAI/ML), Hummingbot (Avellaneda-Stoikov), Jesse (backtesting)
- Quick wins to add: time-of-day filtering, relative volume, ATR sizing, funding percentile
- V2 gaps: volatility sizing, regime detection, session filtering, performance metrics

## Key Files
- `projects/autopilot-trader-v2/sources/scanner_v2/data_collector.py` — REST candles from Lighter
- `projects/autopilot-trader-v2/sources/scanner_v2/ws_client.py` — WebSocket client (NEW)
- `projects/autopilot-trader-v2/sources/scanner_v2/scanner.py` — orchestrates WS + REST
- `memory/2026-03-29-research.md` — GitHub repo research findings
- `memory/2026-03-29.md` — daily notes

## ⚠️ LESSONS LEARNED
- NEVER guess when you can search or check existing code first
- ALWAYS use subagents for research and implementation work
- DO NOT jump to conclusions (Coinalyze) when Lighter already works
- VERIFY before implementing — check V1 code, check SDK source, check raw API responses
