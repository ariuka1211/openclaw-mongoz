# Audit: Stop Loss Execution, Close Retry Logic, and Outcome Logging

**Date:** 2026-03-27  
**Auditor:** Subagent  
**Scope:** execution_engine.py, executor.py, shared_utils.py, verifier.py, models.py, position_tracker.py

---

## Q1: Are `shared_utils.log_outcome` and `signal_processor._log_outcome` the same function?

**Yes — they are the same function, no double-logging risk.**

`signal_processor._log_outcome` (line 87-88) is a thin wrapper:

```python
def _log_outcome(self, pos, exit_price, exit_reason, estimated=False):
    from core.shared_utils import log_outcome
    log_outcome(pos, exit_price, exit_reason, self.cfg, self.tracker, estimated=estimated)
```

`executor.py` imports `log_outcome` directly from `shared_utils` and calls it.  
`execution_engine.py` calls `self.bot.signal_processor._log_outcome(...)` which delegates to the same `shared_utils.log_outcome`.

**All close paths funnel through the single `shared_utils.log_outcome` function.** No double-logging possible — each close action triggers exactly one call.

---

## Q2: Is the CRITICAL-4 pattern ("log outcome ONCE after verification") consistently followed?

**Yes — all exit paths follow the pattern.** Detailed audit of each path:

### tier_lock / stagnation / hard_sl (execution_engine.py ~L420-520)
1. SL order submitted → `execute_sl()`
2. If `sl_success`: verify via `_verify_position_closed()`
   - If verification **fails** and max attempts reached → `_log_outcome(pos, price, f"dsl_{action}", estimated=True)` then `return` (keeps position in tracker)
   - If verification **passes**: get fill price → `_log_outcome(pos, exit_price, f"dsl_{action}")` → remove from tracker ✓
3. If `sl_success` is False: set retry delay → `return` (no outcome logged, will retry) ✓

### trailing_sl (execution_engine.py ~L540-610)
- Mirror structure of the above. After successful close + verification:
  - Verification fails after max retries → `_log_outcome(pos, price, "trailing_sl", estimated=True)` ✓
  - Verification passes → `_log_outcome(pos, exit_price, "trailing_sl")` ✓

### exchange_close (execution_engine.py `_tick()` detection loop)
- Position absent from exchange → `_log_outcome(pos, exit_price, "exchange_close")` ✓

### AI close (executor.py `execute_ai_close`)
- Same pattern: verify → log once on success, log with `estimated=True` on max failure ✓

### AI close_all (executor.py `execute_ai_close_all`)
- Per-position: verify → `log_outcome(pos, exit_price, "ai_close_all")` on success ✓

**Verdict: CONSISTENT.** No path double-logs. No path skips logging.

---

## Q3: Close retry delays — `_sl_retry_delays` values and reasonableness

**Defined in bot.py L127:**
```python
self._sl_retry_delays: list[int] = [15, 60, 300, 900]  # 15s, 1min, 5min, 15min
```

**Usage:** The delay index is `min(attempts - 1, len(delays) - 1)`, so:
- Attempt 1 → 15s cooldown
- Attempt 2 → 60s cooldown
- Attempt 3 → 300s cooldown (5 min)
- Attempt 4+ → 900s cooldown (15 min, caps at last value)

After 4 attempts, the bot alerts for manual intervention.

**Assessment: REASONABLE.**
- The exponential-ish progression (15s → 1m → 5m → 15m) balances urgency with not hammering the API
- The 15s first retry handles transient rate limits
- The 15m cap for late retries prevents infinite rapid-fire but still allows eventual recovery if the exchange recovers
- Max 4 attempts before alerting keeps the window manageable (~16 minutes worst case)

**One note:** The `bot._close_attempts` (used by `executor.py`) and `bot._dsl_close_attempts` (used by `execution_engine.py`) are **separate counters**. This is correct because AI close and DSL close are independent code paths — they shouldn't share retry state. The cooldown dicts (`_close_attempt_cooldown` vs `_dsl_close_attempt_cooldown`) are also separate. ✓

