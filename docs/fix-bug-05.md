# BUG-05 Fix: Crash-Recovery Should Not ACK Decisions That Arrived During Downtime

**Date:** 2026-03-23

## Problem

On restart, the bot unconditionally ACKs the decision currently in `ai-decision.json`. If the AI trader wrote a NEW decision while the bot was crashed, the bot ACKs it without executing — the decision is lost forever.

### Scenario
1. Bot is processing decision A (timestamp `T1`)
2. Bot crashes
3. AI trader writes decision B (timestamp `T2`) to `ai-decision.json` while bot is down
4. Bot restarts, reads `last_ai_decision_ts=T1` from state
5. Bot ACKs decision B (wrong!) — decision B is lost, never executed

## Fix

Added a timestamp comparison in `_load_state()`. The saved `_last_ai_decision_ts` (from state file) represents the decision the bot was processing before crash. We compare it with the current decision file's timestamp:

- **Same timestamp** → Same decision bot was processing before crash → ACK (post-crash unblock, preserves existing IPC fix behavior)
- **Different timestamp** → New decision arrived during downtime → Skip ACK, let `_tick()` process it normally

## Code Change

`projects/autopilot-trader/executor/bot.py` — `_load_state()` method

Before: Unconditionally wrote ACK on restart if `_last_ai_decision_ts` existed.
After: Compares `current_decision["timestamp"]` with `_last_ai_decision_ts` before ACKing.

## Testing

1. Bot processes a decision → crashes → restarts → same decision still in file → should ACK (same as before)
2. Bot processes a decision → crashes → AI trader writes new decision → bot restarts → should NOT ACK, should process new decision on first tick
3. Bot starts fresh (no previous state) → no ACK written (same as before)
