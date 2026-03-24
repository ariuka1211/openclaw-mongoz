# Merged Code Review — 20 Fix Branches into main

**Date:** 2026-03-23  
**Reviewer:** Automated code review (subagent)  
**Scope:** Post-merge review of ~20 fix branches that touched overlapping files  
**Verdict:** ⚠️ **NEEDS FIX** — 1 bug found (NameError), rest is clean

---

## File-by-File Results

### 1. `projects/autopilot-trader/executor/bot.py` — ⚠️ FAIL

**Merge artifacts:** None — no conflict markers, no duplicate code blocks  
**Syntax:** Clean — proper indentation, unmatched parens, colons all correct  
**Imports:** Clean — `ipc_utils` path added correctly, `DecisionDB` import wrapped in try/except

#### Issue 1: `NameError` — undefined `labels` in legacy SL failure path (BUG)

- **Line 2599** — `f"Action: {labels.get(action, action)}\n"`
- **Severity:** HIGH — crashes when a legacy SL (stop_loss or trailing_take_profit) order fails on 4th attempt
- **Impact:** The `labels` dict is defined only inside the DSL exit block (`if action in ("tier_lock", "stagnation", "hard_sl"):` at line 2422). The legacy SL code at line 2599 is outside that block, so `labels` is not defined.
- **Trigger:** Any time `_process_position_tick` handles a legacy stop_loss or trailing_take_profit, and `execute_sl()` returns False (rejected by exchange or rate-limited) on the 4th consecutive attempt (line 2594: `if attempts >= 4:`)
- **Effect:** The `NameError` propagates up to `_tick()`, which catches it with `except Exception as e` at line ~1947. The position stays in the tracker (correct), but the alert for manual intervention is **not sent** (bad — user won't know the SL keeps failing).
- **Root cause:** Copy-paste from DSL exit block. The DSL block defines `labels` at line 2425 for its own use. The legacy failure alert at line 2599 reused the same template string without defining its own `labels`.
- **Fix:** Quick fix — replace `{labels.get(action, action)}` with a direct string like `{action.replace('_', ' ').upper()}`, or define a local labels dict:
  ```python
  # In the legacy SL failure block, change:
  f"Action: {labels.get(action, action)}\n"
  # to:
  f"Action: {action.replace('_', ' ').upper()}\n"
  ```

#### Everything else in bot.py: CLEAN

- **Kill switch check** (line ~1940): Runs at top of `_tick()`, before position sync. Correct — prevents new opens while still managing existing positions.
- **EDGE-03 (None handling):** `get_positions()` returns None on failure → caller checks `if live_positions is None` at line ~1965, preserves tracker state. ✓
- **BUG-07 (unmanaged positions):** Filter at line ~2012 checks `mid not in self.bot_managed_market_ids`. ✓
- **BUG-03 (phantom positions):** `_verify_position_opened()` called after every open at lines ~1160, ~1245. ✓
- **BUG-06 (orphaned positions):** `_no_price_ticks` counter + alert at lines ~2070-2082. ✓
- **STATE-01 (persistence):** `_save_state()` and `_load_state()` use consistent path, atomic writes (tmp + replace), monotonic→remaining seconds conversion for portability. ✓
- **EDGE-01 (idle polling):** `_idle_tick_count` increments when flat + no signals, resets on activity, sleep interval extends to 60s. ✓
- **FIELD NAME consistency:** Bot writes `position_size_usd` in results, reads `requested_size_usd` with `size_usd` fallback in `_validate_ai_decision()`. All consumers consistent. ✓
- **DSL state restoration:** `_reconcile_positions()` is a no-op after first successful reconciliation (sets `self._saved_positions = None`). ✓
- **Quota management:** Cooldown, backoff, pacing, emergency mode — all properly scoped and pruned in `_prune_caches()`. ✓
- **Dead code:** None found. The `ProxySignerClient` class is defined twice but the `__init__` version is unused dead code (not from a merge — it was there before). Minor cleanup opportunity but not merge-related.

---

### 2. `projects/autopilot-trader/signals/ai-trader/ai_trader.py` — ✅ PASS

**Merge artifacts:** None  
**Syntax:** Clean  
**Imports:** Clean — `ipc_utils` path added, imports `atomic_write` and `safe_read_json`

#### Key findings:

- **IPC protocol consistency:** AI trader writes decisions with `requested_size_usd` (line ~275), bot reads `requested_size_usd` with `size_usd` fallback (line ~855). Match confirmed. ✓
- **ACK protocol:** AI trader writes `.ack` file with `decision_id`, bot reads it and writes same. Race condition guarded by double-check at steps 2 and 4. ✓
- **Result correlation (IPC-02):** `_last_sent_decision_id` tracked, `_check_bot_result()` correlates via `processed_decision_id`. ✓
- **Emergency halt:** Writes `close_all` without `requested_size_usd`, bypasses normal IPC flow, deletes ACK file. Bot's `_validate_ai_decision` returns None early for "close_all" (no size check). ✓
- **State hash change detection:** Only calls LLM when signals/positions change. Properly resets on state change. ✓

---

### 3. `projects/autopilot-trader/signals/ai-trader/db.py` — ✅ PASS

**Merge artifacts:** None  
**Syntax:** Clean  
**Schema:** `roe_pct` column exists in table definition AND has migration logic (line ~67: `ALTER TABLE outcomes ADD COLUMN roe_pct REAL` if missing). ✓

#### Key findings:

- **`log_outcome()`:** Accepts `roe_pct` from outcome dict. ✓
- **`update_latest_outcome()`:** Updates `roe_pct` along with other fields. ✓
- **`get_recent_outcomes()`:** Returns `roe_pct` at index 8. ✓
- **`count_recent_rejections()`:** Has redundant datetime calculation (line ~204) but the actual query uses the `cutoff_iso` variable from `cutoff_time`, which is correct. Minor dead code but not a bug.
- **`purge_old_data()`:** Decisions purged after 7 days, alerts after 7 days (acknowledged) / 30 days (stale), outcomes after 30 days. Vacuum after purge. ✓

---

### 4. `projects/autopilot-trader/shared/ipc_utils.py` — ✅ PASS

**Merge artifacts:** None  
**Syntax:** Clean  
**Imports from:** None (standalone module)  
**Imported by:** bot.py, ai_trader.py, context_builder.py — all paths verified correct. ✓

#### Key findings:

- **`atomic_write()`:** tmp + `os.replace()` — atomic on POSIX. ✓
- **`safe_read_json()`:** Retry on partial read (handles mid-write reads from concurrent atomic_write). ✓
- **No issues found.**

---

### 5. `projects/autopilot-trader/signals/ai-trader/context_builder.py` — ✅ PASS

**Merge artifacts:** None  
**Syntax:** Clean  
**Imports:** `ipc_utils` path added, `from ipc_utils import safe_read_json`. Path calculation correct (parent.parent / "shared"). ✓

#### Key findings:

- **`_calc_roe()`:** Uses `position_size_usd` with `size_usd` fallback — consistent with bot result format. ✓
- **Prompt building:** All field accesses use `.get()` with defaults. ✓
- **Injection sanitizer:** `strip_injection_patterns` imported by reflection.py. ✓

---

### 6. `projects/autopilot-trader/signals/ai-trader/reflection.py` — ✅ PASS

**Merge artifacts:** None  
**Syntax:** Clean  
**Imports:** `from context_builder import strip_injection_patterns` — works (function defined at module level in context_builder.py). ✓

#### Key findings:

- **Strategy memory:** Appends timestamped entries to memory file. Reads existing content before appending. ✓
- **Quantitative stats:** Direct SQL queries on `outcomes` and `decisions` tables. Compatible with `roe_pct` column. ✓

---

### 7. `projects/autopilot-trader/signals/ai-trader/safety.py` — ✅ PASS

**Merge artifacts:** None (not significantly touched by merges)  
**Syntax:** Clean  

#### Key findings:

- **Field name consistency:** Uses `position_size_usd` with `size_usd` fallback at line ~152: `abs(p.get("position_size_usd", p.get("size_usd", 0)))`. ✓
- **Schema validation (Rule 11):** Validates required fields for each action type. ✓
- **Kill switch thresholds:** Uses config values, not hardcoded. ✓
- **Rate limiting:** `_check_rate_limit()` properly prunes old timestamps. ✓

---

## Summary of Issues

| # | File | Line | Severity | Description | Fix Difficulty |
|---|------|------|----------|-------------|---------------|
| 1 | bot.py | 2599 | **HIGH** | `NameError: labels` — undefined variable in legacy SL failure alert | **Quick fix** — replace `{labels.get(action, action)}` with `{action.replace('_', ' ').upper()}` or define local dict |

### No other issues found:

- ✅ No merge conflict markers anywhere
- ✅ No duplicate methods/functions
- ✅ No broken imports
- ✅ No inconsistent field names across components
- ✅ DB schema migrations work correctly
- ✅ IPC protocol (decision/ACK/result) is consistent between bot.py and ai_trader.py
- ✅ Kill switch runs before position sync (correct ordering)
- ✅ EDGE-03 None handling, BUG-07 unmanaged filter, STATE-01 persistence, EDGE-01 idle polling all work correctly
- ✅ DSL state restoration is idempotent (no-op after first reconciliation)

---

## Overall Assessment

**Status: ⚠️ NEEDS FIX (1 bug)**

**The bug:** When a legacy SL (stop_loss or trailing_take_profit) order fails 4 times, the bot tries to send an alert referencing an undefined `labels` variable. This causes a `NameError` that gets caught by the generic exception handler, but the critical alert for manual intervention is **silently lost**. Positions stuck in a failing close loop won't alert the user.

**Fix is quick:** One line change at bot.py:2599. Replace the `labels.get(action, action)` call with a direct string conversion.

**Safe to deploy after fix?** Yes — all other merge resolutions look correct. The IPC protocol, field names, DB schema, and logic ordering are all consistent. No merge artifacts, no dead code from conflicting branches, no duplicate methods.

**Recommendation:** Apply the one-line fix, then start services.
