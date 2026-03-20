# MEMORY.md — Freddy 🦊

**John** — First session 2026-03-12. Wants lean systems, hates wasted tokens. Mountain time (MDT, UTC-7). Trading-focused.

---

## Active Projects [active]
- Auto/copilot crypto perps trading project
- Job search/applying automation project
- Resume/portfolio projects
## Setup [fact]
- Large inline prompts in `memory-daily.sh` and `mem-cleanup.py` were externalized into separate files in `/root/.openclaw/workspace/prompts` to reduce script size and token usage.
  - `agents.defaults.compaction.customInstructions` is a valid and implemented OpenClaw configuration field (added in 2026.3.13 via PR #10456).
  - LCM's "lossless" design treats the session's `.jsonl` file as an immutable, append-only event log, causing continuous growth as it does not rewrite or delete raw messages from it.
  - The `watchdog-daemon.sh` (unified daemon) is responsible for `📝 LCM Summary ✅` alerts and has been restarted and configured as a systemd service (`openclaw-watchdog.service`) with `Restart=always` for persistence.
  - The memory system uses `MEMORY.md` as its 'brain' and `lcm.db` for raw history, with `google/gemini-2.5-flash` integrated for memory analysis and summarization via `memory-daily.sh`.
- **Server:** srv1435474 (8GB RAM, Linux x64)
  - **Bot:** @trader_hype_bot on Telegram
  - **Model:** hunter-alpha via OpenRouter
  - **Gateway:** OpenClaw v2026.3.12, port 18789
  - **Skills:** deep-research-pro, baseline-kit, skill-monitor, xint
  - **LCM:** active, summary model google/gemini-2.5-flash, threshold 0.1 (pending restart), max depth 2
## Preferences [pref]
- Lean systems, anti-bloat, no wasted tokens.
- Bash scripts over plugins where possible.
- `openrouter/` prefix on all fallback models.
## Key Rules [rule]
Check `LEARNINGS.md` before deploy/config/debug/spawn/reasoning.
Integration Rule: check conflicts, add value, compose don't replace, test before done.
No auto-scripts/systemd services without explicit approval.
Recall priority: `memory-search.sh` → `memory_search` tool → `lcm_grep` → web search → "I don't know".
**NEVER** edit `openclaw.json` directly. Communicate changes to John.
`trash` > `rm`. Never delete when unsure.
No gateway restarts without explicit permission.
## Decisions [decision]
- [2026-03-17] A hybrid memory system architecture was defined, using `memory-llm.sh` as a reusable OpenRouter API wrapper for semantic deduplication, quality filtering, and section assignment, while `memory-daily.sh` orchestrates the process and file operations.
  - [2026-03-17] Memory promotion logic was tuned, requiring 4+ summaries and 70% word overlap for items to be promoted.
  - ⭐ [2026-03-17] All memory-related cron jobs are now system cron jobs.
  - ⭐ [2026-03-17] Implemented retry logic with backoff for LLM API calls.
  - [2026-03-17] Added a new LLM-powered distillation step for LCM summaries to remove noise before extraction.
  - [2026-03-17] Performed a self-audit against 7 layers (Memory, Tool Access, Error Recovery, Observability, Session Management, Multi-Agent Coordination, External Integrations) for OpenClaw.
  - ⭐ [2026-03-17] When a user provides an OpenRouter API key, the system will store it in `.env`, test `memory-llm.sh`, run `memory-daily.sh` end-to-end, and update the crontab.
  - ⭐ [2026-03-17] Fallback model changed to `openrouter/google/gemini-3.1-pro` and gateway restarted to activate it.
- [2026-03-16] LCM summary model changed to `gemini-2.5-flash`, capped at depth 2, and `contextThreshold` lowered to 0.75 along with `freshTailCount` to 20.

## Key Findings [fact]
- `contextPruning` was implemented in `openclaw.json` with a 1-hour TTL and 4000-character `softTrim` to reduce the effective context sent to the model, leading to less frequent LCM triggers.
- The `contextThreshold` for LCM triggering was adjusted from 0.75 to 0.1, calculated as `contextThreshold × model_context_window` (post-pruning).
- A new observability layer was built, including `obs-log.sh` for structured logging and `session-watchdog.sh` for hourly session size monitoring with alerts.
- A self-audit across 7 system layers (memory, tools, error recovery, observability, session mgmt, multi-agent, integrations) resulted in a score of 4.3/10, with observability, error recovery, and session monitoring identified as highest-impact areas for improvement.
## Lessons [lesson]
- OpenClaw's `contextPruning` prunes old tool results (`exec` output), not conversation messages.
- The default LCM summarizer prompt is generic, lacking explicit exclusions for tool calls or thinking content, and employs purely ordinal compaction (oldest-first eviction).
- OpenClaw's `/status` command and UI metrics for compaction (e.g., "0 fresh tokens") are derived from the *legacy* compaction engine, giving a false impression that LCM is not working.
- Aggressive `contextPruning` (trimming the active buffer to ~30k tokens) interferes with LCM by preventing it from reaching its `contextThreshold` (~105k tokens), thereby reducing aggressive compaction.
- Orphaned data in `lcm.db` was primarily caused by LCM's append-only design, lacking retention policies and `ON DELETE RESTRICT` SQL constraints that prevented cascade deletions.
- Unbounded growth of session `.jsonl` files is intentional with LCM, as they serve as an immutable, lossless event bus, unlike the legacy engine's truncation behavior.
- Large sessions (`.jsonl` files) can lead to long embedded run timeouts and block processing lanes, requiring `/reset` to clear, as a gateway restart does not.
- Shell scripts are safer for external integrations than directly invoking the gateway via the plugin registry; consider using system crontab for automation.
## Open Items [open]
- Finalize model strategy: utilizing healer-alpha free with vision capabilities.
- Implement Firehose for real-time web alerts with a topic monitor.
- Integrate TinyFish with n8n for AI web scraping.
- Set up Gotify for push alerts.
- Improve Telegram polling reliability.
- Implement automatic `/reset` for sessions exceeding 500KB. Investigate OpenClaw configuration options for this.
- Investigate increasing `freshTailCount` (e.g., from 20 to 30) for LCM to protect more recent raw context.
## Patterns [pattern]
- Prefers auditing against OpenClaw's actual goals rather than generic 'Jarvis-like' frameworks.
- Prefers testing before declaring done
- ⭐ Asks about cost before implementation
- Prefers system-level automation over plugin-dependent solutions
- Values understanding how things work, not just that they work
- "Trim memory" = full wrap-up + dedup + learnings.
## Pocket Ideas [active]
- See `memory/pocket-ideas.md`
- vxtwitter API for reading X/Twitter links (`api.vxtwitter.com`)
## Archive [archived]
- **Dual-instance plan** (Mar 14) — trading gets own OpenClaw instance, set up when ready
