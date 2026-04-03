# Session Handoff — 2026-04-02 (FINAL — All 7 Features)

## What happened
Two sessions today. Added 7 features total via subagent swarm. All committed, pushed, bot restarted.

## All Features Shipped
### Batch 1 (4 parallel subagents):
1. **Volume Profile** — `calc_volume_profile()` replaces BB-based roll. HVN/LVN placement from combined 15m+30m+4H+1D candles. BB fallback if empty.
2. **Funding Rate Awareness** — `funding_rate_adjustment()`. Extreme positive >0.1% → 0.4x sizing, negative → 1.1x. Multiplicative chain: `size = base × vol_adj × time_adj × funding_adj × compounding`
3. **One-Sided Roll** — `roll_one_sided()`. Only replace tested side, keep other intact. Falls back to full roll if too few levels or >80% overlap.
4. **Time-Aware Volatility** — `time_awareness_adjustment()`. Asian 0.7x, London 1.0x, NY overlap 1.2x.

### Batch 2 (sequential):
5. **Trailing Loss Limit** — `peak_equity` tracking via `risk.trailing_loss_pct: 0.04`. 4% trail from peak, never drops below static 8% floor.

### Batch 3 (2 parallel subagents):
6. **OI Divergence Detection** — `get_open_interest_history()` + `oi_divergence()`. 4 states: long_squeeze (price↑ OI↓), capitulation (↓↓), new_shorts (↓↑), new_longs (↑↑). Influences roll decisions + LLM prompt.
7. **Volume Spike Cooldown** — `detect_volume_spike()`. >2.5x avg volume → 1hr cooldown on roll + trend pause. Prevents over-reaction to panic candles.

## Current state
- btc-grid-bot: ✅ running, restarted at 23:51 MDT
- btc-grid-telegram: ✅ running
- Equity: ~$86.72, BTC @ ~$66,571

## Commits on main
- `ac0d6ae` feat: volume profile, funding rate awareness, one-sided roll, time-aware volatility
- `2f891e5` feat: trailing loss limit
- `163f8c9` feat: OI divergence detection + volume spike cooldown

## Files modified
- indicators.py (all new functions + gather_indicators integration)
- analyst.py (new prompt sections for VP, funding, OI, volume spike)
- grid.py (roll_one_sided, VP-based levels, OI-influenced rolls, spike cooldown)
- calculator.py (time_adj + funding_adj params)
- main.py (trailing loss, peak_equity, time/funding adj in startup/resume)
- market_intel.py (OI history endpoint)
- config.yml (trailing_loss_pct: 0.04)
