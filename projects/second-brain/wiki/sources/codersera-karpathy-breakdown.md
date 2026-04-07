---
title: "Karpathy's Second Brain — Codersera Breakdown"
type: source
tags: [llm-wiki, analysis, second-brain]
sources: [karpathy-llm-wiki-codersera.md]
created: 2026-04-06
updated: 2026-04-06
---

# Karpathy's Second Brain — Codersera Breakdown

**Original:** [Codersera Article](https://ghost.codersera.com/blog/karpathy-llm-knowledge-base-second-brain/), 2026-04-03

## Summary

Independent article breaking down Karpathy's LLM wiki architecture. Covers the same core pattern but adds context about why it makes RAG obsolete for personal use.

## Key Claims

1. The system replaces ephemeral interaction with durable structured memory
2. At ~100 articles the entire wiki index fits in context window — no RAG needed
3. Markdown format is deliberate: most compact, LLM-readable, human-auditable format
4. Schema file (CLAUDE.md/AGENTS.md) is "co-evolved" — refined over time based on how the wiki develops
5. The human's main editorial role is refining instructions, not writing content

## Related
- [[LLM Wiki Pattern]] — concept analyzed in this article
- [[Karpathy's LLM Wiki Gist]] — the original gist being analyzed
