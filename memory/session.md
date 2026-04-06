# Session Handoff — 2026-04-06

## What Changed Today

### Grid Bot — Shut Down
- **PnL confirmed:** Equity dropped $86.73 → $79.29 (-$7.44, ~8.5% loss) over 3 days
- Recurring orphan short position bug (3 incidents of $200-438 shorts)
- AI kept redeploying grids that never filled, then redeployed again
- Root cause of rate limits: OpenRouter headers were wrong (`X-Title` → `X-OpenRouter-Title`)
- Added 3-tier LLM fallback: qwen → openrouter/free → gemini
- **BOT IS STOPPED AND ARCHIVED** — no longer running

### V2 Orderflow Bot — Planned
- Full build plan written at `projects/autopilot-trader-v2/docs/plan-orderflow.md`
- 5 phases: Data → Context Builder → AI → Position Manager → Integration
- Tools decided: Lightpanda (headless), stealth-browser-mcp (anti-detection)
- Coinglass liquidation heatmap screenshots work via Playwright
- Credentials shared for Coinglass pro account
- **Status: Planning complete. Ready for Phase 1 when John says go.**

### Mental Health Context
- Created `memory/mental-health.md` with ADHD, TRT taper, ketamine research
- Added "check in" keyword trigger to AGENTS.md
- John is on final 2-4 weeks of TRT taper, considering BetterU ketamine ($500/5 sessions)

## Services Status
- ALL STOPPED — btc-grid-bot, scanner, bot, ai-decisions

## Archive Status
- Grid bot → `projects/archive/btc-grid-bot/`
- V1 autopilot → `projects/archive/autopilot-trader/`

## Next Steps
1. Lightpanda + stealth-browser-mcp install on VPS
2. Coinglass liquidation data collector (logged-in session)
3. Lighter orderbook WS stream
4. Then build out the rest of V2 per plan

## Key Lessons
- Never claim something is "fixed" without verifying
- Always check headers/API keys match what's actually being sent
- Don't change config or make unilateral decisions about the bot
- Verify before claiming, don't guess with confidence
