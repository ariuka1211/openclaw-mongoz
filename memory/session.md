# Session — 2026-03-25 09:44 MDT

## What Happened
- Discussed backtesting approaches for autopilot-trader. Reviewed Hummingbot's architecture (Controllers, triple barrier method, Quants Lab). Recommended starting with signal backtesting (triple barrier on scanner signals, no LLM needed).
- Cleaned up docs: deleted `signals/docs/` folder entirely (3 redundant spec files moved there earlier). Deleted 5 stale fix/research docs from `docs/` (fix-bug-02, fix-bug-08, fix-flaw-01, fix-state-01, lighter-research-findings). Kept: autopilot-trader.md, trading-lessons.md, pocket-ideas.md, unified-dashboard-plan.md.
- Created `docs/cheatsheet.md` — lean file map + key patterns for fast session startup. Updated AGENTS.md step 3 to read cheatsheet first instead of full autopilot-trader.md.

## Decisions
- Backtesting: start with signal-level backtesting using triple barrier method (Option 1 from discussion). Not implemented yet.
- Docs: single docs folder at `projects/autopilot-trader/docs/`. No more scattered doc folders.

## Pending
- Backtesting implementation (signal backtester — not started)
- Uncommitted changes from last session (equity fix, SL default fix) still need pushing
