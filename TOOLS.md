# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Working Tools

### Web Search — 3 Options (fallback chain)

1. **Tavily** (primary) — `scripts/search-tavily.sh "query" num_results`
   - AI-optimized, fastest (0.7s), 1000 free/month
   - Tavily key in script + env `TAVILY_API_KEY`

2. **DuckDuckGo** (secondary) — `bun scripts/search-ddg.ts "query" num_results`
   - Free, no API key, datacenter-friendly (lite HTML endpoint)
   - Good for general queries, news, docs

3. **Exa** (tertiary) — `EXA_API_KEY=... bun scripts/search-exa.ts "query" num_results`
   - Semantic/AI search, 1000 free requests/month
   - Best for nuanced/complex queries, code search, research
   - Get key at https://exa.ai, set `EXA_API_KEY` in env

**Usage rule:** Tavily first. If it fails or returns poor results, try DDG. Use Exa for deep/research queries.

### Defuddle — YouTube/Web → Markdown
- **API:** `curl defuddle.md/YOUR_URL`
- Returns clean Markdown with YAML frontmatter (title, author, date, description)
- **YouTube:** Extracts full timestamped transcript, title, channel, publish date
- **Rule:** When John sends a YouTube link → use Defuddle first, never say "I can't watch videos"
- Works on any URL, not just YouTube

### Tavily Search
- **API:** `scripts/search-tavily.sh "query" num_results`
- Tavily key stored in script and env `TAVILY_API_KEY`
- Fast (0.7s), structured results with relevance scores
- Best for: finding info across the web (replaces DDG in deep-research-pro)

### LCM (Lossless Claw) Tools
- `lcm_grep` — search conversation history with full-text search
- `lcm_describe` — get summary of topics/threads
- `lcm_expand_query` — deep recall, spawns sub-agent to expand DAG, returns answer with cited summary IDs

### Memory System v2 (Supermemory-inspired, 9 scripts + 3 prompts)
- `mem-version.py` — relational versioning (facts supersede old versions) + TTL decay
- `mem-append.py` — appends new items with structured metadata (date, status, source)
- `mem-cleanup.py` — LLM-powered dedup + reorg of MEMORY.md sections
- `mem-promote.py` — promotes recurring themes from LCM summaries
- `mem-profile.sh` — generates 200-word user profile → `memory/profile.md`
- `memory-search.sh` — unified search across MEMORY.md, LCM, daily files, LEARNINGS.md
- `memory-daily.sh` — 10-step pipeline (pull LCM → distill → extract → version → TTL → append → cleanup → profile → promote → report)
- `memory-llm.sh` — OpenRouter API wrapper for LLM calls
- **Config:** `memory/ttl-config.json` (decay settings), `memory/containers.json` (container tags)
- **Prompts:** `prompts/extract.txt`, `prompts/distill.txt`, `prompts/cleanup.txt`

### LCM Fixes
- Patched `lossless-claw/src/summarize.ts` — "Expand for details" footer only included when details actually dropped (prevents gemini-2.5-flash hallucination)

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
