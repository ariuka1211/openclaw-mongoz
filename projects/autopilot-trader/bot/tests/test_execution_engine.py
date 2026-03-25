"""
Tests for core.execution_engine.ExecutionEngine
"""

import sys
import time
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
# Add shared/ to path for ipc_utils (safe_read_json used by signal_processor, state_manager)
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))

from core.execution_engine import ExecutionEngine
from core.position_tracker import PositionTracker
from core.order_manager import OrderManager
from core.models import TrackedPosition
from dsl import DSLState
from datetime import datetime, timezone


# ── Helpers ─────────────────────────────────────────────────────────

def _make_engine(config, mock_api, mock_alerter, mock_bot):
    """Create ExecutionEngine with real tracker, mock everything else.

    OrderManager accesses self.tracker and self.bot_managed_market_ids
    directly (not via self.bot), so we set those on the real OM.
    Also sets required mock_api attributes to avoid MagicMock comparison errors.
    """
    # Set mock_api attributes used by _tick — use real comparison values
    mock_api._last_quota_time = 0
    mock_api._last_known_quota = None
    if not isinstance(mock_api.volume_quota_remaining, int):
        mock_api.volume_quota_remaining = 100
    # Mock mark_price methods to return real numbers
    mock_api.get_mark_price = MagicMock(return_value=0.0)
    mock_api.update_mark_prices_from_positions = MagicMock()

    if not hasattr(mock_bot, 'bot_managed_market_ids') or mock_bot.bot_managed_market_ids is None:
        mock_bot.bot_managed_market_ids = set()
    if not hasattr(mock_bot, '_position_sync_failures'):
        mock_bot._position_sync_failures = 0
    if not hasattr(mock_bot, '_position_sync_failure_threshold'):
        mock_bot._position_sync_failure_threshold = 3

    tracker = PositionTracker(config)
    mock_bot.tracker = tracker

    om = OrderManager(config, mock_api, mock_bot)
    om.tracker = tracker
    om.bot_managed_market_ids = mock_bot.bot_managed_market_ids
    mock_bot.order_manager = om

    mock_bot.state_manager = MagicMock()
    mock_bot.state_manager._save_state = MagicMock()
    mock_bot.state_manager._reconcile_positions = AsyncMock(return_value=None)

    # Create a proper mock signal processor with AsyncMock methods
    sp = MagicMock()
    sp._process_signals = AsyncMock()
    sp._process_ai_decision = AsyncMock()
    sp._refresh_position_context = MagicMock()
    sp._log_outcome = MagicMock()
    sp._verify_position_closed = AsyncMock(return_value=True)
    sp._get_fill_price = AsyncMock(return_value=50000.0)
    mock_bot.signal_processor = sp

    engine = ExecutionEngine(config, mock_api, tracker, mock_alerter, mock_bot)
    engine._ai_mode = False
    engine._result_dirty = False
    engine._last_quota_alert_time = time.time() + 3600
    engine._quota_alert_interval = 3600
    engine._ai_close_cooldown = {}
    engine._api_lag_warnings = {}
    return engine, tracker


# ── Kill switch tests ───────────────────────────────────────────────

class TestKillSwitch:
    """Tests for kill switch check at the top of _tick()."""

    @pytest.mark.asyncio
    async def test_kill_switch_activation_file_exists_sets_flag_and_alerts(self, config, mock_api, mock_alerter, mock_bot, tmp_path):
        """Kill switch file exists → _kill_switch_active=True, alert sent."""
        kill_path = tmp_path / "kill"
        kill_path.touch()
        mock_bot._kill_switch_path = kill_path
        mock_bot._kill_switch_active = False

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        mock_api.get_positions = AsyncMock(return_value=[])

        await engine._tick()

        assert mock_bot._kill_switch_active is True
        mock_alerter.send.assert_awaited()
        assert "KILL SWITCH ACTIVE" in mock_alerter.send.await_args[0][0]

    @pytest.mark.asyncio
    async def test_kill_switch_deactivation_file_removed_resets_flag_and_alerts(self, config, mock_api, mock_alerter, mock_bot, tmp_path):
        """Kill switch file removed → _kill_switch_active=False, alert sent."""
        kill_path = tmp_path / "kill"
        kill_path.touch()
        mock_bot._kill_switch_path = kill_path
        mock_bot._kill_switch_active = True

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        mock_api.get_positions = AsyncMock(return_value=[])

        kill_path.unlink()
        await engine._tick()

        assert mock_bot._kill_switch_active is False
        mock_alerter.send.assert_awaited()
        assert "deactivated" in mock_alerter.send.await_args[0][0]


