# MEMORY.md — Freddy 🦊

**John** — First session 2026-03-12. Wants lean systems, hates wasted tokens. Mountain time (MDT, UTC-7). Trading-focused.

---

## Active Projects [active]
- Auto/copilot crypto perps trading
- Job search/applying automation
- Resume/portfolio projects
- Phase: gathering tools, skills, building robust infra for future projects

## Setup [fact]
  - [2026-03-17] `memory-llm.sh` provides a reusable wrapper script for making LLM API calls to OpenRouter.
  - [2026-03-17] `memory-daily.sh` now pulls LCM summaries, sends `MEMORY.md` content and summaries to `gemini-2.5-flash` via `memory-llm.sh` for extraction, deduplication, and tagging, appends results to `MEMORY.md` in correct sections, auto-trims sections over 25 items (keeping newest), and archives daily notes older than 7 days.
- **Server:** srv1435474 (8GB RAM, Linux x64)
- **Bot:** @trader_hype_bot on Telegram
- **Model:** hunter-alpha via OpenRouter
- **Gateway:** OpenClaw v2026.3.12, port 18789
- **Skills:** deep-research-pro, baseline-kit, skill-monitor, xint
- **LCM:** active, summary model google/gemini-2.5-flash, threshold 0.15, max depth 2
- **Search:** Tavily (primary), DuckDuckGo (fallback), Exa (deep research)

## Preferences [pref]
- Lean systems, no wasted tokens, anti-bloat
- Bash scripts over plugins where possible
- Always ask before deploying or creating auto-scripts
- `openrouter/` prefix on all fallback models
- `trash` > `rm` — never delete when unsure
- No gateway restarts without explicit permission

## Key Rules [rule]
- Check LEARNINGS.md before deploy/config/debug/spawn/reasoning — mandatory pre-action protocol
- Integration Rule: check conflicts, add value not complexity, compose don't replace, test before done
- No auto-scripts/systemd services without explicit approval
- Recall priority: memory-search.sh → memory_search tool → lcm_grep → web search → "I don't know"
- "Trim memory" = full wrap-up + dedup + learnings
- 🔴 **NEVER edit `openclaw.json`** — not with jq, not with any tool, not even a tiny field. Tell John what to change, let him do it. He has said this 1000+ times.

## Decisions [decision]
- [2026-03-17] When a user provides an OpenRouter API key, the system will store it in `.env`, test `memory-llm.sh`, run `memory-daily.sh` end-to-end, and update the crontab.
- [2026-03-17] The memory system will be redesigned to leverage LLMs for content analysis while retaining bash for file operations, using a new architecture of `memory-daily.sh`, `memory-llm.sh`, and `memory-search.sh`.
- ⭐ [2026-03-17] Fallback model changed to `openrouter/google/gemini-3.1-pro` and gateway restarted to activate it.
- [2026-03-17] Watchdog daemon updated to tail correct log, detect 400s/embedded run errors, identify subagent loop, and perform proper process cleanup.
- [2026-03-17] `contextThreshold` will be lowered to 0.15 (150k tokens) for more frequent summarization (deferred by user).
- [2026-03-16] LCM summary model changed to `gemini-2.5-flash`, capped at depth 2, and `contextThreshold` lowered to 0.75 along with `freshTailCount` to 20.
- [2026-03-16] Session will reset when file size exceeds 500KB to prevent queue blocking.
## Lessons [lesson]
- vxtwitter API for reading X/Twitter links (`api.vxtwitter.com`)
- Tavily finds URLs, web_fetch reads pages — complementary, not competitors
- OpenClaw cron (sys-*) unreliable — use system crontab for all automation
- Plugin registry can deadlock gateway — shell scripts safer for external integrations
- Queue-watchdog cooldown not working — gateway transient restarts cause alert spam
- **Session bloat blocks queue** (Mar 16) — 5MB session → 10min embedded run timeouts → lane blocked. `/reset` fixes. Gateway restart does NOT clear sessions.

## Open Items [open]
- Brave API key missing
- Memory-core plugin integration into main pipeline
- Model strategy: healer-alpha free with vision
- Firehose + topic-monitor (real-time web alerts)
- TinyFish + n8n (AI web scraping)
- Gotify (push alerts)
- Telegram polling reliability
- **Session auto-rotation** — auto `/reset` when session > 500KB. Check if OpenClaw has config for this.
- **Deferred:** Remove `alertThresholdMB` and `maxSizeMB` keys from `openclaw.json` session.maintenance
- Set up GitHub Copilot as model provider (`openclaw models auth login-github-copilot`)

## Patterns [pattern]
- Prefers testing before declaring done
- ⭐ Asks about cost before implementation
- Prefers system-level automation over plugin-dependent solutions
- Values understanding how things work, not just that they work

## Pocket Ideas [active]
- See `memory/pocket-ideas.md`

## Archive [archived]
- Dual-instance plan (Mar 14) — trading gets own OpenClaw instance, set up when ready
- Honcho research (Mar 16) — "memory that reasons" platform, concluded LCM + structured MEMORY.md sufficient
