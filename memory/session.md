# Session Handoff — 2026-03-26

## Active: DSL + Trailing SL Unification Plan

### What happened
- John worried about DSL and trailing TP interaction — did deep analysis
- Found 3 problems: unit mismatch (raw price % vs ROE%), trailing TP is dead code in DSL mode, dual trailing systems overlapping
- Designed replacement: DSL (profit protection) + Trailing SL (downside protection), remove trailing TP
- Also found: AI's `stop_loss_pct` is silently ignored in DSL mode (DSL hard_sl uses config, not per-position sl_pct)
- Created detailed plan v2 at `docs/plans/dsl-trailing-sl-plan.md`
- Plan re-verified against full codebase — 17 files, no cross-service impact

### Current state
- **Plan ready:** `docs/plans/dsl-trailing-sl-plan.md` — 17 files, implementation order defined
- **Not started:** No code changes yet
- **Services:** All running unchanged

### Key decisions
- Remove trailing TP entirely (dead code in DSL mode)
- Add trailing SL: trigger +0.5%, step 0.95%, ratchets up from entry
- DSL stays unchanged for profit protection
- Stagnation loosened: 60min → 90min
- Per-position sl_pct now used for trailing SL hard floor (was ignored in DSL mode)

### Not committed yet
- Previous sessions' changes still on working tree (position sizing refactor, leverage cleanup, pattern learning, OpenRouter switch)
- This session: plan only, no code changes
