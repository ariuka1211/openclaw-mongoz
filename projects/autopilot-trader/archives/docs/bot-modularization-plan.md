# Bot.py Modularization Plan

> **Goal:** Split `bot/bot.py` (3,285 lines) into focused modules with proper folder structure.  
> **Constraint:** No logic changes — pure extraction. All existing behavior preserved.  
> **Import strategy:** Direct imports via sys.path. Bot runs as `python3 bot.py` with cwd=bot/, so bot/ is on sys.path. Import rules:
> - Root-level modules (`config`, `dsl`, `auth_helper`): `import config` from anywhere
> - Subpackage modules: `from api.lighter_api import LighterAPI` from anywhere
> - **Intra-package (same subpackage):** must use full path: `from core.models import TrackedPosition` (NOT `from models import ...`)

---

## Current Structure

```
bot.py (3,285 lines) — 5 classes + proxy patch + entry point
├── SOCKS5 Proxy Patch      ~68 lines   (L42-109)
├── BotConfig               ~138 lines  (L129-266)
├── TelegramAlerter         ~36 lines   (L267-302)
├── TrackedPosition          ~18 lines  (L303-320)
├── PositionTracker         ~226 lines  (L321-546)
├── LighterAPI              ~582 lines  (L547-1128)
├── LighterCopilot         ~2,157 lines (L1129-3285)
│   ├── __init__ + start() + _get_balance    ~187 lines  → bot.py
│   ├── Signal/AI processing          ~914 lines  → signal_processor
│   ├── Position verification         ~120 lines  → signal_processor
│   ├── State management              ~338 lines  → state_manager
│   ├── Tick + position_tick          ~531 lines  → bot.py
│   ├── Quota/pacing helpers           ~40 lines  → bot.py
│   └── Cache + misc                  ~54 lines  → bot.py
└── main()                   ~23 lines  (L3263-3285)
```

## Proposed File Layout

```
bot/
├── bot.py                     ~550 lines  (entry point + orchestrator)
├── config.py                  ~140 lines  (BotConfig dataclass)
├── dsl.py                      178 lines  (unchanged — self-contained)
├── auth_helper.py               86 lines  (unchanged)
├── config.yml                   72 lines  (unchanged)
├── api/
│   ├── __init__.py              (empty)
│   ├── lighter_api.py         ~582 lines  (LighterAPI)
│   └── proxy_patch.py          ~68 lines  (SOCKS5 monkey-patch)
├── core/
│   ├── __init__.py              (empty)
│   ├── models.py                ~48 lines  (TrackedPosition + BotState)
│   ├── position_tracker.py     ~226 lines  (PositionTracker)
│   ├── signal_processor.py     ~750 lines  (AI decisions + signals + verification)
│   └── state_manager.py       ~340 lines  (save/load/reconcile/restore)
├── alerts/
│   ├── __init__.py              (empty)
│   └── telegram.py              ~36 lines  (TelegramAlerter)
├── state/                       (unchanged — runtime state files)
├── bot.log                      (unchanged)
└── venv/                        (unchanged)
```

**Before:** bot.py = 3,285 lines  
**After:** bot.py = ~550 lines (−83%), largest module = signal_processor.py at ~750 lines

---

## Module Breakdown

### Root-level

#### `config.py` (~140 lines) — Lines 129–266

**Contents:** `BotConfig` dataclass + `validate()` + `from_yaml()`  
**Imports:** `yaml`, `os`, `dataclasses`  
**Imported by:** everything

#### `dsl.py` (178 lines) — unchanged

Self-contained, only imports stdlib. Stays at root.

#### `auth_helper.py` (86 lines) — unchanged

---

### `api/` subpackage

#### `api/__init__.py` — empty

#### `api/proxy_patch.py` (~68 lines) — Lines 42–109

**Contents:** `_patched_rest_client_init()` + monkey-patch of `lighter.rest.RESTClientObject.__init__`  
**Imports:** `lighter.rest`, `aiohttp_socks`, `aiohttp`, `ssl`  
**Imported by:** `bot.py` — `import api.proxy_patch` (side-effect, must run before `import lighter`)

