---
title: "Karpathy's LLM Wiki Gist"
type: source
tags: [llm-wiki, karpathy, pattern, personal-knowledge]
sources: [karpathy-llm-wiki-gist.md]
created: 2026-04-06
updated: 2026-04-06
---

# Karpathy's LLM Wiki Gist

**Original:** [GitHub Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) by [[Andrej Karpathy]], 2026-04-03

## Summary

The original idea file that sparked the entire LLM wiki movement. Karpathy describes a pattern where the LLM incrementally builds and maintains a persistent wiki from raw source material — as opposed to RAG's stateless chunk-and-retrieve approach.

## Key Claims

1. **RAG has no accumulation** — every query rediscovers knowledge from scratch. The wiki pattern fixes this by creating a persistent, compounding artifact.
2. **Three layers** — raw/ (immutable sources), wiki/ (LLM-written pages), schema/ (AGENTS.md or CLAUDE.md that controls how the LLM behaves)
3. **Three workflows** — Ingest (process new sources), Query (ask questions), Lint (health-check for contradictions and gaps)
4. **~400K words, ~100 articles** on a single research topic — all LLM-generated, zero human writing
5. **Obsidian as IDE** — "Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase"
6. **Recommended tooling** — summarize (steipete), qmd (tobi), Obsidian Web Clipper

## Notable Insight

> "The wiki is a persistent, compounding artifact. The cross-references are already there. The contradictions have already been flagged. The synthesis already reflects everything you've read."

## Related
- [[LLM Wiki Pattern]] — the concept this source defines
- [[Raw-Wiki-Schema Architecture]] — the three-layer system
- [[Nick Spisak (second-brain)]] — implementation inspired by this gist