---

## Q4: Does trailing SL call `_log_outcome` after successful close + verification?

**Yes.** Full call chain in execution_engine.py ~L580-610:

```python
# After verification passes:
self.bot._dsl_close_attempts.pop(pos.symbol, None)
self.bot._dsl_close_attempt_cooldown.pop(pos.symbol, None)
fill_price = await self.bot.signal_processor._get_fill_price(mid, sl_coi)
exit_price = fill_price if fill_price else price
self.bot.signal_processor._log_outcome(pos, exit_price, "trailing_sl")  # ← YES
self.bot._recently_closed[mid] = time.monotonic() + 300
```

And for the failure-then-give-up path:
```python
if attempts >= self.bot._max_close_attempts:
    self.bot.signal_processor._log_outcome(pos, price, "trailing_sl", estimated=True)  # ← YES
```

**Both success and max-retry-failure paths log the outcome.** ✓

---

## Q5: ROE calculation discrepancy in `execute_ai_close`

**YES — there IS a discrepancy in the Telegram alert message.**

In `executor.py` after successful close (~L228-234):
```python
roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
    else ((pos.entry_price - exit_price) / pos.entry_price * 100)
# This is raw price movement %, NOT leveraged ROE
```

This `roe` is sent in the Telegram alert. It represents raw price change %, not ROE on margin.

Meanwhile, `shared_utils.log_outcome` correctly computes:
```python
roe_pct = pnl_pct * leverage  # Return on Equity, leverage-adjusted
```

**Impact:**
- **Database (DB):** Correct. ROE is leverage-adjusted. ✓
- **Telegram close message:** Shows raw price % as "ROE" — this is technically wrong labeling. For a 10x leveraged position that moved 2%, the alert says "ROE: +2.0%" when the actual ROE is +20%.

**In `execution_engine.py`,** the post-close alerts use `pnl = self._pnl_info(pos, price)` which **does** compute `roe_pct = pnl_pct * leverage`. So DSL close alerts show correct ROE.

**Scope of bug:** Only `executor.py` (`execute_ai_close`) shows incorrect ROE in its Telegram alert. The DSL exit alerts in `execution_engine.py` and the DB logging are both correct.

**Severity:** Low-Medium. Telegram display only. Database is correct. Users might be misled about actual margin return by ~10x (typical leverage).

---

## Q6: `_verify_position_closed` polling strategy and false negative risk

**Polling strategy (verifier.py):**
```python
delays = [3, 5, 7, 10]  # Total: 25 seconds
```
- 4 polls with progressive delays (3s, then 5s, then 7s, then 10s)
- Total verification window: ~25 seconds
- Each poll calls `get_positions()` and checks if any position for that market_id has size > 0.001
- Also checks active orders to provide diagnostic info

**During verification, market_id is added to `_verifying_close` set** to skip DSL/SL evaluation (MED-25), preventing the bot from triggering another SL on a position that's in the process of closing.

**False negative risk (position closed but verification says "still open"):**

1. **API eventual consistency:** If the exchange takes >25s to remove the position from `get_positions()` response, verification will return False. This is the primary risk.
2. **Stale API cache:** If the Lighter API has a read replica with replication lag, positions might appear open briefly after closure.
3. **Network errors:** Any exception during `get_positions()` is caught and logged — the position is considered "still open" for that poll attempt.

**Mitigations already in place:**
- The `estimated=True` fallback in `log_outcome` handles the case where verification fails after max attempts — the outcome is still logged (with the note that the fill price is estimated)
- Positions aren't removed from tracker until verification passes, so they get another chance on the next tick cycle
- The `_verifying_close` skip prevents double-triggering during verification

**Assessment:** The 25s window is reasonable for most exchanges. False negatives would result in retrying the close (not double-closing), which is the safe direction. The `estimated=True` fallback ensures outcomes are still recorded even when verification times out. **Acceptable risk.**

---

## Q7: Partial close handling in `close_all`