# ── Position sync failure tests ─────────────────────────────────────

class TestPositionSync:
    """Tests for position sync edge cases in _tick()."""

    @pytest.mark.asyncio
    async def test_position_sync_failure_none_increments_counter(self, config, mock_api, mock_alerter, mock_bot):
        """get_positions returns None → _position_sync_failures increments."""
        mock_api.get_positions = AsyncMock(return_value=None)
        mock_bot._position_sync_failures = 0

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert mock_bot._position_sync_failures == 1

    @pytest.mark.asyncio
    async def test_position_sync_recovery_list_resets_counter(self, config, mock_api, mock_alerter, mock_bot):
        """get_positions returns list → failures reset to 0."""
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_bot._position_sync_failures = 2

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert mock_bot._position_sync_failures == 0

    @pytest.mark.asyncio
    async def test_position_sync_failure_alert_at_threshold(self, config, mock_api, mock_alerter, mock_bot):
        """Failures reaching threshold → alert sent."""
        mock_api.get_positions = AsyncMock(return_value=None)
        mock_bot._position_sync_failures = 2
        mock_bot._position_sync_failure_threshold = 3

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert mock_bot._position_sync_failures == 3
        mock_alerter.send.assert_awaited()
        assert "Position Sync Failed" in mock_alerter.send.await_args[0][0]


# ── Idle tick tests ─────────────────────────────────────────────────

class TestIdleTick:
    """Tests for idle tick tracking at end of _tick()."""

    @pytest.mark.asyncio
    async def test_idle_tick_counting_no_positions_no_signals_increments(self, config, mock_api, mock_alerter, mock_bot):
        """No positions + no signals → _idle_tick_count increments."""
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_bot._idle_tick_count = 0
        mock_bot._signal_processed_this_tick = False

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert mock_bot._idle_tick_count == 1

    @pytest.mark.asyncio
    async def test_idle_tick_reset_positions_exist_resets_to_zero(self, config, mock_api, mock_alerter, mock_bot):
        """Positions exist → _idle_tick_count resets to 0."""
        # Must include position in live_positions so it's not detected as "exchange closed"
        mock_api.get_positions = AsyncMock(return_value=[
            {"market_id": 1, "symbol": "BTC", "side": "long", "entry_price": 50000.0, "size": 0.1, "leverage": 10.0},
        ])
        mock_bot._idle_tick_count = 5
        mock_bot._signal_processed_this_tick = False

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        await engine._tick()

        assert mock_bot._idle_tick_count == 0

    @pytest.mark.asyncio
    async def test_idle_tick_reset_signal_processed_resets_to_zero(self, config, mock_api, mock_alerter, mock_bot):
        """Signal processed this tick → _idle_tick_count resets to 0."""
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_bot._idle_tick_count = 3
        mock_bot._signal_processed_this_tick = True

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert mock_bot._idle_tick_count == 0


# ── API not initialized test ────────────────────────────────────────

class TestApiNotInitialized:
    """Tests for early return when api is falsy."""

    @pytest.mark.asyncio
    async def test_api_not_initialized_tick_returns_early_no_crash(self, config, mock_alerter, mock_bot):
        """api is None → _tick returns early without crash."""
        mock_bot._kill_switch_path = Path("/tmp/nonexistent_kill_switch")
        mock_bot._kill_switch_active = False
        mock_bot._idle_tick_count = 0
        mock_bot.bot_managed_market_ids = set()

        tracker = PositionTracker(config)
        mock_bot.tracker = tracker
        mock_bot.order_manager = MagicMock()
        mock_bot.order_manager._prune_caches = MagicMock()
        mock_bot.state_manager = MagicMock()

        engine = ExecutionEngine(config, None, tracker, mock_alerter, mock_bot)
        engine._ai_mode = False
        engine._last_quota_alert_time = time.time()
        engine._quota_alert_interval = 3600

        await engine._tick()

        mock_bot.order_manager._prune_caches.assert_called_once()


