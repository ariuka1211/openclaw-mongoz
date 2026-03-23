# Fix BUG-08 — Extract shared IPC utilities

## Summary

Extracted duplicated `safe_read_json` and `atomic_write` functions into a shared module at `projects/autopilot-trader/shared/ipc_utils.py`.

## Problem

Three files had independent copies of the same IPC utility functions:
- `signals/ai-trader/ai_trader.py` — had both `safe_read_json` and `atomic_write`
- `executor/bot.py` — had `safe_read_json`
- `signals/ai-trader/context_builder.py` — had `safe_read_json`

If a bug fix was applied to one, the others could silently diverge.

## Solution

Created `shared/ipc_utils.py` with canonical implementations (based on `ai_trader.py`'s version — most complete with logging).

Each consumer adds `shared/` to `sys.path`:

```python
_shared_dir = Path(__file__).resolve().parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json  # and atomic_write for ai_trader.py
```

## Files changed

| File | Change |
|------|--------|
| `shared/ipc_utils.py` | new — `atomic_write`, `safe_read_json` |
| `shared/__init__.py` | new — package marker |
| `signals/ai-trader/ai_trader.py` | import from shared, removed local defs |
| `executor/bot.py` | import from shared, removed local def |
| `signals/ai-trader/context_builder.py` | import from shared, removed local def |
