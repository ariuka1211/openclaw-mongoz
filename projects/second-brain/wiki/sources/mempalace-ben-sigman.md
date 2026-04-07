---
title: "MemPalace by Ben Sigman"
type: source
tags: [memory, ai-tools, mempalace, open-source, local-memory, aaaak]
sources: [mempalace-ben-sigman.md]
created: 2026-04-07
updated: 2026-04-07
---

# MemPalace — Open-Source AI Memory Tool

**Original:** [X Post by @bensig](https://x.com/bensig/status/2041229266432733356) and [GitHub](https://github.com/milla-jovovich/mempalace), 2026-04-06

## Summary

Local, open-source memory system that stores AI conversations and project files, making them permanently searchable. Claims 96.6% recall on LongMemEval (100% with reranking) — higher than any published result.

## Architecture

1. **The Palace** — Hierarchical: Wings (people/projects) → Rooms (subjects) → Closets (summaries) → Drawers (original files)
2. **AAAK** — Custom 30x compression shorthand dialect for AI agents. Zero information loss, ~170 tokens for entire world state
3. **Three mining modes:** Projects (code/docs), Convos (chat exports), General (auto-classifies decisions/milestones/preferences)

## Three Workflows

1. **Mine** — `mempalace mine ~/projects/` or `mempalace mine ~/chats/ --mode convos`
2. **Search** — CLI or Python API: `search_memories("auth decisions", palace_path="~/.mempalace/palace")`
3. **Wake-up** — `mempalace wake-up > context.txt` — loads ~170 tokens of critical facts for local models

## Why It Matters

- **Store everything** vs AI deciding what's worth remembering (contrast to [[RAG vs Wiki Pattern]])
- Palace structure alone = 34% retrieval boost
- **$10/yr** to remember everything vs $507/yr for LLM summaries
- Runs 100% local — ChromaDB + local models, zero cloud
- MCP integration (19 tools for Claude)

## Caveats

- GitHub author is "milla-jovovich" — may be attention-grabbing pseudonym
- Benchmark claims unverified by third party
- AAAK only in closets (not full files) in current version

## Related
- [[RAG vs Wiki Pattern]] — different approach to memory
- [[LLM Wiki Pattern]] — similar philosophy: persistent, not retrieved-at-query-time
- [[Memvid]] — alternative local vector memory format we use