#### `api/lighter_api.py` (~582 lines) — Lines 547–1128

**Contents:** `LighterAPI` class — 28 methods covering init, auth, positions, prices, orders, quota, state persistence  
**Imports:** `config` (BotConfig), `auth_helper` (LighterAuthManager), `lighter`, `aiohttp_socks`, `aiohttp_retry`  
**Imported by:** `bot.py`, `core/signal_processor.py`, `core/state_manager.py`

---

### `core/` subpackage

#### `core/__init__.py` — empty

#### `core/models.py` (~48 lines)

**Contents:**
- `TrackedPosition` dataclass (L303–320, 18 lines) — position fields + DSL state
- `BotState` dataclass (NEW, ~30 lines) — shared mutable state

**Imports:** `dataclasses`, `datetime`, `dsl` (DSLState)  
**Imported by:** everything

#### `core/position_tracker.py` (~226 lines) — Lines 321–546

**Contents:** `PositionTracker` class — 8 methods  
**Key method:** `update_price()` — 134 lines (DSL evaluation + legacy trailing + triggers)  
**Imports:** `config` (BotConfig), `dsl` (DSLConfig/DSLState/DSLTier/evaluate_dsl), `core.models` (TrackedPosition)  
**Imported by:** `bot.py`

#### `core/signal_processor.py` (~750 lines) — 14 methods from LighterCopilot

**Extracted methods:**
| Method | Lines | Purpose |
|--------|-------|---------|
| `_process_signals` | 180 | Rule-based signal processing |
| `_validate_ai_decision` | 32 | AI decision field validation |
| `_process_ai_decision` | 94 | AI decision file reader + dispatcher |
| `_execute_ai_open` | 142 | AI open execution + verification |
| `_execute_ai_close` | 106 | AI close execution + verification |
| `_execute_ai_close_all` | 64 | Emergency close all |
| `_check_active_orders` | 42 | Active order lookup |
| `_verify_position_closed` | 29 | Poll exchange for close confirmation |
| `_verify_position_opened` | 48 | Poll exchange for open confirmation |
| `_get_fill_price` | 31 | Query actual fill price |
| `_resolve_market_id` | 29 | Symbol → market_id resolution |
| `_log_outcome` | 66 | Trade outcome logging to DB |
| `_write_ai_result` | 35 | AI result file writer |
| `_refresh_position_context` | 45 | Result file position refresh |

**Class:** `SignalProcessor`  
**Constructor receives:** `bot` (LighterCopilot reference), `api` (LighterAPI), `tracker` (PositionTracker), `cfg` (BotConfig), `alerts` (TelegramAlerter), `state` (BotState)

**Why `bot` reference:** Methods need:
- `bot._get_balance()` — called in `_process_signals` and `_execute_ai_open`
- `bot._auth_manager` — used in `_check_active_orders` and `_get_fill_price`
- `bot.api._cancel_order()` — called in close methods
- `bot._save_state()` — called after state changes

**External deps:** `ai-decisions/db.py` (DecisionDB — added via sys.path), `shared/ipc_utils.py` (safe_read_json — added via sys.path)  
**Imports:** `api.lighter_api`, `core.models`, `core.position_tracker`, `config`, `alerts.telegram`, `json`, `hashlib`, `asyncio`, `pathlib`, `time`, `logging`

#### `core/state_manager.py` (~340 lines) — 7 methods from LighterCopilot

**Extracted methods:**
| Method | Lines | Purpose |
|--------|-------|---------|
| `_serialize_dsl_state` | 17 | DSLState → JSON dict |
| `_write_equity_file` | 13 | Equity file for dashboard |
| `_save_state` | 47 | Full state persistence |
| `_load_state` | 90 | State restoration from disk |
| `_reconcile_state_with_exchange` | 107 | Startup reconciliation |
| `_restore_dsl_state` | 55 | DSL state restoration |
| `_reconcile_positions` | 51 | Saved vs live position matching |

**Class:** `StateManager`  
**Constructor receives:** `bot` (LighterCopilot reference), `api` (LighterAPI), `tracker` (PositionTracker), `cfg` (BotConfig), `alerts` (TelegramAlerter), `state` (BotState)

