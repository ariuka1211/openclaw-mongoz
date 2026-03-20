# Skill Monitor 📊

Track skill execution history and detect degradation patterns. Skills rot silently — this catches it early.

## When to Use

- **After running any skill** → log the execution
- **Periodically** (weekly/heartbeat) → run audit to check health
- **When outputs seem off** → check audit for degradation trends
- **When building new skills** → initialize tracking from day one

## Quick Start

```bash
# First time only — initialize the database
bun skills/skill-monitor/scripts/setup.ts
```

## Logging a Run

After any skill completes, log it:

```bash
bun skills/skill-monitor/scripts/log-run.ts <skill-name> <success|failure|partial> \
  --task "what you used it for" \
  --duration <ms> \
  --error "error message if failed" \
  --feedback "user feedback if any"
```

**Examples:**
```bash
# Success
bun skills/skill-monitor/scripts/log-run.ts deep-research-pro success \
  --task "Research Cognee self-improving skills" --duration 45000

# Failure
bun skills/skill-monitor/scripts/log-run.ts skill-monitor failure \
  --error "DB not initialized, ran setup.ts first"

# With user feedback
bun skills/skill-monitor/scripts/log-run.ts deep-research-pro success \
  --task "Market analysis" --feedback "Good but too verbose, tighten next time"
```

## Running an Audit

```bash
# Full audit
bun skills/skill-monitor/scripts/audit.ts

# Focus on one skill
bun skills/skill-monitor/scripts/audit.ts --skill deep-research-pro

# Different time window
bun skills/skill-monitor/scripts/audit.ts --days 7

# Machine-readable output
bun skills/skill-monitor/scripts/audit.ts --format json
```

## Audit Output

The audit reports:
- **Success rate** per skill (all-time and trend)
- **Degradation detection** — comparing last 7 days vs previous 7 days
- **Common errors** — top 3 error patterns per skill
- **Untracked skills** — skills with no logged runs
- **Recent failures** — last 10 failures with context

## Interpreting Trends

| Trend | Meaning | Action |
|-------|---------|--------|
| 📈 improving | Success rate rising | Keep current approach |
| ➡️ stable | Consistent performance | Monitor, no action needed |
| 📉 degrading | Success rate falling | Investigate root cause |
| Untracked | No data yet | Start logging |

## Logging Hook (Self-Discipline)

**Agent rule:** After completing any skill-driven task, add a one-line log command. This is the cost of self-improvement — 5 seconds per skill use.

If the DB isn't initialized, the log command will tell you. Run `setup.ts` first.

## Data Location

- DB: `~/.openclaw/workspace/data/skill-monitor/skill-runs.db`
- Backed up with workspace

## What Counts as "Failure"?

- Skill instructions produced wrong/outdated results
- Tool calls within skill failed (API changed, endpoint down)
- Skill was selected but shouldn't have been (routing failure)
- Output required significant manual correction

## Human-in-the-Loop

This system **detects and flags** problems. It does NOT auto-edit SKILL.md files.
When issues are found, the agent should:
1. Present the findings to the user
2. Propose a specific fix
3. Get approval before editing the skill file
4. Log the fix attempt and result
