# Bot Test Suite Plan

> **Goal:** Create comprehensive test coverage for the modularized bot to ensure reliability and catch regressions before merging to main.
> **Framework:** pytest + pytest-asyncio (async support for LighterAPI, signal processing)
> **Location:** `bot/tests/` directory

---

## Current Module Structure

```
bot/
├── bot.py (318L) — Core coordinator + main()
├── config.py (147L) — BotConfig dataclass
├── dsl.py (existing) — DSL state machine
├── auth_helper.py (existing) — Auth token generation
├── api/
│   ├── lighter_api.py (596L) — DEX API wrapper
│   └── proxy_patch.py (68L) — SOCKS5 monkey-patch
├── core/
│   ├── models.py (67L) — TrackedPosition + BotState
│   ├── position_tracker.py (234L) — DSL + trailing TP/SL
│   ├── signal_processor.py (1013L) — AI decisions + signals
│   ├── state_manager.py (436L) — Save/load/reconcile
│   ├── order_manager.py (123L) — Quota/pacing/cache cleanup
│   └── execution_engine.py (576L) — Main tick + position processing
└── alerts/
    └── telegram.py (40L) — TelegramAlerter
```

---

## Test Architecture

### Directory Structure
```
bot/tests/
├── conftest.py — Shared fixtures + mocks
├── test_config.py — BotConfig tests
├── test_models.py — TrackedPosition + BotState tests
├── test_position_tracker.py — TP/SL calculations
├── test_order_manager.py — Quota + pacing logic
├── test_signal_processor.py — AI decision validation
├── test_state_manager.py — Save/load serialization
├── test_execution_engine.py — Tick orchestration
├── test_lighter_api.py — API wrapper (mocked)
├── test_telegram.py — Alert formatting
└── test_integration.py — Cross-module delegation
```

---

## Test Categories

### 1. Pure Logic Tests (NO mocks needed)

#### `test_config.py`
- `BotConfig.from_yaml()` — valid YAML, env var expansion, type coercion
- `BotConfig.validate()` — missing required fields, invalid ranges
- Default values — all 23 fields verified

#### `test_models.py`
- `TrackedPosition` creation with all field combinations
- `BotState` initialization — all default factories
- Field types and defaults match annotations

#### `test_position_tracker.py`
- `compute_tp_price()` — long/short, trigger not reached, high-water mark logic
- `compute_sl_price()` — long/short, trailing level vs entry-based
- `update_price()` — legacy mode: trailing activation, SL ratchet
- `update_price()` — DSL mode: tier evaluation, stagnation, floor locking
- `add_position()` — DSL state creation, effective leverage calculation
- `remove_position()` — existing vs non-existing market_id

#### `test_order_manager.py`
- `_prune_caches()` — expired cooldowns, stale data cleanup
- `_should_pace_orders()` — time-based rate limiting
- `_should_skip_open_for_quota()` — quota threshold logic
- `_is_quota_emergency()` — critical quota levels
- `_should_skip_non_critical_orders()` — combined quota checks

### 2. Mocked External Dependencies

#### `test_lighter_api.py` (mock lighter SDK)
- `_extract_quota_from_response()` — field extraction from various response types
- `_to_lighter_amount()` — decimal conversion math
- `_next_client_order_index()` — monotonic counter, overflow handling
- `_load_tracked_markets()` / `_save_tracked_markets()` — file I/O
- `_load_quota_cache()` / `_save_quota_cache()` — file I/O
- `get_positions()` — mock response parsing (positions, leverage, side)
- `get_price()` — retry logic, failure handling
- `update_mark_prices_from_positions()` — mark price derivation math

#### `test_signal_processor.py` (mock API + bot state)
- `_validate_ai_decision()` — valid/missing/invalid action fields
- `_resolve_market_id()` — signal-based lookup, cache logic
- `_log_outcome()` — win/loss calculation, equity tracking
- `_write_ai_result()` — file write, JSON serialization
- `_refresh_position_context()` — stale signal detection

#### `test_state_manager.py` (mock file I/O)
- `_serialize_dsl_state()` — all DSL fields, nested objects
- `_save_state()` — JSON serialization, file write
- `_load_state()` — valid JSON, missing file, corrupted file
- `_reconcile_positions()` — exchange vs saved state matching
- `_restore_dsl_state()` — DSL field restoration from dict

### 3. Integration Tests

#### `test_integration.py`
- **Delegation chain**: `bot._save_state()` → `state_manager._save_state()`
- **Manager initialization**: all 4 managers created with correct dependencies
- **Signal → State → Execution**: end-to-end signal processing flow
- **Tick orchestration**: `_tick()` calls signal_processor, state_manager, order_manager
- **State persistence**: save → restart → load cycle

### 4. Edge Case Tests

#### Critical Business Logic
- **Quota depletion**: signal_processor should skip opens when quota < threshold
- **DSL tier locking**: floor lock calculation with various leverage values
- **Position sync race**: `_pending_sync` prevents premature reconciliation
- **Kill switch**: execution_engine respects kill switch file existence
- **Duplicate signal**: hash-based dedup prevents re-processing
- **AI close cooldown**: 5-minute cooldown after AI-initiated close

---

## Implementation Plan

