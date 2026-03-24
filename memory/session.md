# Session Handoff — 2026-03-24 17:53 MDT

## What Happened
- **Max positions increase:** Changed from 3 → 8 in both `config.json` (safety.max_positions) and `bot.py` (hardcoded limit). Updated context_builder account section to show "/8 max".
- **ai-trader not trading diagnosis:** All LLM decisions were "hold None conf=0.90" because every "open" order was rejected by the bot ("max positions reached, skipping"). The LLM learned to stop trying after hours of rejections.
- **Live reflection system built:** Replaced slow file-based reflection (3-day cron) with real-time prompt injection. Three new sections added to every cycle:
  1. **Performance Window** — live stats: win rate by direction, hold time buckets, current streak (from DB queries)
  2. **Learned Patterns** — rules with confidence decay (state/patterns.json). Decays 0.02/cycle, drops below 0.3, reinforced on confirmed patterns.
  3. **Hold Regret** — shows missed opportunities: top signals when LLM chose "hold" and whether those symbols were later traded profitably.

## Files Modified
- `signals/ai-trader/config.json` — max_positions 3→8
- `signals/ai-trader/bot.py` — hardcoded limit 3→8
- `signals/ai-trader/db.py` — 4 new methods (direction_stats, hold_time_stats, streak, hold_regret_data)
- `signals/ai-trader/context_builder.py` — 6 new methods, removed old strategy_memory handoff, wired live reflection into build_prompt()
- `signals/ai-trader/ai_trader.py` — removed memory parameter from build_prompt() call
- `signals/ai-trader/state/patterns.json` — created (empty, ready for learning)

## State
- Bot: 4 positions (ICP, JTO, HYUNDAI, BRENTOIL), limit 8
- ai-trader: running, actively making open decisions again (broke hold pattern)
- Patterns: empty (will populate as trades accumulate)
- Services: all 3 running (ai-trader, lighter-bot, lighter-scanner)
- Old reflection agent: still exists as cron job but no longer used by ai-trader

## Known Issues
- "Bot reported validation failure" message is misleading — it shows when bot rejects due to "already in position", not just validation errors
- Old `read_strategy_memory()` method still exists in context_builder.py (dead code, harmless)
- Bot restart is sluggish (stuck in "deactivating" state, needs SIGKILL)

## Next
- Monitor if live reflection improves trade decisions over next few hours
- Consider adding pattern reinforcement when trade outcomes confirm a pattern (reinforce_pattern() exists but not wired to outcomes yet)
- Old reflection-agent cron job could be removed since it's no longer used
