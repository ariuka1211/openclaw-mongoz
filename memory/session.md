# Session Handoff — 2026-03-26 09:03 MDT

## What Happened
- **Leverage audit** — traced full leverage flow across all 3 services + exchange
- **Root cause found:** bot never calls `update_leverage()` on Lighter. Cross margin auto-calculates leverage as `notional/equity`. Result: 50× BTC position opened with no enforcement.
- **Designed unified leverage architecture** — one config value, enforced on exchange, used everywhere
- **Comprehensive plan written** with 6 phases, 10 edge cases, rollback strategy
- **No code changes** — plan only, implementation deferred to next session

## Key Discovery
- `set_leverage()` via `SignerClient.update_leverage(market_id, CROSS_MARGIN_MODE(0), leverage)` must be called BEFORE `create_market_order()` in `open_position()`
- SDK: `imf = int(10_000 / leverage)` — sets Initial Margin Fraction on exchange
- Current saved state has poisoned leverage values (0.096x = notional/equity, not real leverage)
- Dashboard exposure calc is pre-existing broken (`entry * size * leverage` with leverage=0.1 = 10% of real)
- LLM prompts (`prompts/system.txt`, `prompts/decision.txt`) still tell AI to output leverage — must update

## Files Created
- `docs/plans/leverage-unification-plan.md` — 6-phase implementation plan (24 files, 10 edge cases)
- `docs/plans/leverage-unification-handoff.md` — quick-reference for next session

## Plan Summary
| Phase | What | Risk | Files |
|-------|------|------|-------|
| 4C (FIRST) | Update LLM prompts — remove leverage from schema | Low | 2 |
| 1 | Exchange enforcement — `set_leverage()` before every open | Medium | 2 |
| 4A-B | Remove leverage from AI safety/IPC/bot execution | Medium | 4 |
| 2 | Unify DSLState — one `leverage` field (no `effective_leverage`) | HIGH | 6 |
| 3 | Unify ROE — one formula everywhere | Medium | 3 |
| 5 | Clean up scanner leverage refs | Low | 2 |
| 6 | Config/docs/dashboard fix | Low | 5 |

## Edge Cases Found
- **EC-1:** Saved state has `leverage: 0.096` (was `effective_leverage = notional/equity`). Need backward-compat load with `<1.0` guard.
- **EC-2:** Dashboard exposure calc broken pre-existing (`entry*size*leverage` with levy=0.1). Fix: use `entry*size`.
- **EC-5:** Execution engine tick loop recalculates `effective_leverage = notional/equity` per tick. Must switch to reading exchange IMF.
- **EC-8:** `lighter_api.py:187` has `min(100/imf, default_leverage)` cap. Keep as safety net.
- Others: EC-3 (quota), EC-4 (state serialization), EC-6 (unverified positions), EC-7 (cold start adoption), EC-9 (multi-position same market), EC-10 (quota extraction)

## Open Items
- **Leverage unification implementation** — plan ready, needs next session
- **Existing 50× BTC position** — stays at 50× until closed, `set_leverage()` only affects future opens
- **Dashboard bug** — exposure shows 10% of real size (plan fixes in Phase 6)
- **Other uncommitted changes from 2026-03-25** — docs cleanup, config rename, dead code fixes

## Services Status
- All 3 services running: bot, scanner, ai-decisions
- No restarts needed (no code changes this session)
