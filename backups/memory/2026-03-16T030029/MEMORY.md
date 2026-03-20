# MEMORY.md — Freddy 🦊

**John** — First session 2026-03-12. Wants lean systems, hates wasted tokens. Mountain time (MDT, UTC-7). Trading-focused.

**Strategic Goals (Mar 14):**
1. Auto/copilot crypto perps trading
2. Job search/applying automation
3. Resume/portfolio projects
- Phase: gathering tools, skills, building robust infra for future projects

**Setup:** hunter-alpha via OpenRouter. LCM plugin active. Telegram (@trader_hype_bot). Server: srv1435474. **MEM0 integrated** — semantic memory layer with auto-search/store protocols (hosted API, scripts at skills/mem0/scripts/).

**System:** Built-in memory-core search ✅. LCM ✅. Mission Control dashboard ✅. OpenClaw v2026.3.12. Tavily search API ✅. Watchdog cron ✅. LCM model switched to free OpenRouter ✅. **MEM0 semantic memory ✅** (hosted API, v2 search). 5 skills: deep-research-pro, baseline-kit, systematic-debugging, review-driven-dev, skill-monitor.

**Key Rules:**
- No auto-scripts/systemd services without explicit approval
- Always `openrouter/` prefix on fallback models
- Integration Rule: check conflicts, add value not complexity, compose don't replace, keep it lean, test before done
- "Trim memory" = full wrap-up + dedup + learnings
- Recall: QMD → LCM → web search → then "I don't know"
- Mistakes: Check MISTAKES.md before deploy/config/debug/spawn/reasoning — mandatory pre-action protocol

**Tomorrow's Queue:** Firehose + topic-monitor (real-time web alerts), stock-analysis skill eval, Paperclip AI research (multi-agent org framework), TinyFish + n8n (AI web scraping), Gotify (push alerts), Telegram polling reliability.

**Open:** Brave API key missing, memory-core plugin integration into main pipeline, model strategy (healer-alpha free with vision).

**Lessons:**
- vxtwitter API for reading X/Twitter links (`api.vxtwitter.com`)
- Tavily finds URLs, web_fetch reads pages — complementary, not competitors

**Dual-Instance Plan (saved):** memory/2026-03-14-architecture-plan.md — Trading gets its own OpenClaw instance (~/.openclaw-trading/, port 18790, new bot). Same tools, separate memory. Set up when ready.

**Skill Monitor (built Mar 15):** SQLite-based skill health tracking. DB: `data/skill-monitor/skill-runs.db`.

**Pocket Ideas:** See `memory/pocket-ideas.md`
