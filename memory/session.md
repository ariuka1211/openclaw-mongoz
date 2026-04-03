# Session Handoff — 2026-04-02 (v2 — Grid Bot Overhaul Day 2)

## What happened
Session: added 5 new features to grid bot. All committed & pushed. Bot restarted.

## Features added
1. **Volume Profile** — `calc_volume_profile()` in indicators.py, replaces BB-based roll generation. HVN/LVN placement, BB fallback.
2. **Funding Rate Awareness** — `funding_rate_adjustment()` in indicators.py. Tiered sizing: extreme positive funding → 0.4x, negative → 1.1x. In calculator, deploy, main, analyst prompt.
3. **One-Sided Roll** — `roll_one_sided()` in grid.py. Only replaces tested side, keeps other intact. Falls back to full roll if too few levels.
4. **Time-Aware Volatility** — `time_awareness_adjustment()` in indicators.py. Asian 0.7x, London 1.0x, NY overlap 1.2x. Multiplies with vol_adj and compounding.
5. **Trailing Loss Limit** — peak_equity tracking in main.py + grid.py. 4% trail from peak, never drops below static 8% floor. Configurable via `risk.trailing_loss_pct`.

## All multiplier chain
`size = base × vol_adj × time_adj × funding_adj × compounding`

## Current state
- btc-grid-bot: ✅ running, restarted with new code
- btc-grid-telegram: ✅ running
- Equity: ~$86.72, BTC @ ~$66,499
- Grid adopted: 3 buy + 7 sell orders
- Config: `trailing_loss_pct: 0.04` added to config.yml

## Commits pushed to main
- `ac0d6ae` feat: volume profile, funding rate awareness, one-sided roll, time-aware volatility
- `2f891e5` feat: trailing loss limit - 4% trail from peak equity with absolute 8% floor

## Rejected
- CoinGlass liquidation heatmap: too much work, user said skip.

## Next potential ideas (not implemented)
- Downtrend auto-reduce (shrink buys 50% when EMA filter warns)
- Multi-asset expansion (same engine, ETH next)
- Configurable time session multipliers in config.yml
