# LEARNINGS.md — Don't Repeat These 🔴

**After any mistake:** Log it here immediately. Increment repeat counter if it matches an existing pattern.

**Graduation tiers:** 🔴 Active → 🟡 Watch (14+ days no repeat) → 🟢 Retired (30+ days, archived)

**Before acting:** See Pre-Action Protocol in AGENTS.md for category mappings.

---

## 🔴 Active

### DEPLOY

### M002: Restart service without verifying it works
- **Root Cause:** Restart ≠ working. Process can start but serve broken content.
- **Pattern:** deploy
- **Prevention:** After ANY restart: warn John first, checkpoint state, curl localhost:PORT to verify actual content
- **Repeats:** 3 | **Last:** 2026-03-16

### M032: Restart gateway on ambiguous permission
- **Root Cause:** John said "okey" — assumed it meant "go ahead" instead of asking for explicit yes. Red line in SOUL.md says "NEVER restart without explicit permission."
- **Pattern:** deploy
- **Prevention:** Always ask for explicit "yes" or "go ahead" before any restart. Ambiguous responses get a clarification question, not action.
- **Repeats:** 1 | **Last:** 2026-03-18

### M001: Deploy without understanding build system
- **Root Cause:** Assumed pnpm build → restart = working. Did not understand standalone mode file layout.
- **Pattern:** deploy
- **Prevention:** After ANY build: check what files the runtime actually reads, verify static assets exist, test the page before declaring done
- **Repeats:** 1 | **Last:** 2026-03-15

### DEBUG

### M020: Assume root cause without investigation
- **Root Cause:** Jumped to conclusion. Did not check simpler thing first.
- **Pattern:** debug
- **Prevention:** Check if files/resources actually exist at expected path. Verify simplest explanation first.
- **Repeats:** 2 | **Last:** 2026-03-15

### SPAWN

### M061: Spawn subagent without checking existing code
- **Root Cause:** Spawned Blitz to research Lighter API without checking that `lighter-copilot/` already had a full working trading bot with API keys, proxy, and all the endpoints we needed. Wasted a subagent run on information we already had.
- **Pattern:** spawn
- **Prevention:** BEFORE spawning any research/build subagent: `find /root/.openclaw/workspace -not -path "*/node_modules/*" | grep -i <keyword>`. List existing relevant files in the spawn prompt. Tell subagent to extend existing code, not build from scratch.
- **Repeats:** 1 | **Last:** 2026-03-19

### M031: Try random free models
- **Root Cause:** Assumed free models would work without checking allowlist
- **Pattern:** spawn
- **Prevention:** Check models.allowlist before using ANY model. Only use configured models.
- **Repeats:** 2 | **Last:** 2026-03-14

### L002: Doing code work in main session instead of delegating
- **Root Cause:** Started editing bot.py, running processes, adding debug prints directly instead of spawning Mzinho. Got pulled into debugging rabbit hole. Froze the session with 31k+ tokens and rate limits. John had to restart gateway.
- **Pattern:** spawn
- **Prevention:** NEVER edit, debug, read code, or run processes in main session. Maaraa is ORCHESTRATOR only. Say "I'll delegate this to Mzinho" and spawn immediately. If you catch yourself editing a file, STOP and spawn.
- **Repeats:** 1 | **Last:** 2026-03-20

## 🟡 Watch

- **M051:** Install skills without auditing (last: 2026-03-14, repeats: 1)
- **M050:** Create auto-scripts without approval (last: 2026-03-13, repeats: 1)
- **M010:** Edit openclaw.json without reading docs (last: 2026-03-14, repeats: 1)
- **M011:** Use wrong model prefix/format (last: 2026-03-13, repeats: 1)
- **M021:** Confuse correlation with causation (last: 2026-03-15, repeats: 1)
- **M003:** Build replacements for existing systems (last: 2026-03-14, repeats: 1)
- **M061:** Use wrong CLI command names (last: 2026-03-14, repeats: 1)
- **M030:** Batch-spawn subagents (last: 2026-03-14, repeats: 1)
- **M040:** Thinking loop - burn tokens on repeated reasoning (last: 2026-03-13, repeats: 1)

## 🟢 Retired

_(empty — nothing graduated yet)_

