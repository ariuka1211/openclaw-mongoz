---
title: "Nick Spisak's Second Brain Implementation"
type: source
tags: [llm-wiki, implementation, agent-skills, second-brain]
sources: [nick-spisak-second-brain-impl.md]
created: 2026-04-06
updated: 2026-04-06
---

# Nick Spisak's Second Brain Implementation

**Original:** [GitHub - NicholasSpisak/second-brain](https://github.com/NicholasSpisak/second-brain), 2026-04-04

## Summary

A turnkey implementation of Karpathy's LLM Wiki pattern. Ships as four npm-installable Agent Skills: setup wizard, ingest, query, and lint.

## Key Claims

1. **Zero configuration needed** — `npx skills add` installs everything
2. **Agent-agnostic** — works with Claude Code, Codex, Cursor, Gemini CLI, and 40+ others
3. **Built-in tooling** — recommends summarize, qmd, and agent-browser
4. **Obsidian-friendly** — designed to be browsed in Obsidian with graph view
5. **Structured wiki** — sources/, entities/, concepts/, synthesis/ subdirectories

## Reception
1,361 likes, 142 retweets on launch tweet

## Related
- [[LLM Wiki Pattern]] — pattern this implements
- [[Agent Skills]] — distribution mechanism used
- [[Nick Spisak (second-brain)]] — creator/author
