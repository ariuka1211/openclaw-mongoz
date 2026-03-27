# Session Handoff — 2026-03-27 17:41 MDT

## Completed This Session

### ✅ Service Health Check
- All 3 services running: scanner (~5.5h), bot (~17min, restarted at 15:00), ai-decisions (~14min)
- Bot had crash/restart cycle at 15:00 (exit-code 1, counter hit 5), recovered since 15:00:47
- Likely from SL refactor merge needing clean restart

### ✅ Trade Analysis
- Last 5 trades: all winners, all shorts, all dsl_tier_lock exits
- Avg exit at ~0.35% price move — too tight
- Exit reason breakdown: dsl_tier_lock avg +0.47%, trailing_take_profit avg +2.81%
- Losses: dsl_hard_sl 40 trades avg -1.61%

### ✅ DSL Config Review
- Confirmed refactor worked: all pure price move %, no leverage in SL math
- Problem: tier 1 triggers at 0.3% with 0.6% buffer (floor goes underwater)
- Suggested new tiers: 0.75/0.25, 1.5/0.4, 3.0/0.5, 5.0/0.5, 8.0/0.5
- Not applied yet — decided to go with V2 modular approach instead

### ✅ V2 Architecture Planning
- Created v2/ directory with 5 planning docs
- Key insight: scanner + exit strategy are the tuning knobs, bot/executor are stable plumbing
- AI engine is optional (direct pipeline skips it)
- TradingView webhooks = another SignalSource
- Evaluated MMT API — decided to stick with Lighter direct for now

## Open Items
- V2 plans reviewed but not approved for implementation
- No code written for V2 yet
- DSL tier tightening not applied to v1 (waiting for V2)

## Next Steps
- Review v2 plan files, iterate on interfaces
- Start with skeleton code when ready
- Consider signing up for MMT free tier to test buy/sell volume data later
