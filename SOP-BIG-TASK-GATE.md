# SOP — Big Task Gate Protocol 🛠️

**Purpose:** Prevent runaway tasks, wasted tokens, and system instability. Every significant task must pass this gate before execution.

**When to use:** Any task that involves code changes, new services, config edits, deployments, spending money, or takes more than ~10 minutes of agent time.

---

## Pre-Flight Check (5 questions)

### 1. 🎯 WHY — Is it worth doing?
- What problem does this solve?
- What happens if we DON'T do it?
- Is there a simpler solution already available?
- **Kill criteria:** If the answer is "it'd be nice" or "someone else does it" — stop.

### 2. 💥 BLAST RADIUS — What gets affected?
- Which services/processes could break?
- Does it touch production (live trading bot)?
- Does it modify shared resources (openclaw.json, gateway, system config)?
- Does it affect other agents' work?
- **Kill criteria:** If it could take down the trading bot without a rollback plan — stop.

### 3. 💰 COST — Can we afford it?
- **Technical cost:** RAM, CPU, disk, API calls, rate limits
- **Financial cost:** Model tokens, API credits, server resources
- **Opportunity cost:** What can't we do while this is running?
- **Kill criteria:** If cost > value or competes with trading bot resources — stop or get approval.

### 4. ⏱️ TIME — How long will it take?
- Estimate: quick (<15 min) / medium (15-60 min) / long (>1 hour)
- Does it block the main session? (answer should be NO)
- Can it be broken into smaller phases?
- **Kill criteria:** If it blocks main session for >15 min without phasing — stop and spawn subagent.

### 5. 🤖 AGENTS — Who handles it?
- Match task to agent specialty:
  - **Infrastructure/deployment/monitoring** → Techno4k (system)
  - **Code/implementation/fixes** → Mzinho (coder)
  - **Research/analysis** → Blitz (research)
  - **Coordination/decisions** → Maaraa (main)
- Can one agent handle it or does it need handoffs?
- **Rule:** If a task crosses agent boundaries, Maaraa coordinates with clear handoff points.

---

## Execution Rules

### Session Hygiene
- ❌ Never run heavy work in main session — always spawn subagent
- ❌ Never poll in tight loops — use background exec or subagents
- ❌ Never block waiting on another agent — fire and check later
- ✅ Main session stays responsive at all times

### Change Safety
- Backup before destructive changes (DB, config, service state)
- Test before declaring done (syntax check, service status, curl test)
- Verify after changes (service running? bot still alive? disk OK?)
- Rollback plan: know how to undo before you start

### Token Budget Awareness
- Each session costs tokens — don't waste them
- Avoid re-reading files already in context
- Don't generate reports nobody asked for
- If a subagent is spinning (>5 min, no progress) — kill it

### Communication Protocol
- Report BEFORE starting: "I'm going to do X, it affects Y, estimated 15 min"
- Report AFTER completion: "Done. Result: X. Services: Y status. Warnings: Z."
- Report IMMEDIATELY if something goes wrong
- Don't silently deploy automation — always confirm

### Never Do (Hard Rules)
- ❌ Gateway restarts without explicit permission
- ❌ Edits to openclaw.json without telling John what to change
- ❌ Auto-scripts/systemd services without approval
- ❌ `rm` instead of `trash`
- ❌ Sharing private data, credentials, or keys
- ❌ Running trading bot changes without testing
- ❌ Committing to GitHub without review

---

## Quick Reference (Decision Matrix)

| Scenario | Action |
|---|---|
| Simple fix, <5 min, no risk | Just do it, report after |
| Code change, testing needed | Spawn Mzinho, test, report |
| Server/config change | Check blast radius, backup, execute |
| New service/cron/automation | Get approval before deploying |
| Research question | Spawn Blitz, get results |
| Unknown complexity | Time-box to 10 min, reassess |
| Trading bot affected | STOP — get John's approval |
| Costs money or tokens | STOP — get John's approval |

---

## Escalation

- **Level 0:** Agent handles it (routine ops)
- **Level 1:** Agent proposes → Maaraa approves (moderate risk)
- **Level 2:** Maaraa proposes → John approves (high risk, costs money, affects trading)

Default: when in doubt, escalate up.

---

_Last updated: 2026-03-21 01:36 UTC_
