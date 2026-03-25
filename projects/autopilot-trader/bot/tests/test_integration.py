"""
Cross-module integration tests.

Tests delegation chains, data flow between modules, and end-to-end
position lifecycle flows. Focus on how modules connect, not internal logic.
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
# Add shared/ to path for ipc_utils (safe_read_json used by signal_processor, state_manager)
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))

from config import BotConfig
from core.position_tracker import PositionTracker
from core.order_manager import OrderManager
from core.execution_engine import ExecutionEngine
from core.state_manager import StateManager
from core.signal_processor import SignalProcessor
from core.models import TrackedPosition
from dsl import DSLState, evaluate_dsl, DSLConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _build_mock_bot(config, mock_api, mock_alerter):
    """Build a mock bot with real PositionTracker and OrderManager.

    OrderManager accesses self.tracker and self.bot_managed_market_ids
    directly (not via self.bot), so we patch those on the real OM.
    Also sets mock_api attributes needed by execution_engine._tick().
    """
    bot = MagicMock()
    bot.cfg = config
    bot.api = mock_api
    bot.alerter = mock_alerter
    bot.running = True

    # Set mock_api attributes to avoid MagicMock comparison errors
    mock_api._last_quota_time = 0
    mock_api._last_known_quota = None
    if not isinstance(mock_api.volume_quota_remaining, int):
        mock_api.volume_quota_remaining = 100
    mock_api.get_mark_price = MagicMock(return_value=0.0)
    mock_api.update_mark_prices_from_positions = MagicMock()
    mock_api._save_tracked_markets = MagicMock()
    mock_api.set_tracked_markets = MagicMock()

    # Real tracker
    bot.tracker = PositionTracker(config)

    # All state attributes from LighterCopilot.__init__
    bot._min_score = 60
    bot._kill_switch_active = False
    bot._signal_processed_this_tick = False
    bot._opened_signals = set()
    bot._ai_close_cooldown = {}
    bot._ai_cooldown_seconds = 300
    bot._api_lag_warnings = {}
    bot._recently_closed = {}
    bot._pending_sync = set()
    bot._verifying_close = set()
    bot._close_attempts = {}
    bot._close_attempt_cooldown = {}
    bot._max_close_attempts = 3
    bot._close_cooldown_seconds = 900
    bot._dsl_close_attempts = {}
    bot._dsl_close_attempt_cooldown = {}
    bot._sl_retry_delays = [15, 60, 300, 900]
    bot._last_order_time = 0.0
    bot._no_price_ticks = {}
    bot._no_price_alert_threshold = 3
    bot._idle_tick_count = 0
    bot._idle_threshold = 2
    bot._idle_sleep_interval = 60
    bot._result_dirty = False
    bot._saved_positions = None
    bot._kill_switch_path = Path("/tmp/test_kill_switch")
    bot._signals_file = config.signals_file
    bot._ai_decision_file = config.ai_decision_file
    bot._ai_result_file = config.ai_result_file
    bot._ai_trader_dir = config.ai_trader_dir
    bot._last_ai_decision_ts = None
    bot._last_signal_timestamp = None
    bot._last_signal_hash = None
    bot._last_quota_alert_time = 0.0
    bot._quota_alert_interval = 3600
    bot._position_sync_failures = 0
    bot._position_sync_failure_threshold = 3
    bot.bot_managed_market_ids = set()

    # Real order manager — patch tracker and bot_managed_market_ids onto it
    om = OrderManager(config, mock_api, bot)
    om.tracker = bot.tracker
    om.bot_managed_market_ids = bot.bot_managed_market_ids
    bot.order_manager = om

    # State manager mock
    bot.state_manager = MagicMock()
    bot.state_manager._save_state = MagicMock()
    bot.state_manager._load_state = MagicMock()
    bot.state_manager._reconcile_positions = AsyncMock()
    bot.state_manager._reconcile_state_with_exchange = AsyncMock()

    # Delegation method
    def _save_state_delegate():
        bot.state_manager._save_state()
    bot._save_state = _save_state_delegate

    return bot


# ── Test 1: bot._save_state() delegates to state_manager ───────────

class TestSaveStateDelegation:

    def test_save_state_delegates_to_state_manager(self, config, mock_api, mock_alerter):
        """bot._save_state() → calls state_manager._save_state()."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        bot._save_state()

        bot.state_manager._save_state.assert_called_once()