**Why `bot` reference:** `_load_state` reconstructs `bot.bot_managed_market_ids` and `_reconcile_state_with_exchange` modifies it. Also `_restore_dsl_state` sends alerts.

**Imports:** `api.lighter_api`, `core.models`, `core.position_tracker`, `config`, `alerts.telegram`, `json`, `os`, `pathlib`, `time`, `logging`, `datetime`

---

### `alerts/` subpackage

#### `alerts/__init__.py` — empty

#### `alerts/telegram.py` (~36 lines) — Lines 267–302

**Contents:** `TelegramAlerter` class — `__init__()` + `send()`  
**Imports:** `aiohttp` (inside `send()`), `logging`  
**Imported by:** `bot.py`, `core/signal_processor.py`, `core/state_manager.py`

---

## Shared State: `BotState` dataclass

Both `SignalProcessor` and `StateManager` need mutable access to bot state. `BotState` lives in `core/models.py`:

```python
@dataclass
class BotState:
    # Signal tracking
    opened_signals: set[int] = field(default_factory=set)
    last_signal_timestamp: str | None = None
    last_signal_hash: str | None = None
    signal_processed_this_tick: bool = False

    # AI decision tracking
    last_ai_decision_ts: str | None = None
    result_dirty: bool = False

    # Close tracking
    recently_closed: dict[int, float] = field(default_factory=dict)
    close_attempts: dict[str, int] = field(default_factory=dict)
    close_attempt_cooldown: dict[str, float] = field(default_factory=dict)
    dsl_close_attempts: dict[str, int] = field(default_factory=dict)
    dsl_close_attempt_cooldown: dict[str, float] = field(default_factory=dict)
    ai_close_cooldown: dict[str, float] = field(default_factory=dict)

    # Position management
    bot_managed_market_ids: set[int] = field(default_factory=set)
    pending_sync: set[int] = field(default_factory=set)
    verifying_close: set[int] = field(default_factory=set)

    # Quota/order pacing (only used by bot.py, but included for save/load)
    last_order_time: float = 0

    # Misc
    api_lag_warnings: dict[str, float] = field(default_factory=dict)
    no_price_ticks: dict[int, int] = field(default_factory=dict)
    idle_tick_count: int = 0
    kill_switch_active: bool = False
    saved_positions: dict | None = None
    position_sync_failures: int = 0
```

Created once in `LighterCopilot.__init__()`. Passed to `SignalProcessor` and `StateManager`. All three read/write the same object — single-threaded asyncio, no synchronization.

**Non-shared state stays in bot.py:**
- `_last_quota_alert_time`, `_quota_alert_interval` — quota alert timing
- `_last_quota_emergency_warn` — rate-limiting
- `_close_cooldown_seconds`, `_max_close_attempts`, etc. — constants

---

## What Remains in `bot.py` (~550 lines)

```
bot.py
├── Imports + logging setup                          ~20 lines
├── class LighterCopilot
│   ├── __init__()                                   ~50 lines  (create all components, wire BotState)
│   ├── start()                                     ~113 lines  (startup sequence + main loop)
│   ├── _get_balance()                               ~13 lines  (thin API wrapper)
│   ├── _tick()                                     ~263 lines  (orchestrator: sync → process → evaluate)
│   ├── _process_position_tick()                    ~268 lines  (single-position evaluation + execution)
│   ├── _prune_caches()                              ~46 lines  (delegate to state + per-module prune)
│   ├── Quota helpers                                ~40 lines  (pace/skip/emergency)
│   ├── _mark_order_submitted()                       ~4 lines
│   └── _shutdown()                                   ~8 lines
└── main()                                           ~23 lines  (config + validate + run)
```

`_tick()` and `_process_position_tick()` stay because they orchestrate all components.

---

## Cross-Module Call Map

