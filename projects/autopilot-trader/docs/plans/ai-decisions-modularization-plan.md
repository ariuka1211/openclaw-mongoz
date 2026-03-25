# AI Decisions Modularization Plan

> **Goal:** Break `ai_trader.py` (625 lines) and `context_builder.py` (611 lines) into focused, single-responsibility modules with subfolder organization.  
> **Constraint:** No logic changes — pure extraction. All existing behavior preserved.  
> **Pattern:** Same as bot modularization — managers receive `ai_trader` reference in constructor, access state via `self.ai_trader.*`.

---

## Current Structure

```
ai-decisions/ (2,303 lines total)
├── ai_trader.py       (625 lines) — daemon loop, cycle execution, IPC, emergency halt
├── context_builder.py (611 lines) — signals, positions, patterns, stats, prompt assembly, sanitization
├── db.py              (629 lines) — SQLite decision journal (clean, unchanged)
├── llm_client.py      (186 lines) — OpenRouter LLM client (clean, unchanged)
├── safety.py          (252 lines) — hard safety rules (clean, unchanged)
├── config.json
├── prompts/
│   ├── system.txt
│   └── decision.txt
└── state/
    ├── trader.db
    └── patterns.json
```

### Problems in the two big files

**ai_trader.py (625 lines)** — 7 tangled responsibilities:
1. `parse_decision_json()` — pure function, no reason to be here
2. `execute_cycle()` — 200 lines doing 10 things (context gathering, hashing, LLM call, parsing, safety, IPC, logging)
3. `_send_to_bot()` — IPC protocol with ACK race guards
4. `_check_bot_result()` — result file correlation
5. `_emergency_halt()` — close_all with retry loop
6. `run_forever()` — daemon lifecycle (this should stay)
7. `main()` — entry point (this should stay)

**context_builder.py (611 lines)** — 6 tangled concerns:
1. Token estimation (tiktoken) — utility
2. Injection sanitizer (regex patterns) — security
3. Signal + position file I/O — data reading
4. Pattern rules (load/save/decay/reinforce) — state management
5. Stats formatting (live stats, hold regret) — prompt sections
6. Final prompt assembly (token budget, section ordering) — orchestration

---

## Proposed File Layout

```
ai-decisions/
├── ai_trader.py                (~180 lines) — thin coordinator: init, daemon loop, main()
├── cycle_runner.py             (~250 lines) — cycle orchestration
├── db.py                        (629 lines) — unchanged
├── llm_client.py                (186 lines) — unchanged
├── safety.py                    (252 lines) — unchanged
├── config.json
├── prompts/
│   ├── system.txt
│   └── decision.txt
├── state/
│   ├── trader.db
│   └── patterns.json
├── llm/                         ← NEW
│   ├── __init__.py
│   └── parser.py               (~45 lines) — parse LLM JSON responses
├── context/                     ← NEW
│   ├── __init__.py
│   ├── token_estimator.py      (~25 lines) — token counting with tiktoken fallback
│   ├── sanitizer.py            (~65 lines) — prompt injection detection + stripping
│   ├── data_reader.py          (~110 lines) — read signals + positions from files
│   ├── pattern_engine.py       (~120 lines) — learned pattern rules with decay
│   ├── stats_formatter.py      (~80 lines) — format performance stats + hold regret
│   └── prompt_builder.py       (~170 lines) — assemble final LLM prompt with token budget
└── ipc/                         ← NEW
    ├── __init__.py
    └── bot_protocol.py          (~200 lines) — send decisions, receive results, emergency halt
```

**Before:** 5 files, 2,303 lines, largest file 629 lines  
**After:** 14 files, ~2,400 lines, largest file 629 lines (unchanged db.py), biggest new module ~250 lines

---

## Module Breakdown

### Root level — unchanged files

| File | Lines | Status |
|------|-------|--------|
| `db.py` | 629 | Unchanged — clean SQLite journal |
| `llm_client.py` | 186 | Unchanged — clean OpenRouter client |
| `safety.py` | 252 | Unchanged — clean safety rules |

### Root level — changed files

#### `ai_trader.py` (~180 lines, was 625) — thin coordinator

