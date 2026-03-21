# Session Summary: Watchdog Improvements (2026-03-19)

## Context
Multi-session work on improving watchdog alerts for OpenClaw sub-agents. Started with John requesting Telegram alert categorization, evolved into fixing sub-agent detection issues.

## What We Did
1. **Proposed alert category split** — Core Infra, Trading Bot, Agents (separate bots per category)
2. **Diagnosed sub-agent alert issues:**
   - Race condition: 30s polling missed fast sub-agents
   - Bare-bones completion alerts: only showed run ID
   - Duplicate detection: log tailer + runs.json polling competed
3. **Implemented fixes in watchdog-daemon.sh:**
   - Replaced 30s polling with `inotifywait` (filesystem event-driven)
   - Enriched state file with agent, model, task, startedAt
   - Split detection paths: tailer handles errors, runs checker handles successes
   - Added `completed_fast` flag for runs that finish between cycles

## Key Decisions
- **inotifywait over polling** — instant detection, 60s timeout as fallback
- **One bot per category** — cleaner routing, John can mute categories independently
- **Dedup by detection path** — tailer owns errors, runs checker owns successes

## Technical Notes
- inotify-tools installed at `/usr/bin/inotifywait`
- State file: `/tmp/openclaw-watchdog/last-runs-state.json`
- JSON escaping used for task field (handles special chars)
- `childSessionKey` format: `agent:{agentId}:subagent:{uuid}` — extract agent name from second segment

## Open Questions
- Implementation not fully tested — no active sub-agents at time of coding
- John will verify alerts work when sub-agents actually run
- Multi-bot architecture (3 separate bots) not yet implemented — code changes only

## Next Actions
- John to verify sub-agent alerts fire correctly with new detection
- If working, proceed with multi-bot token setup for alert categories
- Monitor for edge cases (very fast runs, OpenClaw restarts during detection)