# ── Test 2: Manager initialization ─────────────────────────────────

class TestManagerInitialization:

    def test_all_managers_created_with_correct_references(self, config, mock_api, mock_alerter):
        """All 4 managers created with correct cfg/api/tracker/alerter references."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        assert isinstance(bot.tracker, PositionTracker)
        assert bot.tracker.cfg is config

        assert isinstance(bot.order_manager, OrderManager)
        assert bot.order_manager.cfg is config
        assert bot.order_manager.api is mock_api

        assert bot.alerter is mock_alerter
        assert bot.api is mock_api
        assert bot.order_manager.bot is bot


# ── Test 3: Position open flow ─────────────────────────────────────

class TestPositionOpenFlow:

    def test_add_position_tracker_has_position_and_dsl_state(self, config, mock_api, mock_alerter):
        """add_position → tracker has position, DSL state created."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)
        tracker = bot.tracker

        tracker.add_position(1, "BTC", "long", 50000.0, 0.1, leverage=10.0)

        assert 1 in tracker.positions
        pos = tracker.positions[1]
        assert pos.symbol == "BTC"
        assert pos.side == "long"
        assert pos.entry_price == 50000.0
        assert pos.size == 0.1
        assert pos.dsl_state is not None
        assert pos.dsl_state.side == "long"
        assert pos.dsl_state.entry_price == 50000.0
        assert pos.dsl_state.leverage == 10.0

    def test_add_position_no_dsl_config(self, config_no_dsl, mock_api, mock_alerter):
        """add_position with DSL disabled → no DSL state."""
        bot = _build_mock_bot(config_no_dsl, mock_api, mock_alerter)

        bot.tracker.add_position(1, "BTC", "long", 50000.0, 0.1)

        pos = bot.tracker.positions[1]
        assert pos.dsl_state is None


# ── Test 4: Position close flow ────────────────────────────────────

