# Session Handoff — 2026-03-24 18:15 MDT

## What Happened
- **LLM waste when positions full:** Investigated whether ai-trader wastes API calls when max positions is reached. Confirmed yes — LLM still called, outputs "open", safety rejects.
- **First attempt (reverted):** Added early-exit guard in ai_trader.py to skip LLM entirely when at max positions. Reverted because it also prevents the LLM from closing bad positions.
- **Prompt-based fix:** Instead, the prompt now explicitly warns the LLM when full: `⚠️ ALL SLOTS FULL (8/8) — DO NOT open new positions. Only consider closing existing ones.` Added to context_builder.py account section.
- **System prompt stale:** system.txt said "Max 3 concurrent positions" — fixed to use `{max_positions}` template, injected from config at runtime.
- **Bug fix:** Initial context_builder change referenced `self.safety` which doesn't exist on ContextBuilder. Fixed to use `self.config.get("safety", {}).get("max_positions", 8)`.
- **Restart hang fix:** Root-caused the ai-trader restart getting stuck. `asyncio.sleep(sleep_time)` with up to 120s sleep — SIGTERM sets flag but sleep completes. Fixed by sleeping in 2-second chunks with running flag check between each.

## Files Modified
- `signals/ai-trader/ai_trader.py` — system_prompt template injection (line 110), interruptible sleep chunks (line ~216)
- `signals/ai-trader/context_builder.py` — slots_status warning in account section (line ~530)
- `signals/ai-trader/prompts/system.txt` — `{max_positions}` template placeholder

## State
- Bot: 3 positions (ICP, JTO, BRENTOIL), limit 8
- ai-trader: running, making decisions normally
- Services: all 3 running
- Restart now takes ~2s instead of up to 120s

## Pending
- None of these changes are committed yet
- Monitor if LLM respects "DO NOT open" warning when at max positions
- Consider: if LLM still tries to open when full, add dedicated `## ⚠️ CAPACITY ALERT` section at top of prompt

## Next
- Commit changes (branch + PR per protocol)
- Monitor token savings from prompt warning vs early-exit approach
