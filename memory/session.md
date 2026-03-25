# Session Handoff — 2026-03-24 19:06 MDT

## What Happened
- **Scanner signal exploration:** Walked through full pipeline — scanner → signals.json → ai-trader → bot IPC. No DB in scanner, file-based IPC only.
- **Scanner improvements (deployed):**
  1. Direction-aware momentum: aligned with MA gets 1.3× boost, opposed gets 0.5× penalty
  2. OI trend signal replaces volume anomaly — tracks open interest changes via `oi-snapshot.json`
  3. Weights rebalanced: Funding 35% / MA 25% / OB 15% / Momentum 15% / OI 10%
  4. No more blind "long" fallback — uses funding spread direction as tiebreaker
  5. All output/display updated
- **Signal analyzer patched:** `signal_analyzer.py` updated from `volumeAnomalyScore` → `oiTrendScore`, weights aligned with scanner
- **Analyzer results (9 trades):** Funding all 100 (no differentiation), momentum NEGATIVE delta (-48), MA + OB positive. OI trend will populate with new data.
- **Pocket ideas:** Created `projects/autopilot-trader/docs/pocket-ideas.md` — signal history table idea documented

## Files Modified
- `signals/scripts/opportunity-scanner.ts` — OI trend, direction-aware momentum, new weights, direction fallback
- `signals/ai-trader/signal_analyzer.py` — volumeAnomalyScore → oiTrendScore, default weights updated
- `projects/autopilot-trader/docs/pocket-ideas.md` — new file

## State
- Bot: 3 positions (ICP, JTO, BRENTOIL), limit 8
- Scanner: restarted, running with new scoring. OI snapshot baseline created (107 markets)
- Services: all 3 running
- First OI snapshot saved — next scan will show real trend data

## Pending / Open Issue
- **SL volatility bug:** Stop loss distance = single-day (high-low)/price. Spikes give absurdly wide stops (CRCL 26.7%), quiet days give too-tight stops. Fix: use 7-day average daily range from OKX klines instead.
- Signal history table in pocket-ideas.md (not urgent)

## Next Steps
- Fix SL volatility (use rolling average range from OKX klines)
- Monitor OI trend data accumulation over next few scans
- Monitor if direction-aware momentum changes trade quality
- Commit all changes (branch + PR per protocol)
