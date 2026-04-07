---
title: "Raw-Wiki-Schema Architecture"
type: concept
tags: [architecture, design-pattern, three-layer]
sources: [karpathy-llm-wiki-gist.md, karpathy-llm-wiki-codersera.md]
created: 2026-04-06
updated: 2026-04-06
---

# Raw-Wiki-Schema Architecture

The three-layer architecture at the heart of the LLM Wiki pattern.

## Layer 1: Raw/ (Immutable Sources)

- Append-only source documents
- LLM reads but never modifies
- Source of truth for everything
- Ingest via Obsidian Web Clipper, manual drop, or archive downloads

## Layer 2: Wiki/ (LLM-Owned)

- LLM writes, updates, maintains
- Subdirectories:
  - **sources/** — one summary per ingested source
  - **entities/** — people, orgs, tools
  - **concepts/** — ideas, frameworks, theories
  - **synthesis/** — comparisons, analyses, themes
- Two special files:
  - **index.md** — master catalog with links + summaries
  - **log.md** — append-only operation record

## Layer 3: Schema (Agent Config)

- CLAUDE.md, AGENTS.md, or equivalent
- Tells the LLM how to behave: conventions, structure, workflows
- Co-evolved over time by human + LLM
- The human's primary editorial role: refining the schema, not writing content

## Key Design Decisions

1. **Markdown format** — most compact, LLM-readable, human-auditable
2. **Wikilinks** — `[[wikilink]]` for all internal references
3. **YAML frontmatter** — tags, sources, created/updated dates
4. **No RAG** — at moderate scale the wiki fits in context window

## Related
- [[LLM Wiki Pattern]]
- [[Second Brain Implementation]]