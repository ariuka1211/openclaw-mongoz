# HANDOFF NOTES FOR LEVERAGE UNIFICATION

## Plan Status: ✅ READY FOR IMPLEMENTATION

**Location:** `/root/.openclaw/workspace/docs/plans/leverage-unification-plan.md`

---

## Critical Additions Found During Final Review

### 🔥 PHASE 4C: Update LLM Prompts (CRITICAL - MISSING FROM ORIGINAL PLAN)

**Files Found:**
- `ai-decisions/prompts/system.txt` - contains leverage limits and guidance
- `ai-decisions/prompts/decision.txt` - JSON schema includes `"leverage": 1.0-20.0`

**Required Changes:**

**File:** `ai-decisions/prompts/system.txt`
```txt
# REMOVE these lines:
- Max 20x leverage
- Higher leverage = tighter stops needed

LEVERAGE:
- Start low (2-5x) unless high conviction
- Higher leverage = tighter stops needed
- Liquidation distance must be >= 2x stop loss distance
```

**File:** `ai-decisions/prompts/decision.txt`
```json
# REMOVE the leverage field entirely:
{
  "action": "open" | "close" | "hold",
  "symbol": "TOKEN" | null,
  "direction": "long" | "short" | null,
  "size_pct_equity": 0.0-10.0,
  // "leverage": 1.0-20.0,  ← REMOVE THIS LINE
  "stop_loss_pct": 0.5-15.0,
  "reasoning": "...",
  "confidence": 0.0-1.0
}
```

**Impact:** Without updating the prompts, the LLM will continue trying to output `leverage` in decisions, causing schema validation errors in `safety.py` after we remove leverage from the required fields.

---

## Implementation Priority Order

1. **PHASE 4C: Update prompts FIRST** (prevents immediate failures)
2. **PHASE 1: Exchange enforcement** (core fix)  
3. **PHASE 4A-B: Remove leverage from AI safety/protocol**
4. **PHASE 2: Unify DSLState** (high risk, test carefully)
5. **PHASES 3,5,6: Cleanup**

---

## Pre-Implementation Checklist

- [ ] Read the full plan at `docs/plans/leverage-unification-plan.md`
- [ ] Check if new positions have been opened since state analysis (may need fresh state backup)
- [ ] Verify all 10 edge cases are understood (EC-1 through EC-10)
- [ ] Test environment ready (can restart services without affecting production)
- [ ] Backup current `bot_state.json` before starting

---

## Post-Implementation Verification

1. **Test `set_leverage()` works:**
   ```bash
   # Check logs for "✅ Leverage set: 10x for market X"
   # Verify no "❌ Aborting open: could not set leverage" errors
   ```

2. **State migration succeeded:**
   ```bash
   # Check state file has single leverage values (not 0.096)
   python3 -c "import json; d=json.load(open('projects/autopilot-trader/bot/core/state/bot_state.json')); [print(f'{v[\"symbol\"]}: {v[\"dsl\"][\"leverage\"]}') for v in d['positions'].values()]"
   ```

3. **Dashboard shows correct position sizes:**
   ```bash
   # Visit dashboard, verify positions show real size (not 1/10th)
   ```

4. **AI decisions no longer include leverage:**
   ```bash
   # Check ai-decision.json has no "leverage" field
   tail -f ai-decisions/ipc/ai-decision.json
   ```

---

## Risk Mitigation

- **Phase 2 is highest risk** (DSL changes). Test thoroughly on a copy first.
- **State corruption protection**: The plan includes backward-compatibility for old state files with `effective_leverage: 0.096`.
- **Exchange failures**: `set_leverage()` failure → aborts open (safe failure mode).
- **Rollback**: Comment out `set_leverage()` call to revert to old behavior if needed.

---

## Files Actually Needing Changes (Final Count)

| Phase | Files | Changes |
|-------|-------|---------|
| 1 | 2 | `lighter_api.py` + tests |
| 2 | 6 | `dsl.py`, `position_tracker.py`, `execution_engine.py`, `state_manager.py` + tests |
| 3 | 3 | `shared_utils.py`, `prompt_builder.py`, `result_writer.py` |
| 4 | 6 | `safety.py`, `bot_protocol.py`, `executor.py`, `signal_handler.py`, `system.txt`, `decision.txt` |
| 5 | 2 | `position-sizing.ts`, `output.ts` |
| 6 | 5 | `config.yml`, `config.json`, `cheatsheet.md`, `autopilot-trader.md`, `portfolio.py` |

**Total: 24 files** (plus extensive test updates)

---

## The Current Leverage Bug (Reminder)

```bash
# Live position on exchange
BTC/USDT: 50x leverage, $X position

# Our DSL tracking
leverage: 0.096x (notional/equity calculation)

# Dashboard display  
size_usd: position * 0.096 = 10% of real size
```

This plan fixes ALL of these issues by making the exchange the single source of truth.

---

**READY FOR IMPLEMENTATION** ✅

Next session should start with Phase 4C (update prompts), then Phase 1 (exchange enforcement).