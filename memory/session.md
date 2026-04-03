# Session Handoff — 2026-04-03 (AM)

## What happened
Two feature batches + critical bug fixes.

### Batch 1: Phase 2 — Short Grid Direction (commits d0251f7-384ecc2)
- Short grids: bot can flip SHORT instead of pausing in downtrends
- deploy_short_grid(), direction_score() (6-signal, -100/+100)
- Safety overrides: never short during capitulation/long_squeeze
- Min grid spacing: 1.0× ATR, min range: 5× ATRs (~$530)
- Deployment snapshots with signal context in state/deployments/
- 28 unit tests passing

### Batch 2: Critical Bug Fixes (3 commits)
- **d53d6c6**: recover_position() short fix — place BUY to cover shorts, not SELL
- **5cb6ee0**: Dust position loop — skip recovery for < 1% equity
- **fe359e5**: Remove premature AI pause bail, default direction = neutral_prefer_long

## The Short Disaster (root cause)
Old recover_position() always placed SELL orders. When position was a SHORT (-0.024 BTC = $1,606, 1853% equity), it placed SELL orders that made the short bigger. Each restart grew the short: -0.0036 → -0.0071 → -0.0160 → -0.0240 BTC.

Fixed by detecting position sign, placing BUY to cover shorts, SELL to close longs. 3 exit BUYs filled, short closed. Net loss: ~$1.47 ($86.73 → $85.26 = -1.7%).

## Current State
- btc-grid-bot: ✅ running on `fe359e5` (11:32 deploy)
- BTC @ ~$66,842, equity ~$85.26
- Grid: 5 BUYs ($65,700-$66,650) + 5 SELLs ($67,100-$69,200)
- Size per level: 0.001432 BTC (~$96/level)
- Direction: neutral_prefer_long (dust position detected but incorporated)
- Next 06:00 UTC daily reset will use full direction scoring

## Key Decisions
- Long-only grid → directional grid (long/short/neutral_prefer_long) — approved
- Position nets on Lighter, can't hold long + short simultaneously
- Dust position threshold: < 1% equity → ignore in recover/AI pause logic
- Dust but >= 1% and < 10% → incorporate into grid

## Pending
- 4% trailing stop check: equity is $85.26 vs high watermark?
- Post-mortem after 5+ ATR deployment results come in
- Volume quota concerns: used ~143 of remaining (8356 quota → 8356 remaining = barely touched)
- Config.yml has uncommitted changes (contains secrets, .gitignored)

## Git Status
- On branch main, pushed to fe359e5
- 6 total commits today
- config.yml managed manually (secrets)