**Yes — partial close scenarios are handled.**

In `execute_ai_close_all` (executor.py ~L240-310):

```python
failed_positions = []

for i, (mid, pos) in enumerate(list(tracker.positions.items())):
    # ... submit close order ...
    
    if not sl_success:
        failed_positions.append(pos.symbol)
        continue  # Keeps position in tracker
    
    position_closed = await verify_position_closed(bot, api, mid, pos.symbol)
    
    if position_closed:
        # log_outcome, remove from tracker, alert
        tracker.remove_position(mid)
    else:
        failed_positions.append(pos.symbol)
        # Keeps position in tracker, sends warning alert

return len(failed_positions) == 0
```

**Behavior:**
- Successfully closed positions are removed from tracker and logged
- Failed positions remain in tracker and are added to `failed_positions`
- The return value indicates full success (True) or partial failure (False)
- Each failed position gets its own warning alert

**Potential improvement:** If a position fails verification, it stays in tracker but no retry is attempted within `close_all`. On the next tick, DSL/SL evaluation will run again, potentially re-triggering the close. This is functionally OK because the bot's tick loop will keep trying, but it means `close_all` might report partial failure even though the close eventually succeeds.

**Assessment: HANDLED CORRECTLY.** Failed positions stay tracked and get retried by the next tick cycle. No positions are orphaned (removed from tracker but still open on exchange).

---

## Q8: The `estimated=True` flag in `log_outcome`

**When it's used:**
- Only when `verify_position_closed()` fails after exhausting all retry attempts (`_max_close_attempts`)
- It means: "We submitted a close order, but couldn't confirm it filled. Logging with the trigger price as estimated exit."

**What it does:**
```python
reason_tag = f"{exit_reason} (estimated)" if estimated else exit_reason
```
The exit reason in the DB becomes e.g. `"dsl_hard_sl (estimated)"` or `"ai_close (estimated)"`.

**DB handling:**
```python
_db.log_outcome({
    ...
    "exit_reason": reason_tag,  # "(estimated)" suffix
    ...
})
```

The `estimated` flag is **encoded into the exit_reason string** rather than as a separate boolean column. This is a pragmatic approach — it works with any schema because it's just text. However:

**Pros:**
- Simple, works with existing DB schema
- Clearly visible in exit_reason field
- No schema migration needed

**Cons:**
- Harder to query/filter by estimated vs. actual (need string LIKE instead of boolean index)
- Can't easily compute stats on estimated-only outcomes

**Assessment: FUNCTIONALLY CORRECT.** The estimated flag properly surfaces that the exit price is not a confirmed fill. The trade data (entry_price, size_usd, etc.) is still accurate — only the exit_price and derived PnL are estimated. For failure-after-max-retries, this is the best available data.

---

## Summary of Findings

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | signal_processor._log_outcome wraps shared_utils.log_outcome — same function | — | ✅ OK |
| 2 | CRITICAL-4 pattern consistently followed for all exit paths | — | ✅ OK |
| 3 | Retry delays [15s, 1m, 5m, 15m] are reasonable | — | ✅ OK |
| 4 | Trailing SL calls _log_outcome after both success and max-failure | — | ✅ OK |
| 5 | **executor.py ROE in Telegram alert is raw price %, not leveraged ROE** | ⚠️ Low-Med | **Bug** |
| 6 | verify_position_closed uses 25s progressive polling — acceptable risk | — | ✅ OK |
| 7 | close_all handles partial failures — failed positions stay tracked | — | ✅ OK |
| 8 | estimated=True encoded in exit_reason string — works but hard to query | 📝 Note | ✅ OK |

### Recommended Fix

**Issue #5 (ROE display in executor.py):** Change the `roe` calculation in `execute_ai_close` (~L228) to:
```python
leverage = pos.dsl_state.leverage if pos.dsl_state else cfg.dsl_leverage
roe = pnl_pct * leverage  # Same as what log_outcome computes
```

This would make the Telegram alert consistent with the DB value and with the DSL exit alerts in execution_engine.py.