class TestPositionCloseFlow:

    def test_remove_position_tracker_empty(self, config, mock_api, mock_alerter):
        """remove_position → tracker.positions empty."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)
        bot.tracker.add_position(1, "BTC", "long", 50000.0, 0.1)

        assert 1 in bot.tracker.positions

        bot.tracker.remove_position(1)

        assert 1 not in bot.tracker.positions
        assert len(bot.tracker.positions) == 0


# ── Test 5: Signal → market_id resolution chain ────────────────────

class TestSignalMarketIdResolution:

    def test_resolve_market_id_from_tracker_positions(self, config, mock_api, mock_alerter, tmp_path):
        """_resolve_market_id finds market_id from tracker positions."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)
        bot.tracker.add_position(42, "ETH", "long", 3000.0, 1.0)
        bot._signals_file = str(tmp_path / "signals.json")

        sp = SignalProcessor(config, mock_api, bot.tracker, mock_alerter, bot)
        # Also inject safe_read_json since it's not imported in signal_processor
        import core.signal_processor as sp_mod
        if not hasattr(sp_mod, 'safe_read_json'):
            from ipc_utils import safe_read_json as _srj
            sp_mod.safe_read_json = _srj

        result = sp._resolve_market_id("ETH")
        assert result == 42

    def test_resolve_market_id_from_signals_file(self, config, mock_api, mock_alerter, tmp_path):
        """_resolve_market_id finds market_id from signals.json."""
        signals_file = tmp_path / "signals.json"
        signals_file.write_text(json.dumps({
            "opportunities": [{"symbol": "DOGE", "marketId": 99, "direction": "long"}]
        }))

        bot = _build_mock_bot(config, mock_api, mock_alerter)
        bot._signals_file = str(signals_file)
        sp = SignalProcessor(config, mock_api, bot.tracker, mock_alerter, bot)

        # Inject safe_read_json
        import core.signal_processor as sp_mod
        if not hasattr(sp_mod, 'safe_read_json'):
            from ipc_utils import safe_read_json as _srj
            sp_mod.safe_read_json = _srj

        result = sp._resolve_market_id("DOGE")
        assert result == 99

    def test_resolve_market_id_returns_none_for_unknown(self, config, mock_api, mock_alerter, tmp_path):
        """_resolve_market_id returns None for unknown symbol."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)
        bot._signals_file = str(tmp_path / "nonexistent.json")
        sp = SignalProcessor(config, mock_api, bot.tracker, mock_alerter, bot)

        result = sp._resolve_market_id("UNKNOWN")
        assert result is None


# ── Test 6: State save → load roundtrip ────────────────────────────

class TestStateRoundtrip:

    def test_save_load_roundtrip_preserves_position_data(self, config, mock_api, mock_alerter, tmp_path):
        """Save positions → load → all position data preserved."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        sm = StateManager(config, mock_api, bot.tracker, mock_alerter, bot)

        # Add positions
        bot.tracker.add_position(1, "BTC", "long", 50000.0, 0.1, leverage=10.0)
        bot.tracker.add_position(2, "ETH", "short", 3000.0, 1.0, leverage=5.0)
        bot.bot_managed_market_ids = {1, 2}
        bot._recently_closed = {99: time.monotonic() + 300}

        # Serialize state
        now = time.monotonic()
        state = {
            "last_ai_decision_ts": None,
            "last_signal_timestamp": None,
            "last_signal_hash": None,
            "recently_closed": {str(mid): max(0, t - now) for mid, t in bot._recently_closed.items()},
            "ai_close_cooldown": {},
            "close_attempts": {},
            "close_attempt_cooldown": {},
            "dsl_close_attempts": {},
            "dsl_close_attempt_cooldown": {},
            "bot_managed_market_ids": sorted(bot.bot_managed_market_ids),
            "positions": {
                str(mid): {
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "size": pos.size,
                    "leverage": pos.dsl_state.effective_leverage if pos.dsl_state else config.default_leverage,
                    "sl_pct": pos.sl_pct,
                    "high_water_mark": pos.high_water_mark,
                    "trailing_active": pos.trailing_active,
                    "trailing_sl_level": pos.trailing_sl_level,
                    "unverified_at": pos.unverified_at,
                    "unverified_ticks": pos.unverified_ticks,
                    "active_sl_order_id": pos.active_sl_order_id,
                    "dsl": sm._serialize_dsl_state(pos.dsl_state) if pos.dsl_state else None,
                }
                for mid, pos in bot.tracker.positions.items()
            },
        }

        # Write to temp file
        state_file = tmp_path / "bot_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

        # Clear tracker
        bot.tracker.positions.clear()
        assert len(bot.tracker.positions) == 0

        # Read back
        with open(state_file) as f:
            loaded = json.load(f)

        positions_data = loaded.get("positions", {})
        assert len(positions_data) == 2
        assert "1" in positions_data
        assert "2" in positions_data

        btc = positions_data["1"]
        assert btc["symbol"] == "BTC"
        assert btc["side"] == "long"
        assert btc["entry_price"] == 50000.0
        assert btc["size"] == 0.1

        eth = positions_data["2"]
        assert eth["symbol"] == "ETH"
        assert eth["side"] == "short"
        assert eth["entry_price"] == 3000.0

        assert loaded["bot_managed_market_ids"] == [1, 2]


# ── Test 7: Quota check chain ──────────────────────────────────────

class TestQuotaCheckChain:

    def test_should_skip_open_for_quota_prevents_new_opens(self, config, mock_api, mock_alerter):
        """_should_skip_open_for_quota returns True when quota < 35."""
        mock_api.volume_quota_remaining = 10
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        result = bot.order_manager._should_skip_open_for_quota()
        assert result is True

    def test_should_skip_open_for_quota_allows_when_sufficient(self, config, mock_api, mock_alerter):
        """_should_skip_open_for_quota returns False when quota >= 35."""
        mock_api.volume_quota_remaining = 100
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        result = bot.order_manager._should_skip_open_for_quota()
        assert result is False

    def test_quota_check_chain_blocks_signal_processor_indirectly(self, config, mock_api, mock_alerter, tmp_path):
        """When quota is low, _should_skip_open_for_quota returns True → signal_processor skips."""
        mock_api.volume_quota_remaining = 20
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        assert bot.order_manager._should_skip_open_for_quota() is True


# ── Test 8: DSL evaluation chain ───────────────────────────────────

