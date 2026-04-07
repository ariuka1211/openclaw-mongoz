---
title: "RAG vs Wiki Pattern"
type: concept
tags: [rag, wiki, comparison, architecture, retrieval]
sources: [karpathy-llm-wiki-gist.md, karpathy-llm-wiki-codersera.md]
created: 2026-04-06
updated: 2026-04-06
---

# RAG vs Wiki Pattern

A comparison of the two approaches to giving LLMs access to personal documents.

## RAG (Retrieval-Augmented Generation)

- Chunks documents → embeds → vector search at query time → injects chunks into prompt
- **Pro:** Scales to millions of documents
- **Con:** Loses context (chunking fragments paragraphs from their surrounding text)
- **Con:** No accumulation — every query starts from scratch
- **Con:** Retrieval noise and hallucination risk

## Wiki Pattern

- LLM reads full documents → writes synthesized pages → interlinks → maintains over time
- **Pro:** Full context preserved (LLM reads complete source before summarizing)
- **Pro:** Knowledge compounds — cross-references and synthesis improve over time
- **Pro:** Auditable — human can read the raw markdown
- **Pro:** Contradictions flagged, not ignored
- **Con:** Scale-limited (index must fit in context window, ~hundreds of pages)
- **Con:** Requires LLM calls to maintain (ongoing cost)

## Karpathy's Verdict

> "By the time you have ~100 sources and a few hundred wiki pages, the entire index fits in a modern LLM's context window. No retrieval system needed."

## When to Use What

- **Wiki** for personal knowledge, research, learning (< ~1000 pages)
- **RAG** for enterprise knowledge bases, millions of documents

## Related
- [[LLM Wiki Pattern]]
- [[Raw-Wiki-Schema Architecture]]
