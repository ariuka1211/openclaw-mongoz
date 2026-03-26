# Session Handoff — 2026-03-26

## What We Did

### 1. Stagnation Alert Spam Fix (Complete ✅)
Bot was restarting every ~40s (exec tool killing it). Each restart created fresh TrackedPosition objects — in-memory `_stagnation_alerted` flags reset, alerts re-fired.

**Fixes applied:**
- Made `stagnation_alerted` + `tier_lock_alerted` persistent in saved state (3 files changed)
- Killed duplicate bot process
- Bot stable under systemd since 11:25
- Added rule #8 to AGENTS.md: never use exec for services

**Status:** Code on branch `fix/stagnation-alert-persistence` (PR pending). Memory + docs pushed to main.

### 2. Minor Bug Triage (Complete ✅)
Investigated 3 issues found in logs:
- **Quota tracking stale** — not a bug, working as designed (no standalone quota API on Lighter)
- **Invalid nonce** — one-time transient, SDK self-healed
- **LLM parser truncation** — real bug, max_tokens=1024 too low

### 3. LLM Truncation Fix (Complete ✅)
- Changed `max_tokens` default from 1024 → 5000 in `ai-decisions/llm_client.py`
- Restarted ai-decisions via systemctl
- 53 truncation events included 12 lost opens + 2 lost closes

**Status:** Not yet committed to branch.

## Current State
- All 3 services running: bot (32min+ uptime), scanner, ai-decisions
- No duplicate processes
- Bot stable, no restarts since 11:25
- AGENTS.md updated with rule #8

## What To Do Next
- Commit `llm_client.py` max_tokens change to branch + PR
- Monitor for truncation events (should drop to zero)
- Monitor bot stability
- Merge pending PR `fix/stagnation-alert-persistence`