# ── Position processing tests ───────────────────────────────────────

class TestPositionProcessing:
    """Tests for _process_position_tick logic."""

    @pytest.mark.asyncio
    async def test_process_position_tick_price_drop_triggers_stop_loss_close(self, config, mock_api, mock_alerter, mock_bot):
        """Price drops below SL → close order attempted."""
        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        mock_api.get_price_with_mark_fallback = AsyncMock(return_value=49000.0)
        mock_api.execute_sl = AsyncMock(return_value=(True, "order123"))
        mock_bot.signal_processor._verify_position_closed = AsyncMock(return_value=True)
        mock_bot.signal_processor._get_fill_price = AsyncMock(return_value=49000.0)

        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)

        pos = tracker.positions[1]
        await engine._process_position_tick(1, pos)

        mock_api.execute_sl.assert_called_once()
        assert 1 not in tracker.positions

    @pytest.mark.asyncio
    async def test_process_position_tick_unverified_position_skips_evaluation(self, config, mock_api, mock_alerter, mock_bot):
        """Unverified position → evaluation skipped, no price fetch."""
        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        mock_api.get_price_with_mark_fallback = AsyncMock()

        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            unverified_at=time.time(), unverified_ticks=1,
        )
        tracker.positions[1] = pos

        await engine._process_position_tick(1, pos)

        mock_api.get_price_with_mark_fallback.assert_not_called()


# ── Pending sync skip test ──────────────────────────────────────────

class TestPendingSync:
    """Tests for _pending_sync skip during live position sync."""

    @pytest.mark.asyncio
    async def test_pending_sync_positions_skipped_during_live_sync(self, config, mock_api, mock_alerter, mock_bot):
        """Positions in _pending_sync are skipped during position detection."""
        mock_api.get_positions = AsyncMock(return_value=[
            {"market_id": 1, "symbol": "BTC", "side": "long", "entry_price": 50000.0, "size": 0.1, "leverage": 10.0},
        ])
        mock_bot._pending_sync = {1}
        mock_bot._position_sync_failures = 0

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert 1 not in tracker.positions

    @pytest.mark.asyncio
    async def test_pending_sync_cleared_after_verification_section(self, config, mock_api, mock_alerter, mock_bot):
        """_pending_sync is cleared after position verification section (HIGH-13)."""
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_bot._pending_sync = {99}

        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        await engine._tick()

        assert len(mock_bot._pending_sync) == 0


# ── Cache pruning test ──────────────────────────────────────────────

class TestCachePruning:
    """Tests that _prune_caches is called each tick."""

    @pytest.mark.asyncio
    async def test_tick_calls_prune_caches_each_cycle(self, config, mock_api, mock_alerter, mock_bot):
        """Each tick calls order_manager._prune_caches()."""
        mock_api.get_positions = AsyncMock(return_value=[])
        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)

        original_prune = mock_bot.order_manager._prune_caches
        prune_calls = []
        def counting_prune():
            prune_calls.append(1)
            original_prune()
        mock_bot.order_manager._prune_caches = counting_prune

        await engine._tick()

        assert len(prune_calls) == 1


# ── State persistence test ──────────────────────────────────────────

class TestStatePersistence:
    """Tests that state is saved at end of tick."""

    @pytest.mark.asyncio
    async def test_tick_saves_state_after_processing(self, config, mock_api, mock_alerter, mock_bot):
        """_tick calls state_manager._save_state() after position processing."""
        mock_api.get_positions = AsyncMock(return_value=[])
        engine, tracker = _make_engine(config, mock_api, mock_alerter, mock_bot)
        mock_bot.state_manager._save_state.reset_mock()

        await engine._tick()

        mock_bot.state_manager._save_state.assert_called_once()
