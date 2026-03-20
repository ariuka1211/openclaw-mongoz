---
name: review-driven-dev
description: Use when implementing multi-step coding tasks that benefit from independent review. Use for: implementing features after writing a plan, complex refactors, tasks where catching spec deviations or quality issues early saves time. Do NOT use for: trivial changes (single file edits, config tweaks), simple scripts, or tasks already covered by systematic-debugging. Triggers on: implement this plan, build this feature, refactor, review my code, implement and review.
---

# Review-Driven Development

Implement via sub-agents with two-stage review: spec compliance first, then code quality.

## When to Use

- Implementation plan exists with independent tasks
- Tasks are mostly independent (not tightly coupled)
- Complexity warrants review (multi-file changes, new architecture)
- Quality matters (production code, shared tooling)

For single tasks or trivial changes, just implement directly.

## The Process

### Setup

1. Read the plan/requirements and extract tasks
2. Create a todo list for tracking progress

### Per Task (repeat for each task)

**Step 1: Dispatch Implementer**

Spawn a sub-agent with ONLY:
- The specific task description
- Relevant file paths and context
- Test requirements
- Any constraints or patterns to follow

Do NOT include: unrelated tasks, session history, or extra context.

**Step 2: Spec Review**

After implementer completes, spawn a spec reviewer sub-agent with:
- The original requirements/spec for this task
- The files the implementer changed
- Prompt: "Does this implementation match the spec? List any gaps, missing features, or deviations."

If spec reviewer finds issues → dispatch implementer with specific fixes, then re-review.

**Step 3: Code Quality Review**

After spec passes, spawn a code quality reviewer sub-agent with:
- The changed files
- Prompt: "Review code quality: correctness, error handling, naming, DRY, potential bugs, test coverage."

If quality reviewer finds issues → dispatch implementer with specific fixes, then re-review.

**Step 4: Mark Complete**

Mark task done in todo list. Move to next task.

### After All Tasks

Run a final review pass over the entire implementation:
- Does everything work together?
- Any integration issues?
- Final quality check across all changes.

## Sub-Agent Dispatch Pattern

When spawning implementer sub-agents, use this template:

```
Task: [specific task description]
Files to create: [exact paths]
Files to modify: [exact paths with line ranges if known]
Tests: [what to test]
Constraints: [patterns, style, dependencies to use/avoid]
Context: [only what's needed for THIS task]
```

When spawning reviewers, use:

```
Review type: [spec-compliance | code-quality]
Requirements: [the spec or quality criteria]
Files to review: [list changed files]
Focus on: [specific concerns]
```

## Integration with Existing Skills

- Pair with `systematic-debugging` if bugs arise during implementation
- Use after `writing-plans` skill produces an implementation plan

---

## 📊 Post-Run Logging

After completing a review cycle, log it:
```bash
bun skills/skill-monitor/scripts/log-run.ts review-driven-dev <success|failure|partial> \
  --task "what was implemented/reviewed" --duration <ms>
```
