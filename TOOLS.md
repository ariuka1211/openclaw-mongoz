# TOOLS.md - Local Notes

Environment-specific tools and scripts. Skills define _how_; this is _your_ setup.

## Web Search (fallback chain)

1. **Tavily** (primary) — `scripts/search-tavily.sh "query" num_results`
   - AI-optimized, 0.7s, 1000 free/month. Key in script + `TAVILY_API_KEY` env
2. **DuckDuckGo** (secondary) — `bun scripts/search-ddg.ts "query" num_results`
   - Free, no key, datacenter-friendly lite HTML endpoint
3. **Exa** (tertiary) — `EXA_API_KEY=... bun scripts/search-exa.ts "query" num_results`
   - Semantic search, 1000 free/month. Best for nuanced/research queries

**Rule:** Tavily first → DDG fallback → Exa for deep research.

## Defuddle — YouTube/Web → Markdown
- `curl defuddle.md/YOUR_URL` → clean Markdown with YAML frontmatter
- YouTube: full timestamped transcript, title, channel, publish date
- Rule: John sends a link → use Defuddle first, never say "I can't watch videos"

## Memory System v2 (Supermemory-inspired)
- **Scripts:** `mem-version.py` (relational versioning + TTL), `mem-append.py` (structured append), `mem-cleanup.py` (LLM dedup/reorg), `mem-profile.sh` (200-word profile → `memory/profile.md`), `memory-search.sh` (unified search), `memory-session-extract.sh` (9-step pipeline), `memory-llm.sh` (OpenRouter wrapper)
- **Config:** `memory/ttl-config.json` (decay), `memory/containers.json` (tags)
- **Prompts:** `prompts/extract.txt`, `prompts/distill.txt`, `prompts/cleanup.txt`
