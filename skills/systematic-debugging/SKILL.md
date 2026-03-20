---
name: systematic-debugging
description: Use when encountering any bug, test failure, unexpected behavior, performance problem, or build failure. Use BEFORE proposing fixes, especially when under time pressure, when "obvious" fixes seem tempting, or when previous fixes didn't work. Triggers on words like: bug, broken, failing, error, crash, not working, debug, investigate, why is, what's wrong.
---

# Systematic Debugging

Always find root cause before proposing fixes. Symptom fixes create new bugs.

## The Iron Law

**NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.**

If Phase 1 is incomplete, do not propose fixes. Period.

## Four Phases

Complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

Before any fix:

1. **Read error messages completely** — stack traces, line numbers, error codes. Don't skip past them.
2. **Reproduce consistently** — exact steps, every time. If not reproducible, gather more data first.
3. **Check recent changes** — `git diff`, recent commits, new dependencies, config changes, environment differences.
4. **Gather evidence at component boundaries** — for multi-component systems (API → service → database), add diagnostic logging at each boundary. Log what enters and exits each component.

### Phase 2: Hypothesis Formation

Based on evidence from Phase 1:

- Form specific hypothesis about root cause
- State what evidence supports this hypothesis
- Predict what else should be true if hypothesis is correct
- Identify what would disprove the hypothesis

### Phase 3: Hypothesis Testing

Test hypothesis with minimal changes:

- Make one change at a time
- Verify or falsify the hypothesis
- If falsified, return to Phase 2 with new evidence
- Document what you tried and what happened

### Phase 4: Fix Implementation

Only after Phase 3 confirms root cause:

- Implement minimal fix addressing root cause (not symptom)
- Add regression test
- Verify fix works
- Verify no new issues introduced

## Anti-Patterns

Do not:

- Guess at fixes without investigation
- Apply multiple fixes at once
- Skip reproduction step
- Fix symptoms instead of root cause
- Say "let me try X" without evidence pointing to X

## When Pressure Is High

Under time pressure, systematic debugging is **faster** than guessing:
- Guessing creates new bugs → more debugging time
- Systematic approach finds root cause → one fix
- Document your investigation briefly so others can follow

---

## 📊 Post-Run Logging

After completing a debugging session, log it:
```bash
bun skills/skill-monitor/scripts/log-run.ts systematic-debugging <success|failure|partial> \
  --task "what was being debugged" --duration <ms>
```