### Phase 1: Setup (1 session)
1. Create `bot/tests/` directory with `__init__.py`
2. Create `conftest.py` with shared fixtures:
   - `mock_config` — BotConfig with test values
   - `mock_state` — BotState with initialized fields
   - `mock_tracker` — PositionTracker with test config
   - `mock_alerter` — TelegramAlerter mock (no network)
   - `mock_bot` — LighterCopilot mock with all state attributes
   - `tmp_state_dir` — temporary directory for file I/O tests
3. Add pytest configuration to `pyproject.toml` or `pytest.ini`
4. Install test dependencies: `pip install pytest pytest-asyncio pytest-mock`

### Phase 2: Pure Logic Tests (1 session)
1. `test_config.py` — 10-15 tests (YAML loading, validation, defaults)
2. `test_models.py` — 5-10 tests (dataclass creation, field types)
3. `test_position_tracker.py` — 20-25 tests (TP/SL calculations, DSL evaluation)
4. `test_order_manager.py` — 10-15 tests (quota logic, cache pruning)

### Phase 3: Mocked Tests (1 session)
1. `test_lighter_api.py` — 15-20 tests (response parsing, decimal math)
2. `test_signal_processor.py` — 15-20 tests (validation, state transitions)
3. `test_state_manager.py` — 10-15 tests (serialization, reconciliation)
4. `test_telegram.py` — 5 tests (message formatting, rate limit handling)

### Phase 4: Integration + Edge Cases (1 session)
1. `test_integration.py` — 10-15 tests (delegation chains, end-to-end flows)
2. Edge case tests embedded in module tests
3. Run full suite, fix any failures
4. Add CI configuration if applicable

---

## Test Fixtures (conftest.py)

```python
@pytest.fixture
def mock_config():
    """BotConfig with test-safe defaults."""
    return BotConfig(
        lighter_url="https://test.lighter.xyz",
        account_index=1,
        api_key_index=0,
        api_key_private="test_key",
        signals_file="/tmp/test_signals.json",
        ai_decision_file="/tmp/test_ai_decision.json",
        ai_result_file="/tmp/test_ai_result.json",
        telegram_token="test_token",
        telegram_chat_id="test_chat",
    )

@pytest.fixture
def mock_tracker(mock_config):
    """PositionTracker with test config."""
    return PositionTracker(mock_config)

@pytest.fixture
def mock_state():
    """BotState with all fields initialized."""
    return BotState(
        opened_signals={},
        recently_closed={},
        close_attempts={},
        # ... all fields
    )

@pytest.fixture
def mock_alerter():
    """TelegramAlerter that doesn't send real messages."""
    alerter = TelegramAlerter("", "")
    return alerter

@pytest.fixture
def mock_api(mock_config):
    """LighterAPI with mocked lighter SDK."""
    # Mock lighter module
    with mock.patch.dict('sys.modules', {'lighter': mock.MagicMock()}):
        from api.lighter_api import LighterAPI
        api = LighterAPI(mock_config)
    return api

@pytest.fixture
def mock_bot(mock_config, mock_tracker, mock_alerter):
    """LighterCopilot mock with all state attributes."""
    # Create a simple mock with all required attributes
    bot = mock.MagicMock()
    bot.cfg = mock_config
    bot.tracker = mock_tracker
    bot.alerter = mock_alerter
    bot.api = mock.MagicMock()
    # Add all state attributes from bot.py __init__
    bot._last_signal_hash = None
    bot._last_signal_timestamp = None
    bot._opened_signals = set()
    # ... etc for all 34 state attributes
    return bot

@pytest.fixture
def tmp_state_dir(tmp_path):
    """Temporary directory for state file tests."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir
```

---

## Expected Coverage

| Module | Target Coverage | Priority |
|--------|----------------|----------|
| config.py | 95%+ | High |
| core/models.py | 100% | High |
| core/position_tracker.py | 90%+ | Critical |
| core/order_manager.py | 85%+ | High |
| core/signal_processor.py | 70%+ | Medium (complex async) |
| core/state_manager.py | 85%+ | High |
| core/execution_engine.py | 60%+ | Medium (orchestration) |
| api/lighter_api.py | 75%+ | Medium (external deps) |
| alerts/telegram.py | 90%+ | High |
| bot.py | 50%+ | Low (integration tested) |

**Overall target: 75%+ line coverage**

---

## Running Tests

```bash
cd bot/
source venv/bin/activate
pip install pytest pytest-asyncio pytest-mock
pytest tests/ -v --tb=short
pytest tests/ --cov=. --cov-report=html  # Coverage report
pytest tests/test_position_tracker.py -v  # Single module
```

---

## Notes

- **No live API calls**: all tests use mocks for Lighter SDK, Telegram, file I/O
- **No state contamination**: each test gets fresh fixtures via pytest
- **Async tests**: use `@pytest.mark.asyncio` for async method tests
- **File I/O**: use `tmp_path` fixture for state file tests
- **DSL tests**: most critical — tier evaluation, floor locking, stagnation timers
- **Signal processing**: focus on validation and state transitions, not LLM calls

---

## Success Criteria

✅ All tests pass with `pytest tests/ -v`
✅ 75%+ line coverage across all modules
✅ No external API calls or network access
✅ Tests run in < 30 seconds
✅ Clear test names that document expected behavior
✅ Fixtures are reusable and well-documented