**Stays here:**
- `load_prompts()` — trivial, only used by AITrader
- Logging setup
- `AITrader.__init__()` — create all managers, wire references
- `run_forever()` — daemon loop (unchanged, pure orchestration)
- `main()` — config loading, entry point

**Moves out:**
- `parse_decision_json()` → `llm/parser.py`
- `execute_cycle()` → `cycle_runner.py`
- `_send_to_bot()` → `ipc/bot_protocol.py` (as `send_decision()`)
- `_check_bot_result()` → `ipc/bot_protocol.py` (as `check_result()`)
- `_emergency_halt()` → `ipc/bot_protocol.py` (as `emergency_halt()`)

**New constructor wiring:**
```python
from llm.parser import parse_decision_json
from context.data_reader import DataReader
from context.pattern_engine import PatternEngine
from context.stats_formatter import StatsFormatter
from context.prompt_builder import PromptBuilder
from ipc.bot_protocol import BotProtocol
from cycle_runner import CycleRunner

class AITrader:
    def __init__(self, config: dict):
        self.config = config
        self.db = DecisionDB(config["db_path"])
        # ... resolve paths, existing init ...

        self.safety = SafetyLayer(config, self.db)
        self.llm = LLMClient(config["llm"])
        self.system_prompt, self.decision_template = load_prompts()
        self.system_prompt = self.system_prompt.format(
            max_positions=self.safety.max_positions
        )

        # Context managers (receive self reference)
        self.data_reader = DataReader(self)
        self.pattern_engine = PatternEngine(self)
        self.stats_formatter = StatsFormatter(self)
        self.prompt_builder = PromptBuilder(self)

        # IPC
        self.bot_ipc = BotProtocol(self)

        # Cycle orchestrator
        self.cycle_runner = CycleRunner(self)

        # Cycle state (stays here)
        self._last_state_hash = None
        self._cycles_skipped = 0
        self._last_processed_outcome_ts = None
        # ... rest of state vars ...
```

#### `cycle_runner.py` (~250 lines) — cycle orchestration

**Extracted from:** `AITrader.execute_cycle()` (~200 lines)

**Class:** `CycleRunner`  
**Constructor receives:** `ai_trader` reference

**Accesses via `self.ai_trader`:**
- `self.ai_trader.data_reader` — read signals, positions
- `self.ai_trader.prompt_builder` — build LLM prompt
- `self.ai_trader.llm` — call LLM
- `self.ai_trader.safety` — validate decisions, record losses
- `self.ai_trader.bot_ipc` — send decisions to bot
- `self.ai_trader.db` — log decisions, read history/outcomes
- `self.ai_trader.system_prompt` / `decision_template` — prompt templates
- `self.ai_trader._last_state_hash`, `_cycles_skipped`, `_last_processed_outcome_ts` — cycle state

**Methods (each 20-40 lines, independently testable):**

| Method | Purpose |
|--------|---------|
| `execute(cycle_id)` | Orchestrator — calls steps in sequence |
| `_gather_context()` | Read signals (freshness check + retry), read positions |
| `_check_state_changed(signals, positions)` | Hash top-10 signals + positions, skip if unchanged |
| `_process_losses(outcomes)` | Scan outcomes for new losses, call `safety.record_loss()` |
| `_call_llm(signals, positions, history, outcomes, config)` | Build prompt, call LLM, return (raw, tokens, latency) |
| `_parse_and_validate(raw_response, positions, signals, equity)` | Parse JSON, safety check, return (decision, safe, reasons) |
| `_execute_decision(decision, equity)` | Send to bot, poll result, handle timeout/failure |
| `_log_result(...)` | DB logging + cost calculation |

**Important:** CycleRunner reads history/outcomes directly from `self.ai_trader.db`, NOT through PromptBuilder. Data access should be at the point of use.

---

### `llm/` subpackage

#### `llm/parser.py` (~45 lines) — LLM response parsing

**Extracted from:** `ai_trader.py` `parse_decision_json()` (~40 lines)

Pure function. No state, no class.

```python
def parse_decision_json(raw: str) -> dict:
    """Parse LLM response into decision dict. Handles markdown fences and extra text."""
```

