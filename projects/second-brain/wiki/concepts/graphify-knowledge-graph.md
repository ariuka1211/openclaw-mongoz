---
title: "Graphify Knowledge Graph"
type: concept
tags: [knowledge-graph, code-analysis, graphify, tool]
sources: []
created: 2026-04-06
updated: 2026-04-06
---

# Graphify Knowledge Graph

A knowledge graph tool for codebases, papers, and documents. Built by safishamsi.

**Link:** https://github.com/safishamsi/graphify

## Key Claims

1. Runs code through AST parser (tree-sitter) to extract structure without LLM
2. Claude subagents extract concepts from docs/papers/images in parallel
3. Builds NetworkX graph with Leiden community detection
4. No embeddings — clustering is graph-topology-based
5. Claims 71.5x fewer tokens per query vs reading raw files
6. Can process code, PDFs, markdown, diagrams, screenshots, whiteboard photos
7. Outputs interactive HTML, queryable JSON, plain-English report

## How It Differs from LLM Wiki

- **Graphify** — graph of nodes + edges, confidence scores, optimized for understanding codebases
- **LLM Wiki** — human-readable markdown wiki, optimized for personal knowledge and research

Both inspired by Karpathy's pattern but serve different purposes.

## Related
- [[LLM Wiki Pattern]] — inspiration
- [[RAG vs Wiki Pattern]] — alternative approach
