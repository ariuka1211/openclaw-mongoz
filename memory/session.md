# Session Handoff — 2026-03-24 22:28 MDT

## What Happened
- **DSL overhaul:** Fixed trailing_buffer_roe bug (config missing it → used percentage fallback), added 3% and 30% tiers, stagnation starts at first tier trigger, hard SL 2.0% → 1.25%, 20% tier 1→2 breaches
- **Cross margin ROE:** Added effective leverage (notional/equity) for AI prompt display. Initially applied to DSL too but broke tiers for small positions — reverted DSL to use config leverage (5x/10x)
- **AI trader prompt fix:** Added slots-available encouragement, softened hold-first language. LLM immediately started opening positions instead of holding
- **ROE leverage bug:** ai-result.json didn't include leverage per position. Added it + fixed _calc_roe() to multiply by leverage
- **Position sizing:** Changed from 5% to 10% of equity (~$6 per position). Updated config, system prompt, safety layer
- **Balance check restored:** Uncommented balance fetch, now gets $59.62 on startup

## Services
All 3 running: lighter-bot, lighter-scanner, ai-trader

## Current State
- Bot: 4 positions (ETHFI $100, ENA $2, APT $1.78, BCH $1.91) — last 3 are small old positions
- Equity: $59.62
- Max positions: 8
- DSL: 6 tiers with trailing_buffer_roe, config leverage for DSL, effective leverage for display

## Pending
- Verify next new position opens at ~$6 (10% sizing)
- Old small positions can stay — new opens will use correct sizing
- SL volatility bug (rolling average) still open from prior sessions
- Branch fix/dsl-alert-roe still pending PR merge

## Next Steps
- Monitor first new position open to confirm $6 sizing
- Consider closing old small positions if they clutter the portfolio
- Commit tonight's changes (DSL, cross margin, prompt, sizing)