**Imports:** `json`, `logging`  
**Testable:** ✅ Pure function — test with clean JSON, markdown fences, extra text, unmatched braces, no JSON

---

### `context/` subpackage

#### `context/token_estimator.py` (~25 lines) — token counting

**Extracted from:** `context_builder.py` lines ~24-44 (tiktoken setup + `estimate_tokens()`)

```python
MAX_PROMPT_TOKENS = 16000

def estimate_tokens(text: str) -> int:
    """Estimate token count. Uses tiktoken cl100k_base if available, else len(text)//4."""
```

**Imports:** `logging`, optional `tiktoken`  
**Testable:** ✅ Pure function — test known strings, empty string, fallback path

#### `context/sanitizer.py` (~65 lines) — prompt injection detection

**Extracted from:** `context_builder.py` lines ~50-100 (regex patterns + strip + sanitize)

```python
_INJECTION_PATTERNS = [...]  # 20+ regex patterns

def strip_injection_patterns(text: str) -> str:
    """Truncate text at injection attempt point. Returns [blocked] if entire text consumed."""

def sanitize_reasoning(text: str | None) -> str:
    """Truncate to 200 chars and strip injection attempts from LLM reasoning."""
```

**Imports:** `re`  
**Testable:** ✅ Pure function — security-critical, needs thorough tests (injection variants, edge cases)

#### `context/data_reader.py` (~110 lines) — read signals + positions from files

**Extracted from:** `context_builder.py` methods: `read_signals()`, `_filter_traded_symbols()`, `read_positions()`

**Class:** `DataReader`  
**Constructor receives:** `ai_trader` reference

**Why `ai_trader`:**
- `read_positions()` falls back to `ai_trader.db.conn` when result file missing
- `_filter_traded_symbols()` calls `ai_trader.db.get_recently_traded_symbols()`
- Paths resolved from `ai_trader.config` at init

**Methods:**
```python
class DataReader:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader
        # Resolve paths from config
        self.signals_file = Path(...) / config["signals_file"]
        self.result_file = Path(...) / config.get("result_file", ...)

    def read_signals(self) -> tuple[list[dict], dict]:
        """Read signals.json. Return (top_opportunities, config_dict).
        Filters to top N by compositeScore, removes recently traded symbols."""

    def read_positions(self) -> list[dict]:
        """Read positions from bot result file. DB fallback if file missing."""

    def _filter_traded_symbols(self, opportunities: list[dict]) -> list[dict]:
        """Remove symbols traded in same direction within cooldown window."""
```

**Imports:** `json`, `logging`, `pathlib.Path`, `shared.ipc_utils`

#### `context/pattern_engine.py` (~120 lines) — learned pattern rules with decay

**Extracted from:** `context_builder.py` methods: `_load_patterns()`, `_save_patterns()`, `read_patterns()`, `decay_patterns()`, `reinforce_pattern()`, `build_patterns_section()`

**Class:** `PatternEngine`  
**Constructor receives:** `ai_trader` reference (needs config for patterns_file path)

**Methods:**
```python
class PatternEngine:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader
        self.patterns_file = Path(...) / config.get("patterns_file", "state/patterns.json")

    def read_patterns(self) -> list[dict]:
        """Active patterns with confidence >= 0.4."""

    def decay_patterns(self, decay: float = 0.02):
        """Subtract decay from each pattern. Drop below 0.3."""

    def reinforce_pattern(self, rule_text: str, boost: float = 0.1):
        """Bump existing pattern confidence or add new one at 0.5."""

    def build_section(self) -> str:
        """Format active patterns as prompt section."""

    def _load(self) -> dict:
        """Load patterns.json."""

    def _save(self, data: dict):
        """Write patterns.json atomically."""
```

**Imports:** `json`, `logging`, `pathlib.Path`  
**Testable:** ✅ With temp dirs — load, save, decay, reinforce, section formatting

#### `context/stats_formatter.py` (~80 lines) — format performance stats + hold regret

**Extracted from:** `context_builder.py` methods: `build_live_stats_section()`, `build_hold_regret_section()`

**Class:** `StatsFormatter`  
**Constructor receives:** `ai_trader` reference (needs `ai_trader.db` for queries)

