# Fix: BUG-02 — ACK Only Written After Execution Completes

## Problem

In `_process_ai_decision()`, the ACK file and result file were written **outside** the execution logic. If `_execute_ai_open()`, `_execute_ai_close()`, or `_execute_ai_close_all()` threw an uncaught exception, the ACK was still written. The AI trader assumed the decision was processed and moved on — the opportunity was lost.

Additionally, `close_all` had its own separate ACK path (with `return` before the common path), creating two independent ACK write sites.

## Fix

Restructured `_process_ai_decision()` so all execution branches (open/close/close_all) run inside a single `try` block. The `_write_ai_result()` and ACK write are now **inside** that try, after execution completes.

If execution throws an uncaught exception:
- The `except` block logs the error with full traceback
- **No ACK is written** — the AI trader will re-deliver the decision
- **No result is written** — we can't report success/failure if execution didn't run

If execution completes (success or failure, both are fine):
- Result is written with `success=True/False`
- ACK is written so the AI trader knows to move on

## What Changed

- `executor/bot.py` — `_process_ai_decision()` method (~30 lines restructured)
- Unified `close_all` into the common try block (was previously a separate path with its own ACK)
- Added error logging for crashes (was silent before)

## Verification

```bash
git diff executor/bot.py  # review the change
```
