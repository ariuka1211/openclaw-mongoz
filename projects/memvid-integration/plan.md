# Memvid Memory Integration — Plan

## Goal
Replace workspace memory storage with `.mv2` (memvid) files while keeping OpenClaw's built-in `memory_search`/`memory_get` tools fully functional.

## Key Constraint
- **NEVER modify** `/usr/lib/node_modules/openclaw/` or OpenClaw core
- Keep `session.md` write behavior unchanged (it's auto-loaded at session start)
- Memvid is a **parallel, improved storage layer** — not a replacement for the OpenClaw tools

## Architecture
```
memory/                    # KEEP: session.md (handoff), daily .md files
workspace-memory.mv2       # NEW: semantic search across all historical memory
memvid-tools/              # NEW: Python wrapper for read/write/search
```

## Checklist

### Phase 1: Install & Verify memvid-sdk
- [x] 1.1 `pip install memvid-sdk` (use `--break-system-packages` or venv)
- [x] 1.2 Verify import works (`python3 -c "import memvid_sdk"`)
- [x] 1.3 Test create/read/search locally (sandbox .mv2 file)

### Phase 2: Build Wrapper Module
- [x] 2.1 Create `memvid-tools/search.py` — semantic search over .mv2
- [x] 2.2 Create `memvid-tools/ingest.py` — append sessions/memory to .mv2
- [x] 2.3 Support existing embedding providers (prefer local BGE_SMALL to avoid API key deps)
- [x] 2.4 Test: store 3 records, search, verify results

### Phase 3: Migrate Existing Memory
- [x] 3.1 Script to read all `memory/*.md` files (except session.md)
- [x] 3.2 Convert each to memvid records with proper titles/labels/metadata
- [x] 3.3 Write all into `workspace-memory.mv2`
- [x] 3.4 Verify record count matches file count (111 == 111)
- [x] 3.5 Test semantic search: all 5 test queries returned correct results

### Phase 4: Integration
- [x] 4.1 Add `memvid_search()` call to session startup (after memory search)
- [x] 4.2 Add `memvid_ingest_session()` to session wrap-up
- [x] 4.3 Verify: no regression in normal memory_search (memory_get works normally)
- [x] 4.4 Verify: new semantic search returns richer results than grep (ranked+scored vs raw matches)

## Safety Rules
- Do NOT delete any existing memory/*.md files
- Do NOT modify session.md behavior
- Do NOT install system-wide if it breaks anything (use venv or --break-system-packages)
- Test with a copy first
