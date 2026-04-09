# Advisor-Executor Architecture Plan

## Problem

In multi-turn agent conversations, the main model (advisor) inevitably starts doing executor work directly — editing files, running commands, writing code inline — instead of delegating. This happens because:

1. The advisor has the same tools as the executor — nothing stops it from acting
2. Context compaction kills the role — "you are an advisor" gets compressed away
3. It feels faster — explaining the plan seems slower than doing it
4. No penalty for drift — no feedback loop catches it

**You cannot fix this with better prompts.** Structural enforcement beats prompt engineering every time.

---

## Architecture

```
┌──────────────────────────┐     ┌──────────────────────────┐
│  ADVISOR (GLM-5.1)       │     │  EXECUTOR (Gemma 4)     │
│                          │     │                          │
│  Tools: READ-ONLY        │     │  Tools: WRITE + EXEC    │
│  - read                  │     │  - edit, write          │
│  - web_fetch, web_search │     │  - exec                  │
│  - memory_search         │     │  - git operations       │
│  - sessions_spawn        │     │                          │
│  - sessions_send         │     │  Cannot:                │
│  - git diff (read-only)  │     │  - spawn subagents      │
│  - take_snapshot         │     │  - message user          │
│                          │     │  - access memory         │
│  Cannot:                 │     │                          │
│  - edit, write files     │────▶│  Receives: structured   │
│  - run exec commands     │     │  executor prompt         │
│  - git commit/push       │     │                          │
│                          │◀────│  Returns: diff + results │
└──────────────────────────┘     └──────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Tool-Level Split (the critical fix)

Configure `openclaw.json` so the main agent literally cannot write:

```json
{
  "agents": {
    "list": [
      {
        "id": "main",
        "model": { "primary": "modal/zai-org/GLM-5.1-FP8" },
        "tools": {
          "deny": ["apply_patch", "edit", "write"],
          "exec": {
            "security": "full",
            "ask": "on"
          }
        }
      }
    ],
    "defaults": {
      "subagents": {
        "model": "openrouter/google/gemma-4-31b-it:free",
        "runTimeoutSeconds": 360,
        "tools": {
          "deny": ["sessions_spawn", "sessions_send", "message"]
        }
      }
    }
  }
}
```

**Note:** `exec` with `ask: "on"` means the advisor can still run read-only commands (cat, ls, grep, git diff) but every command requires John's approval. This is too restrictive for daily use — see Phase 4 for the refined approach.

**Completion criteria:** Main agent cannot edit/write files. Subagents cannot spawn further subagents or message the user.

### Phase 2: Compaction-Resistant Identity

Inject the advisor role into places that survive compaction:

1. **AGENTS.md** (always in project context, survives compaction):
```markdown
## 🔒 ADVISOR ROLE ENFORCEMENT

You are an ADVISOR. Your tools are READ-ONLY + SPAWN.
- ✅ Read files, search, plan, review diffs, approve
- ❌ Edit files, write files, run write commands
- If you catch yourself writing code → STOP → delegate to subagent
```

2. **Compaction customInstructions** (in `openclaw.json`):
```json
{
  "compaction": {
    "customInstructions": "CRITICAL: You are an ADVISOR. You READ and PLAN. You NEVER edit files or run write commands. You ALWAYS delegate execution to subagents. This rule survives compaction."
  }
}
```

**Completion criteria:** After compaction, advisor still knows its role.

### Phase 3: Executor Prompt Template

Create a skill (`delegator`) that structures the advisor's delegation:

```markdown
## Executor Prompt Template

Every delegation MUST include:

1. **FILES**: Exact paths, what each contains
2. **CONTEXT**: Relevant code snippets, current state (the advisor reads these first)
3. **CHANGES**: Precise before/after for each file
4. **CONSTRAINTS**: What NOT to touch, guardrails
5. **VERIFICATION**: How to confirm success (commands, expected output)

### Example executor prompt:

"""
File: projects/autopilot-trader/bot/bot.py
Current line 89: time.sleep(interval)
Bug: `interval` is in milliseconds but sleep() expects seconds.

Change:
- Line 89: `time.sleep(interval)` → `time.sleep(interval / 1000)`

Also update test at tests/test_bot.py:
- Line 34: `self.assertEqual(sleep_time, 30)` → `self.assertEqual(sleep_time, 0.03)`

Do NOT change any other lines. Run: python -c "import bot" to verify.
"""
```

**Completion criteria:** Every delegation uses this template. No vague prompts like "fix the bug."

### Phase 4: Refined Tool Access (practical balance)

The problem with Phase 1's `deny` approach: the advisor also needs `exec` for read-only commands like `git diff`, `grep`, `cat`, `systemctl status`. Denying `exec` entirely breaks the advisor's ability to review work.

**Solution:** Keep `exec` available but enforce the role through AGENTS.md + compaction instructions. The tool deny list covers the hard stops:

- **Hard deny:** `edit`, `write`, `apply_patch` — the advisor structurally cannot modify files
- **Soft enforce:** `exec` — advisor self-restricts to read-only commands (grep, cat, git diff, ls, systemctl status). Tracked via session monitoring.
- **Hard deny for executors:** `sessions_spawn`, `sessions_send`, `message` — executors can't escalate or talk to the user

**Completion criteria:** Advisor can read everything, cannot write files, can run read-only shell commands.

### Phase 5: Drift Detection & Monitoring

1. **Session log check** — periodic review of the advisor's tool calls:
   - If the advisor uses `exec` for write operations (echo > file, sed -i, etc.) → flag it
   - If the advisor uses `exec` more than N times without spawning → flag it

2. **Wrap-up check** — at session end, count:
   - Number of subagent spawns vs. direct exec calls
   - Ratio should be > 2:1 (more delegation than direct action)
   - If ratio is inverted → report drift

**Completion criteria:** Drift is caught and reported, not silently accepted.

---

## What Advisor Handles Directly

Not everything should be delegated. The advisor does these directly:

- Reading files, code, configs
- Web searches and research
- Planning and architecture decisions
- Reviewing diffs and verifying work
- Memory operations (search, get)
- Simple lookups (systemctl status, ps aux, df -h)
- BrowserOS snapshots and reads (not writes)

## What Executor Handles

Everything that modifies state:

- Editing files
- Writing new files
- Running commands that change state (systemctl restart, pip install, apt-get)
- Git operations (commit, push, checkout)
- Running tests
- File system operations (mkdir, cp, mv)

---

## When NOT To Use This Pattern

- **Simple one-off reads:** "What does bot.py do?" — just read it
- **Quick research:** "What's the latest on X?" — web_search directly
- **Emergency fixes:** "The bot is down!" — sometimes speed > architecture
- **Pair programming:** When John wants to think out loud together

---

## Metrics for Success

- [ ] Advisor never uses `edit` or `write` tools
- [ ] Advisor only uses `exec` for read-only commands
- [ ] Every delegation uses the executor prompt template
- [ ] Diffs are always reviewed in the main session before merge
- [ ] No silent regressions from executor work
- [ ] Token cost drops 3-5x vs. single-model approach on complex tasks
- [ ] Drift detection catches violations within 1 session

---

## Open Questions

- **How to handle `exec` for read-only?** Can't deny exec entirely. Options: (a) trust the role prompt, (b) wrap exec in a read-only proxy script, (c) require approval for every exec call
- **Executor model choice:** Gemma 4 free tier vs. GLM-5.1 for both. Tradeoff: cost vs. reliability
- **How to handle multi-file refactors?** Single executor prompt with all files, or sequential spawns?
- **What about git operations?** `git diff` is read-only (advisor), `git commit` is write (executor). But they're the same tool.
