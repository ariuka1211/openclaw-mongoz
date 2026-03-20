# MEMORY.md — Freddy 🦊

**John** — First session 2026-03-12. Wants lean systems, hates wasted tokens. Mountain time (MDT, UTC-7). Trading-focused.

---

## Active Projects [active]
- Auto/copilot crypto perps trading
- Job search/applying automation
- Resume/portfolio projects
- Phase: gathering tools, skills, building robust infra for future projects

## Setup [fact]
- **Server:** srv1435474 (8GB RAM, Linux x64)
- **Bot:** @trader_hype_bot on Telegram
- **Model:** hunter-alpha via OpenRouter
- **Gateway:** OpenClaw v2026.3.12, port 18789
- **Skills:** deep-research-pro, baseline-kit, skill-monitor, xint
- **LCM:** active, summary model google/gemini-2.5-flash, threshold 0.75, max depth 2
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
- Recall priority: QMD → LCM → web search → then "I don't know"
- "Trim memory" = full wrap-up + dedup + learnings
- 🔴 **NEVER edit `openclaw.json`** — not with jq, not with any tool, not even a tiny field. Tell John what to change, let him do it. He has said this 1000+ times.

## Decisions [decision]
- [2026-03-16] Changed LCM summary model to gemini-2.5-flash
- [2026-03-16] Capped LCM summary depth at 2 (D1/D2 sufficient for single-user)
- [2026-03-16] Lowered LCM contextThreshold to 0.75, freshTailCount to 20
- [2026-03-15] All cron jobs on system crontab (not OpenClaw cron) — gateway independence
- [2026-03-16] Reset session when file > 500KB to prevent queue blocking

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

## Patterns [pattern]
- Prefers testing before declaring done
- Asks about cost before implementation
- Prefers system-level automation over plugin-dependent solutions
- Values understanding how things work, not just that they work

## Pocket Ideas [active]
- See `memory/pocket-ideas.md`

## Archive [archived]
- Dual-instance plan (Mar 14) — trading gets own OpenClaw instance, set up when ready
- Honcho research (Mar 16) — "memory that reasons" platform, concluded LCM + structured MEMORY.md sufficient