**Methods:**
```python
class StatsFormatter:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    def build_live_stats_section(self) -> str:
        """Format performance window (last 20 outcomes): win rate by direction, hold time, streak."""

    def build_hold_regret_section(self) -> str:
        """Format hold regret (last 6h): which held signals were later traded profitably."""
```

**Imports:** `logging`  
**Testable:** ✅ With mocked db — pass in stats data, verify formatted output

#### `context/prompt_builder.py` (~170 lines) — assemble final LLM prompt

**Extracted from:** `context_builder.py` method: `build_prompt()` (~150 lines) + `_calc_roe()` (~15 lines)

**Class:** `PromptBuilder`  
**Constructor receives:** `ai_trader` reference

**Does NOT read data** — receives it as parameters. Delegates section building to `stats_formatter` and `pattern_engine`.

**Methods:**
```python
class PromptBuilder:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    def build_prompt(
        self,
        signals: list[dict],
        positions: list[dict],
        history: list[dict],
        outcomes: list[dict],
        signals_config: dict,
    ) -> str:
        """Assemble LLM user prompt from pre-gathered data.
        Calls stats_formatter and pattern_engine for sections.
        Enforces MAX_PROMPT_TOKENS budget by truncating new sections first."""

    @staticmethod
    def _calc_roe(position: dict, equity: float = 0) -> float:
        """Calculate ROE% — cross margin (notional/equity) or isolated (leverage)."""
```

**Imports:** `logging`, `datetime`, `context.token_estimator`, `context.sanitizer`  
**Testable:** ✅ `_calc_roe` is pure. `build_prompt` testable with mocked sub-managers.

**Note:** `read_recent_decisions()` and `read_recent_outcomes()` are NOT here. They were thin pass-throughs to db. Callers (`CycleRunner`) call `self.ai_trader.db` directly.

---

### `ipc/` subpackage

#### `ipc/bot_protocol.py` (~200 lines) — send decisions, receive results, emergency halt

**Extracted from:** `ai_trader.py` methods: `_send_to_bot()`, `_check_bot_result()`, `_emergency_halt()`

**Class:** `BotProtocol`  
**Constructor receives:** `ai_trader` reference

**Why `ai_trader`:**
- Reads/writes `ai_trader.decision_file` and `ai_trader.result_file`
- Sets `ai_trader._last_sent_decision_id` after send
- Sets `ai_trader.emergency_halt` after confirmed close_all
- Reads `ai_trader.db` during emergency halt for position fallback

**Methods:**
```python
class BotProtocol:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    async def send_decision(self, decision: dict, equity: float = 1000) -> bool:
        """IPC protocol: ACK check, atomic write, stale timeout, TOCTOU race guard."""

    async def check_result(self, decision_id: str) -> dict | None:
        """Read result file, correlate via processed_decision_id."""

    async def emergency_halt(self, reason: str):
        """Write close_all, poll for confirmation (60s), retry with updated positions."""
```

**Imports:** `asyncio`, `json`, `logging`, `uuid`, `datetime`, `pathlib.Path`, `shared.ipc_utils`  
**Testable:** ✅ With mocked file I/O — ACK flow, stale timeout, atomic write, race guard

---

## Data Flow — How a Cycle Works After Modularization

```
run_forever()
  └─► cycle_runner.execute(cycle_id)
        │
        ├─ 1. data_reader.read_signals()          ← context/data_reader.py
        │     returns (signals, config)
        │
        ├─ 2. data_reader.read_positions()         ← context/data_reader.py
        │     returns positions
        │
        ├─ 3. _check_state_changed(signals, pos)   ← cycle_runner.py (hash comparison)
        │     skip if unchanged
        │
        ├─ 4. db.get_recent_decisions(20)          ← db.py (direct, no wrapper)
        ├─ 5. db.get_recent_outcomes(10)           ← db.py (direct, no wrapper)
        │
        ├─ 6. _process_losses(outcomes)             ← cycle_runner.py
        │     calls safety.record_loss()
        │
        ├─ 7. prompt_builder.build_prompt(          ← context/prompt_builder.py
        │       signals, positions, history,          calls stats_formatter + pattern_engine
        │       outcomes, config)
        │
        ├─ 8. llm.call(system, user_prompt)        ← llm_client.py
        │
        ├─ 9. parse_decision_json(raw)              ← llm/parser.py
        │
        ├─ 10. safety.validate(decision, ...)       ← safety.py
        │
        ├─ 11. bot_ipc.send_decision(decision)      ← ipc/bot_protocol.py
        │      bot_ipc.check_result(decision_id)     ← polls up to 30s
        │
        └─ 12. db.log_decision(...)                 ← db.py
```

