"""
Shared fixtures for bot test suite.
"""

import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

# Add bot/ to path so imports work from tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BotConfig
from dsl import DSLConfig, DSLState, DSLTier
from core.models import TrackedPosition, BotState


# ── Config ──────────────────────────────────────────────────────────

@pytest.fixture
def config():
    """BotConfig with test-safe defaults."""
    return BotConfig(
        lighter_url="https://test.lighter.xyz",
        account_index=1,
        api_key_index=0,
        api_key_private="test_key_private",
        telegram_token="",
        telegram_chat_id="",
        signals_file="/tmp/test_signals.json",
        ai_decision_file="/tmp/test_ai_decision.json",
        ai_result_file="/tmp/test_ai_result.json",
        ai_trader_dir="/tmp/test_ai_trader",
        ai_mode=True,
        hard_sl_pct=1.25,
        max_risk_pct=0.02,
        max_margin_pct=0.15,
        min_risk_reward=1.5,
        max_concurrent_signals=3,
        dsl_leverage=10.0,
        trailing_tp_trigger_pct=3.0,
        trailing_tp_delta_pct=1.0,
        dsl_enabled=True,
        stagnation_roe_pct=8.0,
        stagnation_minutes=60,
        price_poll_interval=5,
        price_call_delay=5.0,
        track_manual_positions=False,
        dsl_tiers=[
            {"trigger_pct": 7, "lock_hw_pct": 40, "trailing_buffer_roe": 5, "consecutive_breaches": 3},
            {"trigger_pct": 12, "lock_hw_pct": 55, "trailing_buffer_roe": 4, "consecutive_breaches": 2},
            {"trigger_pct": 15, "lock_hw_pct": 75, "trailing_buffer_roe": 3, "consecutive_breaches": 2},
            {"trigger_pct": 20, "lock_hw_pct": 85, "trailing_buffer_roe": 2, "consecutive_breaches": 2},
        ],
    )


@pytest.fixture
def config_no_dsl(config):
    """BotConfig with DSL disabled (legacy trailing mode)."""
    config.dsl_enabled = False
    config.dsl_tiers = []
    return config


# ── DSL ─────────────────────────────────────────────────────────────

@pytest.fixture
def dsl_config():
    """Default DSLConfig matching the default tiers in dsl.py."""
    return DSLConfig(
        tiers=[
            DSLTier(trigger_pct=3,  lock_hw_pct=30, trailing_buffer_roe=6, consecutive_breaches=3),
            DSLTier(trigger_pct=7,  lock_hw_pct=40, trailing_buffer_roe=5, consecutive_breaches=3),
            DSLTier(trigger_pct=12, lock_hw_pct=55, trailing_buffer_roe=4, consecutive_breaches=2),
            DSLTier(trigger_pct=15, lock_hw_pct=75, trailing_buffer_roe=3, consecutive_breaches=2),
            DSLTier(trigger_pct=20, lock_hw_pct=85, trailing_buffer_roe=2, consecutive_breaches=2),
            DSLTier(trigger_pct=30, lock_hw_pct=90, trailing_buffer_roe=1, consecutive_breaches=2),
        ],
        stagnation_roe_pct=8.0,
        stagnation_minutes=60,
        hard_sl_pct=1.25,
    )


@pytest.fixture
def dsl_state_long():
    """DSLState for a long position at $100, 10x leverage."""
    return DSLState(
        side="long",
        entry_price=100.0,
        leverage=10.0,
        high_water_price=100.0,
        high_water_time=datetime.now(timezone.utc),
    )


@pytest.fixture
def dsl_state_short():
    """DSLState for a short position at $100, 10x leverage."""
    return DSLState(
        side="short",
        entry_price=100.0,
        leverage=10.0,
        high_water_price=100.0,
        high_water_time=datetime.now(timezone.utc),
    )


# ── Models ──────────────────────────────────────────────────────────

@pytest.fixture
def tracked_position_long():
    """TrackedPosition: long BTC at $50k, size 0.1."""
    return TrackedPosition(
        market_id=1,
        symbol="BTC",
        side="long",
        entry_price=50000.0,
        size=0.1,
        high_water_mark=50000.0,
    )


@pytest.fixture
def tracked_position_short():
    """TrackedPosition: short ETH at $3000, size 1.0."""
    return TrackedPosition(
        market_id=2,
        symbol="ETH",
        side="short",
        entry_price=3000.0,
        size=1.0,
        high_water_mark=3000.0,
    )


@pytest.fixture
def bot_state():
    """BotState with all defaults."""
    return BotState()


# ── Mocks ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_api():
    """Mock LighterAPI with common attributes."""
    api = MagicMock()
    api.volume_quota_remaining = 100
    api._symbol_cache = {}
    api._symbol_cache_ttl = 300
    api.get_positions = AsyncMock(return_value=[])
    api.get_price = AsyncMock(return_value=50000.0)
    api._ensure_client = AsyncMock()
    api._ensure_signer = AsyncMock()
    api.close = AsyncMock()
    return api


@pytest.fixture
def mock_alerter():
    """Mock TelegramAlerter (no network)."""
    alerter = MagicMock()
    alerter.enabled = False
    alerter.send = AsyncMock()
    return alerter


@pytest.fixture
def mock_bot(config, mock_api, mock_alerter):
    """Mock LighterCopilot with all state attributes from bot.py __init__."""
    from core.position_tracker import PositionTracker
    from core.order_manager import OrderManager

    bot = MagicMock()
    bot.cfg = config
    bot.api = mock_api
    bot.tracker = PositionTracker(config)
    bot.alerter = mock_alerter

    # All state attributes from LighterCopilot.__init__
    bot._signals_file = config.signals_file
    bot._last_signal_timestamp = None
    bot._last_signal_hash = None
    bot._opened_signals = set()
    bot._min_score = 60
    bot._signal_processed_this_tick = False
    bot._ai_mode = config.ai_mode
    bot._ai_decision_file = config.ai_decision_file
    bot._ai_result_file = config.ai_result_file
    bot._last_ai_decision_ts = None
    bot._ai_close_cooldown = {}
    bot._ai_cooldown_seconds = 300
    bot._api_lag_warnings = {}
    bot._pending_sync = set()
    bot._recently_closed = {}
    bot._verifying_close = set()
    bot._close_attempts = {}
    bot._close_attempt_cooldown = {}
    bot._max_close_attempts = 3
    bot._close_cooldown_seconds = 900
    bot._close_verify_delay = 5.0
    bot._close_verify_retries = 4
    bot._dsl_close_attempts = {}
    bot._dsl_close_attempt_cooldown = {}
    bot._sl_retry_delays = [15, 60, 300, 900]
    bot._last_order_time = 0.0
    bot._last_quota_alert_time = 0.0
    bot._quota_alert_interval = 3600
    bot._last_quota_emergency_warn = 0.0
    bot._kill_switch_active = False
    bot._kill_switch_path = Path("/tmp/test_kill_switch")
    bot._saved_positions = None
    bot.bot_managed_market_ids = set()
    bot._no_price_ticks = {}
    bot._no_price_alert_threshold = 3
    bot._position_sync_failures = 0
    bot._position_sync_failure_threshold = 3
    bot._idle_tick_count = 0
    bot._idle_threshold = 2
    bot._idle_sleep_interval = 60
    bot._result_dirty = False
    bot.running = True

    return bot


@pytest.fixture
def tmp_state_dir(tmp_path):
    """Temporary directory for state file tests."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir
