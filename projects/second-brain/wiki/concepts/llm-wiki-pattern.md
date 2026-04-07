---
title: "LLM Wiki Pattern"
type: concept
tags: [pattern, knowledge-management, llm, second-brain, rag-alternative]
sources: [karpathy-llm-wiki-gist.md, karpathy-llm-wiki-codersera.md, nick-spisak-second-brain-impl.md]
created: 2026-04-06
updated: 2026-04-06
---

# LLM Wiki Pattern

A pattern for building personal knowledge bases using LLMs. Originated by [[Andrej Karpathy]] on April 3, 2026.

## Core Idea

Instead of RAG (retrieve chunks at query time), the LLM incrementally builds and maintains a persistent wiki from raw sources. Knowledge compounds rather than being re-derived on every query.

## Architecture

See [[Raw-Wiki-Schema Architecture]] for the three-layer system.

## Three Workflows

1. **Ingest** — raw source → LLM reads → summary → wiki updates → index/log
2. **Query** — ask question → LLM reads wiki → synthesized answer with citations
3. **Lint** — periodic health check: contradictions, orphan pages, missing links, gaps

## Why It Works

- Wiki index fits in context window at moderate scale (~100 sources)
- Synthesized pages are better than raw chunks for answering questions
- Cross-references exist before you query
- The human's role shifts from writing to curating and refining instructions

## Ecosystem

- [[Second Brain Implementation]] by [[Nick Spisak (second-brain)]] — turnkey npm install
- [[Graphify Knowledge Graph]] — knowledge graph variant (code-focused)
- Multiple formal specification papers and blog breakdowns

## Related
- [[Raw-Wiki-Schema Architecture]]
- [[RAG vs Wiki Pattern]]
- [[Andrej Karpathy]]
- [[Second Brain Implementation]]