| Caller | Calls | Via |
|--------|-------|-----|
| `bot._tick()` | `signal_processor.process_signals()` | direct |
| `bot._tick()` | `signal_processor.process_ai_decision()` | direct |
| `bot._tick()` | `signal_processor.refresh_position_context()` | direct |
| `bot._tick()` | `state_manager.reconcile_positions()` | direct |
| `bot._tick()` | `state_manager.save_state()` | direct |
| `bot._tick()` | `state_manager.load_state()` | direct |
| `bot.start()` | `state_manager.reconcile_state_with_exchange()` | direct |
| `signal_processor.*` | `bot._get_balance()` | `self.bot` ref |
| `signal_processor.*` | `bot._auth_manager` | `self.bot` ref |
| `signal_processor.*` | `self.api._cancel_order()` | direct |
| `signal_processor.*` | `self.tracker.*` | direct |
| `signal_processor.*` | `self.state.*` | direct (BotState) |
| `state_manager.*` | `self.state.*` | direct (BotState) |
| `state_manager.*` | `self.api.get_positions()` | direct |

---

## External File References to Update

| File | Reference | Change |
|------|-----------|--------|
| `bot.service` | `ExecStart=...python3 bot.py` | **No change** |
| `dashboard/api/system.py:51` | `_pgrep("bot.py")` | **No change** |
| `bot/healthcheck.py` | Scans for `bot.py` | **No change** |
| `bot/README.md` | File structure | Update with new layout |
| `docs/cheatsheet.md:16` | `bot/bot.py` description | Update file map |
| `docs/autopilot-trader.md` | Architecture diagram + section 3.1 | Update bot section |
| `docs/unified-dashboard-plan.md:133` | `pgrep -f bot.py` | **No change** |

---

## Execution Order (each = 1 commit)

| Step | Action | Creates | Risk |
|------|--------|---------|------|
| 1 | Create folder structure | `api/`, `core/`, `alerts/` + `__init__.py` | None |
| 2 | Extract `proxy_patch.py` → `api/` | `api/proxy_patch.py` | None |
| 3 | Extract `config.py` → root | `config.py` | Low |
| 4 | Extract `telegram.py` → `alerts/` | `alerts/telegram.py` | Low |
| 5 | Extract `models.py` + `BotState` → `core/` | `core/models.py` | Low |
| 6 | Extract `position_tracker.py` → `core/` | `core/position_tracker.py` | Medium |
| 7 | Extract `lighter_api.py` → `api/` | `api/lighter_api.py` | Medium |
| 8 | Extract `signal_processor.py` → `core/` | `core/signal_processor.py` | **High** |
| 9 | Extract `state_manager.py` → `core/` | `core/state_manager.py` | Medium |
| 10 | Update docs | cheatsheet + autopilot-trader.md | None |
| 11 | Final verification | restart bot, check all services | Full test |

---

## Verification Checklist

After each extraction step:
- [ ] `cd bot && python3 -c "import bot"` — no import errors
- [ ] `python3 -c "from api.lighter_api import LighterAPI"` — subpackage loads
- [ ] `python3 -c "from core.position_tracker import PositionTracker"` — subpackage loads
- [ ] `python3 -c "from core.signal_processor import SignalProcessor"` — subpackage loads
- [ ] `python3 -c "from core.state_manager import StateManager"` — subpackage loads
- [ ] `systemctl restart bot` — service starts clean
- [ ] `journalctl -u bot -n 30` — no errors in first tick

Final:
- [ ] bot.py < 600 lines
- [ ] Full tick cycle works (sync → signals → DSL evaluation)
- [ ] AI decision processing works (open + close + close_all)
- [ ] State save/load survives restart
- [ ] Dashboard endpoints still 200

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Import order — proxy_patch before lighter | `import api.proxy_patch` first in bot.py |
| Shared state mutation | `BotState` dataclass, single instance, passed by ref |
| DSL circular deps | `dsl.py` at root, one-way import from `core/models.py` |
| `ai-decisions/db.py` path | signal_processor adds `_AI_TRADER_DIR` to sys.path |
| `shared/ipc_utils.py` path | signal_processor adds `_shared_dir` to sys.path |
| Intra-package imports | Use full path: `from core.models import ...` (NOT `from models import ...`) |
| `bot._prune_caches()` touches cross-module state | Delegate: `signal_processor.prune_caches()`, `state_manager.prune_caches()` |