Each step is a separate method on `CycleRunner`, independently testable.

---

## Dependency Graph

```
ai_trader.py (coordinator)
├── cycle_runner.py
│   ├── llm/parser.py              pure function
│   ├── llm_client.py              unchanged
│   ├── context/prompt_builder.py  delegates to:
│   │   ├── context/token_estimator.py
│   │   ├── context/sanitizer.py
│   │   ├── context/stats_formatter.py → db.py
│   │   └── context/pattern_engine.py  → files
│   ├── context/data_reader.py     → files + db.py
│   ├── ipc/bot_protocol.py        → files + db.py
│   ├── safety.py                  unchanged
│   └── db.py                      unchanged
├── ipc/bot_protocol.py
│   └── shared/ipc_utils
├── db.py                          unchanged
├── llm_client.py                  unchanged
└── safety.py                      unchanged
```

**No circular imports.** `llm/`, `context/`, `ipc/` never import `ai_trader.py`. They receive the reference at runtime via constructor.

---

## Test Strategy

### Pure functions (no mocks)

| Module | Functions | Est. Tests |
|--------|-----------|------------|
| `llm/parser.py` | `parse_decision_json` | 8-10 |
| `context/token_estimator.py` | `estimate_tokens` | 3-4 |
| `context/sanitizer.py` | `strip_injection_patterns`, `sanitize_reasoning` | 10-12 |
| `context/prompt_builder.py` | `_calc_roe` | 4-6 |

**~25-32 pure tests**

### Mock-based (db, file I/O, config)

| Module | Mock Targets | Est. Tests |
|--------|-------------|------------|
| `context/data_reader.py` | `safe_read_json`, `db.get_recently_traded_symbols`, file existence | 6-8 |
| `context/pattern_engine.py` | `Path.read_text`, `Path.write_text`, temp dirs | 8-10 |
| `context/stats_formatter.py` | `db.get_direction_stats`, `db.get_hold_time_stats`, `db.get_streak` | 4-6 |
| `context/prompt_builder.py` | `db.get_performance_stats`, `db.get_daily_pnl`, sub-managers | 4-6 |
| `ipc/bot_protocol.py` | `atomic_write`, `safe_read_json`, `Path` ops | 6-8 |
| `cycle_runner.py` | All managers mocked | 8-10 |

**~36-48 mock tests**

### Integration (pytest-asyncio)

| Scenario | What's Tested |
|----------|--------------|
| Hold cycle | Context → LLM mock → parse → safety → no send |
| Open cycle | Context → LLM mock → parse → safety → send → poll → log |
| Stale signals | mtime > 600s → skip |
| State unchanged | Same hash → skip |
| Emergency halt | Kill switch → close_all → poll confirmation |

**~5-8 integration tests**

### **Total: ~66-88 tests**

---

## Execution Steps (11 phases, each = 1 commit)

### Phase 1: Create folder structure
- Create `llm/`, `context/`, `ipc/` with `__init__.py`
- No code moves — scaffolding only
- **Risk:** None

### Phase 2: Extract pure functions
- `parse_decision_json()` → `llm/parser.py`
- `estimate_tokens()` + `MAX_PROMPT_TOKENS` → `context/token_estimator.py`
- Injection sanitizer → `context/sanitizer.py`
- Update imports in `ai_trader.py` and `context_builder.py`
- **Risk:** Low — pure functions, no state
- **Verify:** import check, existing tests still pass

### Phase 3: Extract data reader
- `read_signals()`, `_filter_traded_symbols()`, `read_positions()` → `context/data_reader.py`
- `DataReader(ai_trader)` class
- Remove from `ContextBuilder`, update call sites
- **Risk:** Medium — path resolution must be preserved exactly
- **Verify:** import check + read signals file test

