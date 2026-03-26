# Session Handoff — 2026-03-26

## What We Did

### Stagnation Alert Fix (Complete ✅)
Investigated and fixed repeated "DSL STAGNATION TIMER STARTED" alerts caused by bot restarts.

**Root cause:** Bot was being killed/restarted every ~40s by OpenClaw exec tool (previous session launched bot via exec instead of systemd). Each restart created fresh `TrackedPosition` objects with reset in-memory `_stagnation_alerted` flags.

**Fix 1 — Persistent alert flags (code change):**
- Added `stagnation_alerted` and `tier_lock_alerted` as proper dataclass fields on `TrackedPosition`
- Save/restore both flags in `state_manager.py`
- Replaced `getattr(pos, '_stagnation_alerted', False)` with direct field access in `position_tracker.py`
- 219/219 tests pass

**Files changed:**
- `bot/core/models.py` — 2 new fields
- `bot/core/position_tracker.py` — 4 lines swapped (getattr → field access)
- `bot/core/state_manager.py` — save + restore both flags

**Fix 2 — Kill duplicate process:** Two bot processes were running. Killed the manual one, kept systemd-managed one.

**Fix 3 — Silent restarts:** Investigated — no code bug. Caused by exec tool killing processes on session end. Bot has been stable since.

**Rule added to AGENTS.md:** Rule #8 — never use exec for services, always `systemctl restart`.

## Current State
- Bot running stably under systemd, no restarts
- Stagnation alerts now fire once per position (persist across restarts)
- All tests passing
- Changes not yet committed/pushed

## What To Do Next
- Monitor bot for continued stability
- Changes need to be committed to a branch + PR
- No immediate action needed
