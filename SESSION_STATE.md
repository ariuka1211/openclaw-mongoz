# SESSION_STATE.md — Active Tasks

## LCM Queue Cleanup (Mar 16, 21:30 UTC)

**Status:** Mid-task — LCM disabled, gateway restarting

**What we did:**
1. Found 513 pending messages, 83 in current session — LCM permanently behind
2. Deleted 3 duplicate orphaned leaf summaries (same 20:53–20:53 time range)
3. Disabled LCM (`enabled: false` in openclaw.json) — awaiting gateway restart

**Next steps:**
1. Re-enable LCM after implementing tool-call filtering
2. LCM has NO built-in role filter — tool results mapped to `assistant` role in assembler.ts
3. Need to filter at OpenClaw ingestion level or modify assembler to skip tool messages
4. Goal: LCM should only summarize Telegram chat (user + assistant messages), NOT tool calls

**Key files:**
- `/root/.openclaw/extensions/lossless-claw/src/assembler.ts` — maps tool→assistant role (line 120)
- `/root/.openclaw/extensions/lossless-claw/src/db/config.ts` — LcmConfig (no role filter option)
- `/root/.openclaw/openclaw.json` — LCM currently disabled

**User preference:** No more 2-min disappearances without heads up
