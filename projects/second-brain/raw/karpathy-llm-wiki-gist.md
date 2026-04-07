# Karpathy's LLM Wikipedia Pattern

**Source URL:** https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f  
**Author:** Andrej Karpathy  
**Date:** 2026-04-03  
**Type:** GitHub Gist

## Content

A pattern for building personal knowledge bases using LLMs. This is an idea file, designed to be copy-pasted to your LLM agent (OpenAI Codex, Claude Code, Cursor, etc.).

### Core Problem

Most people's experience with LLMs and documents looks like RAG: upload files, retrieve chunks at query time, generate answer. The LLM is rediscovering knowledge from scratch on every question. There's no accumulation.

### The Idea

Instead of just retrieving from raw documents, the LLM incrementally builds and maintains a **persistent wiki** — a structured, interlinked collection of markdown files that sits between you and the raw sources. When new sources arrive, the LLM reads them, extracts key information, and integrates it into the existing wiki — updating entity pages, revising topic summaries, flagging contradictions.

### Three Layers

1. **Raw sources** — immutable source documents (articles, papers, images, data). LLM reads but never modifies. Source of truth.
2. **The wiki** — LLM-generated markdown pages (summaries, entities, concepts, comparisons, synthesis). LLM owns this layer entirely.
3. **The schema** — a document (CLAUDE.md for Claude Code, AGENTS.md for Codex, etc.) telling the LLM how the wiki is structured, what conventions to follow, and what workflows to follow. Co-evolved over time.

### Three Workflows

- **Ingest:** Drop source into raw/ → LLM reads, discusses takeaways, writes summary, updates index, touches 10-15 wiki pages
- **Query:** Ask questions against wiki → LLM synthesizes answer with citations, good answers filed back as pages
- **Lint:** Periodic health check: contradictions, stale claims, orphan pages, missing cross-references, data gaps

### Two Special Files

- **index.md** — catalog of everything with links + summaries. Works at ~100 sources, hundreds of pages without RAG/embeddings.
- **log.md** — append-only record: ingests, queries, lint passes. Parseable with grep.

### Obsidian Integration

- Obsidian Web Clipper for saving web articles as markdown
- Download images locally for LLM vision reference
- Graph view to see wiki shape
- Obsidian is the IDE; LLM is the programmer; wiki is the codebase

### Why Skip RAG

- RAG chunks documents and loses context
- Wiki articles are already synthesized summaries, written by LLM that read the full context
- At moderate scale the wiki index fits in context window, so retrieval is just reading the index

### Tooling Mentioned

- **summarize** (by @steipete) — summarize links, files, and media
- **qmd** (by @tobi) — local search engine for markdown with hybrid BM25/vector search and LLM re-ranking
- **Obsidian Web Clipper** — browser extension for clipping articles
- **Obsidian graph view** — see wiki connectivity visually

### Results

One research topic had grown to ~100 articles and ~400,000 words (longer than most PhD dissertations) without Karpathy writing a single word.

### Use Cases

Karpathy mentions: personal goal tracking, research deep-dives, reading companion (like fan wikis), business/team knowledge management, competitive analysis, trip planning, course notes, hobby deep-dives.