### Phase 4: Extract pattern engine
- All pattern methods → `context/pattern_engine.py`
- `PatternEngine(ai_trader)` class
- **Risk:** Medium — file I/O paths must resolve correctly
- **Verify:** pattern read/write with temp dir

### Phase 5: Extract stats formatter
- `build_live_stats_section()`, `build_hold_regret_section()` → `context/stats_formatter.py`
- `StatsFormatter(ai_trader)` class
- **Risk:** Low — formatting + db reads
- **Verify:** import check

### Phase 6: Extract prompt builder
- `build_prompt()`, `_calc_roe()` → `context/prompt_builder.py`
- `PromptBuilder(ai_trader)` class
- Delete `context_builder.py` (now empty)
- **Risk:** Medium — prompt assembly is complex, token budget must work identically
- **Verify:** compare prompt output structure before/after

### Phase 7: Extract IPC protocol
- `_send_to_bot()` → `bot_protocol.py` as `send_decision()`
- `_check_bot_result()` → `bot_protocol.py` as `check_result()`
- `_emergency_halt()` → `bot_protocol.py` as `emergency_halt()`
- `BotProtocol(ai_trader)` class
- **Risk:** HIGH — IPC is timing-sensitive, ACK races
- **Verify:** mock-based ACK flow tests

### Phase 8: Extract cycle runner
- `execute_cycle()` → `cycle_runner.py` as `CycleRunner`
- Break into 7 sub-methods (see data flow diagram above)
- History/outcomes read directly from `self.ai_trader.db` (NOT through PromptBuilder)
- **Risk:** HIGH — core logic, every step must work identically
- **Verify:** integration test with mocked LLM

### Phase 9: Thin out ai_trader.py
- Remove all extracted methods
- Update `__init__` to create and wire managers
- Update `run_forever()` to call `self.cycle_runner.execute()` and `self.bot_ipc.emergency_halt()`
- Target: ~180 lines
- **Risk:** Medium — wiring correctness
- **Verify:** `wc -l ai_trader.py` < 200, all imports resolve, smoke test first cycle

### Phase 10: Write tests
- `tests/test_parser.py` — 8-10
- `tests/test_sanitizer.py` — 10-12
- `tests/test_token_estimator.py` — 3-4
- `tests/test_pattern_engine.py` — 8-10
- `tests/test_data_reader.py` — 6-8
- `tests/test_bot_protocol.py` — 6-8
- `tests/test_cycle_runner.py` — 8-10
- `tests/test_prompt_builder.py` — 8-12
- **Risk:** Low — tests only

### Phase 11: Update docs
- Update `docs/cheatsheet.md` file map
- Update `docs/autopilot-trader.md` architecture section
- **Risk:** None

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Path resolution breaks after split | Medium | High | Move path resolution into `DataReader.__init__()`, verify with config fixtures |
| IPC race conditions | Low | Critical | Extract as-is, don't change protocol logic |
| Prompt regression (different output) | Medium | High | Compare prompt sections byte-for-byte before/after |
| Emergency halt position loss | Low | Critical | Test with missing result file, verify DB fallback |
| Circular imports | None | High | Managers never import `ai_trader.py` — forbidden by design |
| State mutation in wrong place | Low | Medium | All cycle state stays on AITrader, managers read/write via `self.ai_trader.*` |

---

## Verification Checklist

**After each phase:**
- [ ] `python3 -c "from <new_module> import <Class>"` — no import errors
- [ ] `python3 -c "import ai_trader"` — no broken imports
- [ ] Existing tests still pass

**After phase 6 (context_builder deleted):**
- [ ] No references to `context_builder` remain anywhere

**After phase 9 (ai_trader thinned):**
- [ ] `ai_trader.py < 200 lines`
- [ ] `wc -l ai-decisions/*.py ai-decisions/*/*.py` — total ~2,400

**Final (after phase 11):**
- [ ] All 66-88 tests pass
- [ ] Daemon starts and completes first cycle clean
- [ ] Decision file written with correct format
- [ ] Bot receives and ACKs decision
- [ ] DB log entry has correct fields
- [ ] Emergency halt path works
- [ ] Dashboard shows AI trader status
