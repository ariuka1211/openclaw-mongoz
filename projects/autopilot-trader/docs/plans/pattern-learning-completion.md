# Pattern Learning — Completion Plan

## Problem Statement

The self-improving pattern system is half-built. `PatternEngine` has load/save/decay/reinforce/display all working, but **nothing ever creates or reinforces patterns**. The feedback loop is broken:

```
✅ Outcomes logged to DB (bot → shared_utils.log_outcome → decisions.db)
✅ Patterns decay every cycle
✅ Patterns injected into LLM prompt
❌ No outcome analysis → no patterns created
❌ `reinforce_pattern()` never called from production code
❌ LLM not asked to propose patterns
❌ patterns.json permanently empty: {"patterns": []}
```

## Current Data Available for Pattern Extraction

The `outcomes` table has: `symbol, direction, entry_price, exit_price, size_usd, pnl_usd, pnl_pct, roe_pct, hold_time_seconds, exit_reason`

The `decisions` table has: `action, symbol, direction, confidence, reasoning, executed, signals_snapshot, positions_snapshot`

Cross-referencing these gives us: what the AI decided, what signals were present, and what actually happened.

---

## Plan: 3 Components

### Component 1: Outcome Analyzer (`ai-decisions/context/outcome_analyzer.py`) — NEW FILE

Analyzes recent outcomes and extracts learnable patterns. Called once per cycle in `cycle_runner.py` after reading outcomes.

**Pattern extraction rules (data-driven, no LLM needed):**

| Pattern Rule | Signal | Reinforce When | Decay Signal |
|---|---|---|---|
| `longs_win_in_{session}` | outcome pnl > 0, direction=long, session=Asia/EU/US | ≥3 wins in session | losses in same session |
| `shorts_win_in_{session}` | same for shorts | ≥3 wins | losses |
| `{symbol}_long_wins` | symbol-specific long wins | ≥2 wins for symbol | losses for symbol |
| `{symbol}_short_wins` | symbol-specific short wins | ≥2 wins | losses |
| `high_conf_wins` | confidence ≥ 0.7 AND pnl > 0 | ≥3 wins at high conf | high conf losses |
| `low_conf_loses` | confidence < 0.4 AND pnl ≤ 0 | ≥3 losses at low conf | low conf wins |
| `quick_exits_lose` | hold < 10min AND pnl ≤ 0 | ≥3 quick losses | quick exits that win |
| `long_hold_wins` | hold > 60min AND pnl > 0 | ≥3 long hold wins | long hold losses |
| `funding_{dir}_favorable` | funding spread aligned with direction AND win | ≥3 aligned wins | aligned losses |

**Implementation:**
```python
class OutcomeAnalyzer:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    def analyze_and_update(self, outcomes: list[dict], history: list[dict]):
        """Analyze recent outcomes and reinforce/decay relevant patterns."""
```

Logic:
1. Get last 30 outcomes + last 30 decisions
2. For each outcome, extract features (session, symbol, direction, hold time, confidence bracket)
3. Match outcome to the decision that opened the position (by symbol + time window)
4. Count wins/losses per feature bucket
5. Call `pattern_engine.reinforce_pattern(rule, boost)` for winning buckets (≥ threshold)
6. No explicit "negative reinforcement" — decay handles that automatically (0.02/cycle)

**Thresholds (configurable):**
- `min_samples`: 3 (minimum outcomes in bucket before reinforcing)
- `win_rate_threshold`: 0.6 (60% win rate to reinforce)
- `boost`: 0.08 (slower than default 0.1 — patterns should be earned)
- `max_patterns`: 20 (cap to prevent bloat)

### Component 2: Wire Into Cycle Runner — MODIFY EXISTING

In `cycle_runner.py`, after outcomes are read (line ~84) and before prompt building (line ~103):

```python
# 2.5: Analyze outcomes → update pattern engine
self.ai_trader.outcome_analyzer.analyze_and_update(outcomes, history)
```

In `ai_trader.py`, add initialization:
```python
from context.outcome_analyzer import OutcomeAnalyzer
self.outcome_analyzer = OutcomeAnalyzer(self)
```

### Component 3: LLM Pattern Suggestions (OPTIONAL — Phase 2)

Extend the decision JSON schema to include optional `learned_rule` field:

```json
{
  "action": "...",
  "...": "...",
  "learned_rule": "optional — if you noticed a useful pattern from this data, state it as a short rule"
}
```

Parser extracts `learned_rule`, calls `reinforce_pattern(rule, boost=0.05)` if present.

**Why Phase 2:** Data-driven patterns (Component 1) are more reliable and testable. LLM suggestions are a bonus layer — the LLM sees patterns humans might miss, but should have lower boost (0.05 vs 0.08) since they're unverified.

---

## Files Changed

| File | Change | Risk |
|---|---|---|
| `ai-decisions/context/outcome_analyzer.py` | **NEW** — pattern extraction logic | None (new, isolated) |
| `ai-decisions/ai_trader.py` | Add `OutcomeAnalyzer` init, 2 lines | Low |
| `ai-decisions/cycle_runner.py` | Add `analyze_and_update()` call, 1 line | Low |
| `ai-decisions/tests/test_outcome_analyzer.py` | **NEW** — unit tests | None (new) |
| `ai-decisions/prompts/decision.txt` | (Phase 2) Add `learned_rule` field | Low |

**Not touched:** pattern_engine.py, prompt_builder.py, db.py, safety.py, parser.py — existing code stays as-is.

## What This Does NOT Do

- Does not change how decisions are made (LLM still sees same prompt structure)
- Does not change safety rules
- Does not add LLM calls or cost
- Does not modify existing IPC protocol
- Pattern section was already in prompt_builder — just stays empty until patterns accumulate

## Verification

1. Run existing tests: `cd ai-decisions && python -m pytest tests/` — must all pass
2. Run new tests: `python -m pytest tests/test_outcome_analyzer.py`
3. Check service starts: `systemctl restart ai-decisions && journalctl -u ai-decisions -f`
4. After 10+ outcomes accumulate, check `patterns.json` — should have entries
5. Check logs for `patterns=N` (should be > 0 once patterns exist)

## Risks

- **Overfitting on small samples:** Mitigated by min_samples=3 threshold and low boost (0.08)
- **Pattern bloat:** Mitigated by max_patterns=20 cap and natural decay (0.02/cycle)
- **Stale patterns:** Decay at 0.02/cycle means a pattern at 0.5 confidence drops to 0.3 (removed) in ~10 cycles without reinforcement — roughly 20-30 minutes
