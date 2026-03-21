# Unified Memory System — Architecture Plan

## Problem: Three Overlapping Systems

| System | Purpose | Data | Search | Status |
|--------|---------|------|--------|--------|
| **QMD** (memory_search) | Semantic search over markdown | MEMORY.md + memory/*.md | embeddinggemma-300m, local | ✅ Active, built-in |
| **LCM** (lossless-claw) | Conversation history + compression | 3235 messages, 49 summaries | FTS5 full-text (porter) | ✅ Active, plugin |
| **Memory-core** | Vector search + auto-capture | 139 chunks, same markdown files | all-MiniLM-L6-v2, 384-dim | ⚠️ Built, dormant |
| **Manual markdown** | Human-readable source of truth | MEMORY.md, memory/*.md, learning.md | None (I read them) | ✅ Active |

### Conflicts
1. **QMD vs Memory-core recall** — Both do semantic search over same markdown files with different embedding models. Running both = conflicting results.
2. **Memory-core capture vs LCM** — Both store conversation-derived info. Capture extracts facts; LCM stores summaries. Running both = duplication.
3. **Memory-core capture vs manual memory** — I already decide what's important and write it down. Auto-capture might add noise.
4. **Three vector/search databases** — QMD (embeddinggemma store), LCM (FTS5), Memory-core (SQLite+Float32Array). Fragmented.

---

## Target Architecture: Single Search, Two Data Layers

```
┌─────────────────────────────────────────────────────────┐
│                    Freddy (Agent)                        │
│                                                         │
│   Before message:  memory_recall(query) → context inject│
│   After response:  manual write (human decides)         │
│   On demand:       memory_search(query) → results       │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│              Memory-Core (Single Search Layer)           │
│                                                         │
│   • Semantic search: all-MiniLM-L6-v2 (384-dim)        │
│   • Topic classification: auto-categorize chunks        │
│   • Watcher: auto-reindex on file change                │
│   • Plugin hooks: beforeAgentStart → recall             │
│                                                         │
│   Sources:                                              │
│   ├── Markdown files (MEMORY.md, memory/*.md, etc.)     │
│   └── LCM summaries (synced, not live-captured)         │
└─────────────────────┬───────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌──────────┐ ┌──────────────────┐
│ Markdown Files│ │ LCM Store │ │ Vector Store     │
│ (Source of    │ │ (Convo    │ │ (better-sqlite3  │
│  Truth)       │ │  History) │ │  + embeddings)   │
│               │ │          │ │                  │
│ MEMORY.md     │ │ Messages │ │ chunks table     │
│ memory/*.md   │ │ Summaries│ │ file_sync table  │
│ learning.md   │ │ FTS5     │ │                  │
│ TOOLS.md      │ │          │ │                  │
│ USER.md       │ │          │ │                  │
└──────────────┘ └──────────┘ └──────────────────┘
```

### Key Decisions

1. **Memory-core replaces QMD** — Better chunking (header-aware, code-block safe), better embedding model (all-MiniLM-L6-v2 vs embeddinggemma-300m), topic classification, auto-reindexing on file change.

2. **LCM stays as-is** — It's battle-tested for conversation history (3235 messages, 49 summaries). FTS5 search is complementary, not competing. It handles compression, context management, and full conversation replay. Don't touch it.

3. **LCM summaries sync INTO memory-core** — Not live capture. Batch-sync LCM summaries into memory-core's vector store periodically (or on-demand). This lets semantic search find relevant conversation history without running two search systems.

4. **Auto-capture is DISABLED** — Human (me) decides what's important. No pattern-matching fact extraction. The capture.js module stays available for manual use but isn't wired as a plugin hook.

5. **Markdown files remain source of truth** — Memory-core is an index, not a store. If the vector DB is deleted, re-sync from markdown files. No data lives exclusively in the vector store.

6. **Single embedding model** — all-MiniLM-L6-v2 (384-dim). Drop embeddinggemma-300m entirely.

---

## Implementation Plan

### Phase 1: Memory-Core Improvements (Foundation)
**Goal:** Make memory-core production-ready as the single search layer

1. **Add LCM summary sync** (`src/lcm-sync.js`)
   - Query LCM summaries table via better-sqlite3
   - Chunk summaries the same way as markdown files
   - Embed and store with `file_path: 'lcm:summary:{summary_id}'`
   - Track sync state to avoid re-processing
   - Run on-demand: `node src/cli.js lcm-sync`

2. **Add FTS5 to memory-core** (optional, for exact-match queries)
   - Create `chunks_fts` virtual table for full-text search
   - Hybrid search: combine vector similarity + FTS5 ranking
   - Useful for exact terms (server names, error codes, dates)

3. **Improve chunker**
   - Track which conversation/summary a chunk came from
   - Add date metadata to chunks for temporal filtering
   - Handle LCM summary format (timestamps, compressed content)

4. **CLI improvements**
   - `memory-core sync` — full re-sync from markdown + LCM
   - `memory-core search "query"` — semantic search with formatted output
   - `memory-core stats` — show what's indexed
   - `memory-core watch` — daemon mode for file watching

### Phase 2: Plugin Integration (Wiring)
**Goal:** Memory-core becomes the default recall mechanism

1. **Wire beforeAgentStart hook** (recall only)
   - On incoming message → semantic search → inject top-5 results
   - Skip for short messages (<10 chars) and commands
   - Graceful failure: if memory-core errors, don't block the agent

2. **Disable QMD**
   - Set `memorySearch.enabled: false` in openclaw.json
   - Remove `memory_search` from available tools (or keep as alias to memory-core)

3. **Update AGENTS.md recall flow**
   - OLD: QMD → LCM → web → "I don't know"
   - NEW: Memory-core (semantic) → LCM (full-text/conversation) → web → "I don't know"

### Phase 3: Maintenance (Ongoing)
1. **Periodic LCM sync** — cron or manual after important conversations
2. **Markdown file watch** — watcher.js runs as daemon, auto-reindexes
3. **Chunk quality tuning** — adjust maxDistance, topK, topic classification
4. **Embedding model upgrades** — swap all-MiniLM-L6-v2 for better models as they release

---

## What Gets Retired

| Component | Action | When |
|-----------|--------|------|
| **QMD** (memory_search) | Disable in config | Phase 2 |
| **Memory-core capture.js** | Keep available, don't wire as hook | Never auto-use |
| **Memory-core plugin.js auto-capture** | Remove agentEnd hook | Phase 2 |

## What Stays

| Component | Role |
|-----------|------|
| **LCM** (lossless-claw) | Conversation history, FTS5 search, context compression |
| **Markdown files** | Human-readable source of truth |
| **Memory-core recall** | Primary semantic search |
| **Memory-core watcher** | Auto-reindex on file change |
| **Memory-core chunker** | Markdown-aware text splitting |
| **Memory-core topics** | Auto-categorization |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| all-MiniLM-L6-v2 worse than embeddinggemma-300m | Test both on same queries before retiring QMD |
| LCM sync adds too many chunks (3235 messages) | Sync summaries only, not individual messages. ~49 summaries = ~100-200 chunks |
| Memory-core SQLite gets corrupted | Re-sync from source files. No exclusive data in vector DB |
| Watcher daemon dies silently | Add health check. CLI can re-sync manually |
| Plugin hook adds latency to every message | Skip recall for short messages. Measure p99 latency |

---

## Estimated Effort

- Phase 1 (Foundation): 2-3 hours
  - LCM sync module: 1 hour
  - CLI improvements: 30 min
  - Chunker improvements: 30 min
  - Testing: 1 hour

- Phase 2 (Wiring): 1 hour
  - Plugin integration: 30 min
  - QMD disable + config: 15 min
  - AGENTS.md update: 15 min

- Phase 3 (Ongoing): Minimal
  - LCM sync: run after important convos or daily
  - Watcher: start as daemon or on-demand

**Total: ~4 hours to full unification**

---

*Plan created: 2026-03-14 03:36 UTC*
*Author: Freddy 🦊*
*Status: DRAFT — awaiting John's approval*
