---
title: "Agent Skills"
type: concept
tags: [distribution, agent-ecosystem, npm]
sources: [nick-spisak-second-brain-impl.md]
created: 2026-04-06
updated: 2026-04-06
---

# Agent Skills

An open distribution format for AI agent capabilities. Think "npm for agent tools."

## How It Works

- `npx skills add <github-repo>` — clones and installs skills into your agent
- Skills are folders with SKILL.md + references
- Works with Claude Code, Codex, Cursor, Gemini CLI, and 40+ agents
- https://agentskills.io

## Why It Matters

Makes complex AI patterns installable by anyone. Instead of copy-pasting configs or reading docs, you run one command and the agent knows how to do the thing.

The [[Second Brain Implementation]] uses Agent Skills as its distribution mechanism.

## Related
- [[Second Brain Implementation]]
- [[LLM Wiki Pattern]]
