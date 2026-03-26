# Session Handoff — 2026-03-26

## What Happened
Completed **position sizing refactor** across all 3 services (scanner, bot, ai-decisions). 4 phases executed via subagents with manual verification.

## Architecture Change
- **Scanner** → pure signal scoring only (removed equity fetch, position sizing, safety checks)
- **Bot** → new `PositionSizer` class, dynamic equity-based sizing (2% risk, 15% margin, 1.5 R:R)
- **AI** → reads equity from `ai-decisions/state/equity.json` instead of signals config
- Config: `max_position_usd: 15` replaced with `max_risk_pct`, `max_margin_pct`, `min_risk_reward`, `max_concurrent_signals`

## Key Numbers
- On $60 equity: BTC=$25.53 notional, $1.20 risk (2%) — was $15 with 0.31% risk
- Scales dynamically: $500 equity → $212 BTC positions
- R:R filter rejects bad setups (e.g. WIF at 12.4% vol, 0.2:1 R:R)

## Files Changed
- **Scanner (5 modified, 2 deleted):** config.ts, types.ts, main.ts, output.ts, pipeline.test.ts; deleted position-sizing.ts + its test
- **Bot (7 changed):** NEW position_sizer.py; modified config.py, config.yml, signal_handler.py, executor.py, conftest.py, test_signal_processor.py
- **AI (5 modified):** data_reader.py (added read_equity), prompt_builder.py, safety.py, cycle_runner.py, ai_trader.py + 2 test files

## Services Status
All 3 running clean after restart. Scanner producing new format signals. AI making decisions with new equity source. Bot tracking 4 positions at $62.38 equity.

## Not Yet Committed
All changes on working tree. Need: branch, commit, PR, merge.

## Next Steps
- Commit + PR for position sizing refactor
- Monitor first trades with new sizing (expect larger positions)
- Consider: should min_risk_reward be 1.5 or lower? Currently rejects many high-vol signals