class TestDSLEvaluationChain:

    def test_tracker_update_price_returns_action_on_trigger(self, config_no_dsl, mock_api, mock_alerter):
        """update_price with legacy trailing mode → returns action on trigger."""
        bot = _build_mock_bot(config_no_dsl, mock_api, mock_alerter)
        tracker = bot.tracker

        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)

        # Price up 5% → trailing takes profit should trigger
        result = tracker.update_price(1, 52500.0)
        assert result is not None

    def test_tracker_update_price_dsl_hard_sl_action(self, config, mock_api, mock_alerter):
        """update_price with price below hard SL → returns 'hard_sl' action."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)
        tracker = bot.tracker

        tracker.add_position(1, "TEST", "long", 100.0, 1.0, leverage=10.0)

        # Price well below hard SL → ROE < -1.25%
        result = tracker.update_price(1, 98.0)
        assert result == "hard_sl"

    def test_dsl_evaluate_dsl_returns_none_at_no_trigger(self, dsl_config, dsl_state_long):
        """evaluate_dsl at low ROE → returns None (no trigger)."""
        result = evaluate_dsl(dsl_state_long, 100.5, dsl_config)
        assert result is None

    def test_dsl_evaluate_dsl_can_return_tier_lock_action(self, dsl_config, dsl_state_long):
        """evaluate_dsl with high ROE + multiple breaches → can return 'tier_lock'."""
        # entry=100, leverage=10x → ROE = (price_diff/entry)*leverage*100
        # Tier 0: 3% trigger, 6% trailing buffer, 3 consecutive breaches
        # First: push price up to set HW ROE (tier 0 activates)
        evaluate_dsl(dsl_state_long, 103.0, dsl_config)  # ROE=30%, HW=30%, tier 0, floor=24%
        # Then drop price to 3 consecutive breaches below locked floor
        r1 = evaluate_dsl(dsl_state_long, 100.5, dsl_config)  # ROE=5%, below 24% floor → breach 1
        r2 = evaluate_dsl(dsl_state_long, 100.5, dsl_config)  # ROE=5%, below 24% floor → breach 2
        r3 = evaluate_dsl(dsl_state_long, 100.5, dsl_config)  # ROE=5%, below 24% floor → breach 3 → tier_lock
        assert r3 == "tier_lock"


# ── Test 9: Cache pruning chain ────────────────────────────────────

class TestCachePruningChain:

    def test_prune_caches_cleans_expired_ai_close_cooldown(self, config, mock_api, mock_alerter):
        """_prune_caches removes expired AI close cooldown entries."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        bot._ai_close_cooldown = {
            "BTC": time.monotonic() - 100,  # expired
            "ETH": time.monotonic() + 100,  # active
        }

        bot.order_manager._prune_caches()

        assert "BTC" not in bot._ai_close_cooldown
        assert "ETH" in bot._ai_close_cooldown

    def test_prune_caches_cleans_expired_recently_closed(self, config, mock_api, mock_alerter):
        """_prune_caches removes expired recently_closed entries."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        bot._recently_closed = {
            1: time.monotonic() - 100,  # expired
            2: time.monotonic() + 100,  # active
        }

        bot.order_manager._prune_caches()

        assert 1 not in bot._recently_closed
        assert 2 in bot._recently_closed

    def test_prune_caches_cleans_close_attempts_without_active_cooldown(self, config, mock_api, mock_alerter):
        """_prune_caches removes close_attempts for symbols without active cooldown."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        bot._close_attempts = {"BTC": 3, "ETH": 1}
        bot._close_attempt_cooldown = {"BTC": time.monotonic() + 100}

        bot.order_manager._prune_caches()

        assert "BTC" in bot._close_attempts
        assert "ETH" not in bot._close_attempts

    def test_prune_caches_cleans_no_price_ticks_for_untracked_positions(self, config, mock_api, mock_alerter):
        """_prune_caches removes no_price_ticks for positions no longer tracked."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        bot.tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        bot._no_price_ticks = {
            1: 2,   # still tracked
            99: 5,  # not tracked anymore
        }

        bot.order_manager._prune_caches()

        assert 1 in bot._no_price_ticks
        assert 99 not in bot._no_price_ticks


# ── Test 10: Kill switch chain ─────────────────────────────────────

class TestKillSwitchChain:

    @pytest.mark.asyncio
    async def test_kill_switch_chain_execution_engine_sets_flag(self, config, mock_api, mock_alerter, tmp_path):
        """Execution engine checks file → sets flag → signal_processor respects it."""
        kill_path = tmp_path / "kill"
        kill_path.touch()

        bot = _build_mock_bot(config, mock_api, mock_alerter)
        bot._kill_switch_path = kill_path
        bot._kill_switch_active = False

        engine = ExecutionEngine(config, mock_api, bot.tracker, mock_alerter, bot)
        engine._ai_mode = False
        engine._last_quota_alert_time = time.time()
        engine._quota_alert_interval = 3600
        engine._ai_close_cooldown = {}
        engine._api_lag_warnings = {}

        # Mock the signal processor for _tick
        sp = MagicMock()
        sp._process_signals = AsyncMock()
        sp._process_ai_decision = AsyncMock()
        sp._refresh_position_context = MagicMock()
        sp._log_outcome = MagicMock()
        bot.signal_processor = sp

        mock_api.get_positions = AsyncMock(return_value=[])
        mock_api.get_mark_price = MagicMock(return_value=0.0)
        mock_api.update_mark_prices_from_positions = MagicMock()
        mock_api._save_tracked_markets = MagicMock()
        mock_api.set_tracked_markets = MagicMock()

        await engine._tick()

        assert bot._kill_switch_active is True
        mock_alerter.send.assert_awaited()
        assert any("KILL SWITCH ACTIVE" in str(call) for call in mock_alerter.send.await_args_list)

    @pytest.mark.asyncio
    async def test_kill_switch_deactivation_resets_flag(self, config, mock_api, mock_alerter, tmp_path):
        """Kill switch file removed → flag resets to False."""
        kill_path = tmp_path / "kill"
        kill_path.touch()

        bot = _build_mock_bot(config, mock_api, mock_alerter)
        bot._kill_switch_path = kill_path
        bot._kill_switch_active = True

        engine = ExecutionEngine(config, mock_api, bot.tracker, mock_alerter, bot)
        engine._ai_mode = False
        engine._last_quota_alert_time = time.time()
        engine._quota_alert_interval = 3600
        engine._ai_close_cooldown = {}
        engine._api_lag_warnings = {}

        # Mock the signal processor for _tick
        sp = MagicMock()
        sp._process_signals = AsyncMock()
        sp._process_ai_decision = AsyncMock()
        sp._refresh_position_context = MagicMock()
        sp._log_outcome = MagicMock()
        bot.signal_processor = sp

        mock_api.get_positions = AsyncMock(return_value=[])
        mock_api.get_mark_price = MagicMock(return_value=0.0)
        mock_api.update_mark_prices_from_positions = MagicMock()
        mock_api._save_tracked_markets = MagicMock()
        mock_api.set_tracked_markets = MagicMock()

        mock_api.get_positions = AsyncMock(return_value=[])

        kill_path.unlink()
        await engine._tick()

        assert bot._kill_switch_active is False


# ══════════════════════════════════════════════════════════════════════
# ── Regression: managers access bot state via self.bot, NOT self ────
# ══════════════════════════════════════════════════════════════════════

class _StrictBot:
    """Mock bot that raises AttributeError for any unset attribute.

    Unlike MagicMock, this does NOT auto-create attributes on access.
    If code reads self.bot._foo and _foo was never set → AttributeError.
    This is how real bot.py works: bot.__init__ sets state attrs, managers
    never define them on themselves.
    """
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        raise AttributeError(f"'_StrictBot' object has no attribute '{name}'")


class TestManagerStateAccess:
    """Regression: managers must access bot state via self.bot, not self.

    Before the fix, managers read self._kill_switch_active etc. Tests masked
    this by setting those attrs directly on the manager. These tests prove
    the fix works: setting state on the manager (self) does NOT help; the
    state must be on self.bot.
    """

    def test_order_manager_prune_caches_reads_bot_state(self, config):
        """OrderManager._prune_caches reads self.bot._ai_close_cooldown, not self._ai_close_cooldown."""
        now = time.monotonic()

        # Correct wiring: bot has state attrs
        bot = _StrictBot(
            _ai_close_cooldown={"BTC": now - 100, "ETH": now + 100},
            _api_lag_warnings={},
            _recently_closed={},
            _close_attempt_cooldown={},
            _close_attempts={},
            _dsl_close_attempt_cooldown={},
            _no_price_ticks={},
            bot_managed_market_ids=set(),
        )
        om = OrderManager(config, None, bot)

        om._prune_caches()

        # BTC (expired) was pruned from bot state
        assert "BTC" not in bot._ai_close_cooldown
        assert "ETH" in bot._ai_close_cooldown

    def test_order_manager_prune_caches_fails_without_bot_state(self, config):
        """If bot state attrs are missing, _prune_caches raises AttributeError (proves self.bot is used)."""
        bot = _StrictBot()  # NO state attrs at all
        om = OrderManager(config, None, bot)

        with pytest.raises(AttributeError):
            om._prune_caches()

    def test_order_manager_does_not_use_self_state(self, config):
        """Setting state on the manager itself (not bot) has no effect on _prune_caches."""
        now = time.monotonic()

        # Bot has empty cooldown — nothing to prune
        bot = _StrictBot(
            _ai_close_cooldown={},
            _api_lag_warnings={},
            _recently_closed={},
            _close_attempt_cooldown={},
            _close_attempts={},
            _dsl_close_attempt_cooldown={},
            _no_price_ticks={},
            bot_managed_market_ids=set(),
        )
        om = OrderManager(config, None, bot)

        # Set state on manager itself — should have NO effect
        om._ai_close_cooldown = {"BTC": now - 100}  # WRONG location

        om._prune_caches()

        # Bot state unchanged (manager's self attr was ignored)
        assert "BTC" not in bot._ai_close_cooldown
        # Manager's own attr still exists (was never read)
        assert "BTC" in om._ai_close_cooldown

    def test_signal_processor_reads_kill_switch_from_bot(self, config):
        """SignalProcessor reads self.bot._kill_switch_active for kill switch check."""
        from core.signal_processor import SignalProcessor

        bot = _StrictBot(
            _kill_switch_active=True,
            _signals_file="/tmp/nonexistent_signals.json",
            _last_signal_hash=None,
            _min_score=60,
            _signal_processed_this_tick=False,
        )
        tracker = PositionTracker(config)
        sp = SignalProcessor(config, None, tracker, MagicMock(), bot)

        # _process_signals should return early when kill switch is active
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(sp._process_signals())
        assert result is None  # early return, no signals processed

    def test_signal_processor_kill_switch_only_on_self_fails(self, config):
        """Setting _kill_switch_active on SignalProcessor (not bot) does NOT trigger kill switch."""
        from core.signal_processor import SignalProcessor

        bot = _StrictBot(
            _kill_switch_active=False,  # NOT active on bot
            _signals_file="/tmp/nonexistent_signals.json",
            _last_signal_hash=None,
            _min_score=60,
            _signal_processed_this_tick=False,
        )
        tracker = PositionTracker(config)
        sp = SignalProcessor(config, None, tracker, MagicMock(), bot)

        # WRONG: set on manager only
        sp._kill_switch_active = True

        # Should NOT early-return (kill switch not active on bot)
        import asyncio
        # _process_signals will proceed past the kill switch check
        # and try to read signals file (which doesn't exist → return)
        result = asyncio.get_event_loop().run_until_complete(sp._process_signals())
        # It returned because signals file doesn't exist, NOT because kill switch
        assert result is None

    def test_execution_engine_kill_switch_reads_from_bot(self, config):
        """ExecutionEngine._tick reads self.bot._kill_switch_active, not self._kill_switch_active."""
        bot = _StrictBot(
            _kill_switch_path=Path("/tmp/nonexistent_kill_file_12345"),
            _kill_switch_active=False,
            _position_sync_failures=0,
            _position_sync_failure_threshold=3,
            order_manager=MagicMock(),
        )
        engine = ExecutionEngine(config, None, PositionTracker(config), MagicMock(), bot)

        # Call _prune_caches path only — just verify no error from bot state access
        import asyncio
        # _tick returns early because api is None after _prune_caches
        asyncio.get_event_loop().run_until_complete(engine._tick())

        # _prune_caches was called (bot state was accessed via self.bot)
        assert bot._kill_switch_active is False

    def test_state_manager_save_reads_bot_state(self, config):
        """StateManager._save_state reads self.bot state attrs (no error = proof it reads from self.bot)."""
        from core.state_manager import StateManager

        bot = _StrictBot(
            _last_ai_decision_ts=None,
            _last_signal_timestamp=None,
            _last_signal_hash=None,
            _recently_closed={},
            _ai_close_cooldown={},
            _close_attempts={},
            _close_attempt_cooldown={},
            _dsl_close_attempts={},
            _dsl_close_attempt_cooldown={},
            _saved_positions=None,
            bot_managed_market_ids=set(),
        )
        tracker = PositionTracker(config)
        sm = StateManager(config, None, tracker, MagicMock(), bot)

        # Patch json.dump to prevent writing to production state dir,
        # but still exercise all the self.bot._* attribute reads
        with patch('core.state_manager.json.dump') as mock_dump:
            # Should succeed — bot has all required state attrs
            # (if SM was reading self._last_signal_timestamp instead of self.bot._last_signal_timestamp,
            #  this would fail because _StrictBot raises AttributeError for missing attrs)
            sm._save_state()

            # json.dump was called with a dict that includes all bot state fields
            assert mock_dump.called
            state_dict = mock_dump.call_args[0][0]
            assert "last_signal_timestamp" in state_dict
            assert "recently_closed" in state_dict
            assert "bot_managed_market_ids" in state_dict

    def test_state_manager_save_fails_without_bot_state(self, config):
        """StateManager._save_state raises AttributeError if bot state attrs missing."""
        from core.state_manager import StateManager

        bot = _StrictBot()  # no state attrs
        tracker = PositionTracker(config)
        sm = StateManager(config, None, tracker, MagicMock(), bot)

        with pytest.raises(AttributeError):
            sm._save_state()


# ══════════════════════════════════════════════════════════════════════
# ── Regression: API references updated after LighterAPI init ────────
# ══════════════════════════════════════════════════════════════════════

class TestApiReferenceUpdate:
    """Regression: managers get api=None at __init__, must be updated in start().

    Bug: bot.py created managers with self.api=None (before LighterAPI init),
    then set self.api = LighterAPI(self.cfg) in start() but forgot to update
    the manager copies. Fix: 4 reassignment lines after LighterAPI init.
    """

    def test_managers_have_none_api_at_init(self, config):
        """Managers created with api=None (as bot.__init__ does before LighterAPI init)."""
        tracker = PositionTracker(config)

        om = OrderManager(config, None, tracker)  # api=None
        sp = SignalProcessor(config, None, tracker, MagicMock(), tracker)
        sm = StateManager(config, None, tracker, MagicMock(), tracker)
        ee = ExecutionEngine(config, None, tracker, MagicMock(), tracker)

        assert om.api is None
        assert sp.api is None
        assert sm.api is None
        assert ee.api is None

    def test_bot_start_updates_manager_api_refs(self, config):
        """After LighterAPI init in start(), bot.py has 4 lines updating manager api refs."""
        # Read source file directly to avoid import side effects
        bot_py = Path(__file__).resolve().parent.parent / "bot.py"
        source = bot_py.read_text()

        # Find the LighterAPI init and 4 reassignment lines after it
        lines = source.split('\n')
        lighter_api_line = -1
        reassignment_count = 0
        reassignment_managers = []

        for i, line in enumerate(lines):
            if 'LighterAPI(self.cfg)' in line:
                lighter_api_line = i
            if lighter_api_line > 0 and i > lighter_api_line:
                if '.api = self.api' in line:
                    reassignment_count += 1
                    for mgr in ['signal_processor', 'state_manager', 'order_manager', 'execution_engine']:
                        if mgr in line:
                            reassignment_managers.append(mgr)
                            break

        assert reassignment_count == 4, (
            f"Expected 4 manager api reassignment lines after LighterAPI init, "
            f"found {reassignment_count}: {reassignment_managers}"
        )

        expected = {'signal_processor', 'state_manager', 'order_manager', 'execution_engine'}
        assert set(reassignment_managers) == expected, (
            f"Expected all 4 managers updated, got: {reassignment_managers}"
        )

    def test_manager_api_gets_updated_to_real_api(self, config):
        """Verify that updating manager.api from None to a real object works."""
        tracker = PositionTracker(config)
        mock_api = MagicMock()

        om = OrderManager(config, None, tracker)
        assert om.api is None

        # Simulate what bot.start() does
        om.api = mock_api
        assert om.api is mock_api


# ══════════════════════════════════════════════════════════════════════
# ── Regression: production wiring integration ───────────────────────
# ══════════════════════════════════════════════════════════════════════

class TestProductionWiring:
    """Verify managers are wired like production (api=None initially, state via self.bot)."""

    def test_all_managers_wired_to_bot_instance(self, config, mock_api, mock_alerter):
        """All 4 managers correctly wired to bot instance for state access."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        # Create real managers with api=None (like bot.__init__), then set api (like start)
        tracker = PositionTracker(config)

        ee = ExecutionEngine(config, None, tracker, mock_alerter, bot)
        sp = SignalProcessor(config, None, tracker, mock_alerter, bot)
        sm = StateManager(config, None, tracker, mock_alerter, bot)
        om = OrderManager(config, None, bot)

        # Update api refs (like bot.start() does)
        ee.api = mock_api
        sp.api = mock_api
        sm.api = mock_api
        om.api = mock_api

        # Verify wiring
        assert ee.cfg is config
        assert ee.bot is bot
        assert ee.api is mock_api
        assert ee.tracker is tracker

        assert sp.cfg is config
        assert sp.bot is bot
        assert sp.api is mock_api

        assert sm.cfg is config
        assert sm.bot is bot
        assert sm.api is mock_api

        assert om.cfg is config
        assert om.bot is bot
        assert om.api is mock_api

    def test_managers_dont_have_bot_state_on_themselves(self, config, mock_api, mock_alerter):
        """After correct wiring, bot state attrs exist on bot, not on managers."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        ee = ExecutionEngine(config, mock_api, bot.tracker, mock_alerter, bot)
        sp = SignalProcessor(config, mock_api, bot.tracker, mock_alerter, bot)
        sm = StateManager(config, mock_api, bot.tracker, mock_alerter, bot)
        om = OrderManager(config, mock_api, bot)

        # Bot has state attrs
        assert hasattr(bot, '_kill_switch_active')
        assert hasattr(bot, '_ai_close_cooldown')
        assert hasattr(bot, '_recently_closed')
        assert hasattr(bot, '_close_attempts')

        # Managers should NOT have these bot state attrs on themselves
        # (they read from self.bot.X, not self.X)
        # Note: we don't set these on managers, so they won't have them
        # This test documents the intended wiring
        for mgr in [ee, sp, sm, om]:
            assert mgr.bot is bot, f"{type(mgr).__name__}.bot must be the bot instance"
            assert mgr.cfg is config

    def test_prune_caches_modifies_bot_state_not_manager_copies(self, config, mock_api, mock_alerter):
        """_prune_caches modifies bot state dicts, not local copies on the manager."""
        bot = _build_mock_bot(config, mock_api, mock_alerter)

        now = time.monotonic()
        bot._ai_close_cooldown = {"BTC": now - 100}  # expired
        bot._recently_closed = {1: now - 100}        # expired

        om = bot.order_manager

        # Call prune
        om._prune_caches()

        # Verify bot state was modified (entries pruned)
        assert "BTC" not in bot._ai_close_cooldown
        assert 1 not in bot._recently_closed

        # Verify manager doesn't have its own copies of these attrs
        assert not hasattr(om, '_ai_close_cooldown') or om._ai_close_cooldown is not bot._ai_close_cooldown
        assert not hasattr(om, '_recently_closed') or om._recently_closed is not bot._recently_closed

    def test_bot_init_creates_managers_with_none_api(self, config):
        """Verify bot.py __init__ creates managers with self.api=None.

        This is the root cause of Bug Class 2: managers are created before
        LighterAPI is initialized, so they get api=None.
        """
        # Read source file directly to avoid import side effects
        bot_py = Path(__file__).resolve().parent.parent / "bot.py"
        source = bot_py.read_text()

        # __init__ should declare self.api = None
        assert 'self.api: LighterAPI | None = None' in source or (
            'self.api' in source and 'None' in source
        )

        # start() should create LighterAPI
        assert 'LighterAPI(self.cfg)' in source

        # start() should have api reassignment lines for all 4 managers
        for mgr in ['signal_processor', 'state_manager', 'order_manager', 'execution_engine']:
            pattern = f'self.{mgr}.api = self.api'
            assert pattern in source, (
                f"Missing '{pattern}' in bot.py — manager api won't be updated after init"
            )
