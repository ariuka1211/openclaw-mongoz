# Fix BUG-08 - Extract shared IPC utilities

## Summary
Extracted duplicated safe_read_json and atomic_write functions into shared/ipc_utils.py.

## Problem
Three files had independent copies of the same IPC utility functions.

## Solution
Created shared/ipc_utils.py; each consumer imports from there via sys.path manipulation.

## Files changed
- shared/ipc_utils.py (new)
- shared/__init__.py (new)
- signals/ai-trader/ai_trader.py (import + removed local defs)
- executor/bot.py (import + removed local def)
- signals/ai-trader/context_builder.py (import + removed local def)
- docs/fix-bug-08.md (new)
