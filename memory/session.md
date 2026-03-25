# Session Handoff — 2026-03-24 19:30 MDT

## What Happened
- **DSL alert ROE bug found:** Two Telegram alerts for same close showed different ROE numbers (-21% vs -2.1%). Alert 1 used leveraged ROE, Alert 2 and 3 used raw price movement but labeled it "ROE".
- **Fix: ROE → dollar amounts:** Replaced ROE% with Position ($), P&L ($), Margin ($) in all three DSL alerts (trigger, close confirmation, SL failed). More useful than abstract ROE%.
- **Branch:** `fix/dsl-alert-roe` pushed, PR pending merge.

## Files Modified
- `projects/autopilot-trader/executor/bot.py` — DSL alerts (lines ~2748, ~2851, ~2941): replaced ROE with notional_usd, pnl_usd, margin_usd

## State
- Bot: 1 position (CRCL), limit 3 (was 8, session.md says 8 but config was changed)
- Services: all 3 running
- Bot restarted to pick up alert fix

## Pending / Open Items
- SL volatility bug (use rolling average range from OKX klines) — from prior session, not addressed this session
- Signal history table idea in pocket-ideas.md
- Merge PR `fix/dsl-alert-roe`

## Next Steps
- Monitor new dollar-amount alerts on next close
- Fix SL volatility bug (rolling average)
- Commit scanner + analyzer changes from prior session (still uncommitted?)
