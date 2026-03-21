# TOOLS.md - Local Notes

Environment-specific tools and scripts. Skills define _how_; this is _your_ setup.

## Web Search (fallback chain)

1. **Tavily** (primary) — `scripts/search/search-tavily.sh "query" num_results`
   - AI-optimized, 0.7s, 1000 free/month. Key in script + `TAVILY_API_KEY` env
2. **DuckDuckGo** (secondary) — `bun scripts/search/search-ddg.ts "query" num_results`
   - Free, no key, datacenter-friendly lite HTML endpoint
3. **Exa** (tertiary) — `EXA_API_KEY=... bun scripts/search/search-exa.ts "query" num_results`
   - Semantic search, 1000 free/month. Best for nuanced/research queries

**Rule:** Tavily first → DDG fallback → Exa for deep research.

## Defuddle — YouTube/Web → Markdown
- `curl defuddle.md/YOUR_URL` → clean Markdown with YAML frontmatter
- YouTube: full timestamped transcript, title, channel, publish date
- Rule: John sends a link → use Defuddle first, never say "I can't watch videos"

## Memory Pipeline
- `memory-session-extract.sh` — LLM-powered session → MEMORY.md (every 2hrs via cron)
- `memory-nightly-cleanup.sh` — dedup/reorg (daily)
- `memory-archive-dailies.sh` — weekly archive
- `memory-search.sh` — unified search across MEMORY.md + daily files
- `memory-llm.sh` — OpenRouter wrapper
