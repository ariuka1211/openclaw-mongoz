"""
Tests for SignalProcessor — signal processing and AI decision execution.

Focuses on testable pure-logic methods. Mocks bot, api, tracker, alerter,
and missing utility functions (safe_read_json, _db, hashlib).
"""

import json
import sys
import time
import hashlib as real_hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Patch missing dependencies before importing SignalProcessor ────────

import asyncio as _asyncio
import json as _json

def _real_safe_read_json(path):
    """Minimal implementation of safe_read_json for tests."""
    try:
        p = Path(path)
        if not p.exists():
            return None
        with open(p) as f:
            return _json.load(f)
    except Exception:
        return None

# safe_read_json is imported from ipc_utils in bot.py but doesn't exist yet.
sys.modules.setdefault("ipc_utils", MagicMock())
sys.modules["ipc_utils"].safe_read_json = _real_safe_read_json

from core.signal_processor import SignalProcessor

# Inject missing module-level names for the modules that now contain the logic
import core.shared_utils as _su_mod
_su_mod._db = None  # Will be patched per-test when needed

import core.signal_handler as _sh_mod
_sh_mod.safe_read_json = _real_safe_read_json


# ── Helpers ───────────────────────────────────────────────────────────

@pytest.fixture
def no_sleep():
    """Patch asyncio.sleep to be instant for fast async tests."""
    with patch("core.signal_handler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.return_value = None
        yield mock_sleep


def _make_processor(config, mock_api, mock_bot):
    """Create SignalProcessor with bot attributes pre-set for testing."""
    from core.position_tracker import PositionTracker
    tracker = PositionTracker(config)
    alerter = MagicMock()
    alerter.send = AsyncMock()

    sp = SignalProcessor(config, mock_api, tracker, alerter, mock_bot)

    # Bot sets self.alerts on the processor (separate from self.alerter)
    sp.alerts = alerter

    # These attributes are on mock_bot (set via conftest.py), not on sp directly
    # sp now accesses them via self.bot._attrname
    sp._write_equity_file = MagicMock()  # no-op for testing
    sp._should_pace_orders = MagicMock(return_value=False)  # never pace in tests
    sp._should_skip_open_for_quota = MagicMock(return_value=False)  # never skip for quota
    sp._mark_order_submitted = MagicMock()  # no-op

    return sp


# ── _validate_ai_decision ─────────────────────────────────────────────

class TestValidateAiDecision:
    """Tests for _validate_ai_decision()."""

    def test_valid_open_decision(self):
        """Valid open decision with all required fields passes."""
        sp = MagicMock()  # use unbound method
        decision = {
            "action": "open",
            "symbol": "BTC",
            "requested_size_usd": 100,
            "direction": "long",
            "confidence": 0.8,
        }
        # Call unbound
        result = SignalProcessor._validate_ai_decision(sp, decision)
        assert result is None

    def test_missing_action_returns_error(self):
        """Missing action field returns validation error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {"symbol": "BTC"})
        assert result is not None
        assert "action" in result.lower() or "Invalid action" in result

    def test_invalid_action_returns_error(self):
        """Invalid action (not open/close/close_all/hold) returns error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {"action": "yolo"})
        assert result is not None
        assert "Invalid action" in result

    def test_hold_action_is_valid(self):
        """'hold' action is valid even with no other fields."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {"action": "hold"})
        assert result is None

    def test_close_action_with_symbol_is_valid(self):
        """Close action with symbol is valid."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "close",
            "symbol": "ETH",
        })
        assert result is None

    def test_open_missing_symbol_returns_error(self):
        """Open action missing symbol returns error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "open",
            "requested_size_usd": 100,
            "direction": "long",
        })
        assert result is not None
        assert "symbol" in result.lower()

    def test_open_missing_size_returns_error(self):
        """Open action with size_usd=0 returns error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "open",
            "symbol": "BTC",
            "requested_size_usd": 0,
            "direction": "long",
        })
        assert result is not None
        assert "size" in result.lower()

    def test_open_missing_direction_returns_error(self):
        """Open action missing direction returns error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "open",
            "symbol": "BTC",
            "requested_size_usd": 100,
        })
        assert result is not None
        assert "direction" in result.lower()

    def test_open_invalid_direction_returns_error(self):
        """Open action with invalid direction returns error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "open",
            "symbol": "BTC",
            "requested_size_usd": 100,
            "direction": "sideways",
        })
        assert result is not None
        assert "direction" in result.lower()

    def test_open_confidence_out_of_range(self):
        """Confidence > 1.0 returns error."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "open",
            "symbol": "BTC",
            "requested_size_usd": 100,
            "direction": "long",
            "confidence": 1.5,
        })
        assert result is not None
        assert "confidence" in result.lower()

    def test_close_all_is_valid(self):
        """close_all action is valid with no other fields."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {"action": "close_all"})
        assert result is None

    def test_legacy_size_usd_fallback(self):
        """Legacy size_usd field works when requested_size_usd is absent."""
        sp = MagicMock()
        result = SignalProcessor._validate_ai_decision(sp, {
            "action": "open",
            "symbol": "BTC",
            "size_usd": 50,
            "direction": "short",
        })
        assert result is None


# ── _resolve_market_id ────────────────────────────────────────────────

class TestResolveMarketId:
    """Tests for _resolve_market_id()."""

    def test_signal_based_lookup(self, config):
        """Resolves market ID from signals file by symbol matching."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        signals_file.write_text(json.dumps({
            "opportunities": [
                {"symbol": "BTC", "marketId": 42},
                {"symbol": "ETH", "marketId": 99},
            ]
        }))
        mock_bot._signals_file = str(signals_file)

        result = sp._resolve_market_id("ETH")
        assert result == 99
        signals_file.unlink(missing_ok=True)

    def test_position_tracker_fallback(self, config):
        """Falls back to position tracker when not in signals."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        # Add a position to tracker
        sp.tracker.add_position(7, "SOL", "long", 100.0, 1.0)

        # No signals file — falls back to tracker
        result = sp._resolve_market_id("SOL")
        assert result == 7

    def test_returns_none_when_not_found(self, config):
        """Returns None when symbol not in signals or positions."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        result = sp._resolve_market_id("DOGE")
        assert result is None


# ── _log_outcome ──────────────────────────────────────────────────────

class TestLogOutcome:
    """Tests for _log_outcome() — win/loss calculations."""

    def test_win_long(self, config):
        """Win calculation for long: exit > entry."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        pos = MagicMock()
        pos.symbol = "BTC"
        pos.side = "long"
        pos.entry_price = 100.0
        pos.size = 1.0
        pos.opened_at = datetime.now(timezone.utc)
        pos.dsl_state = MagicMock()
        pos.dsl_state.leverage = 10.0

        mock_db = MagicMock()
        sp.tracker.account_equity = 1000.0

        with patch.object(_su_mod, "_db", mock_db):
            sp._log_outcome(pos, 110.0, "dsl_close")

        assert mock_db.log_outcome.called
        call_args = mock_db.log_outcome.call_args[0][0]
        assert call_args["pnl_usd"] > 0  # profit
        assert call_args["symbol"] == "BTC"
        assert "estimated" not in call_args["exit_reason"]

    def test_loss_short(self, config):
        """Loss calculation for short: exit > entry means loss."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        pos = MagicMock()
        pos.symbol = "ETH"
        pos.side = "short"
        pos.entry_price = 100.0
        pos.size = 1.0
        pos.opened_at = datetime.now(timezone.utc)
        pos.dsl_state = MagicMock()
        pos.dsl_state.leverage = 5.0

        mock_db = MagicMock()
        sp.tracker.account_equity = 500.0

        with patch.object(_su_mod, "_db", mock_db):
            sp._log_outcome(pos, 110.0, "ai_close")

        assert mock_db.log_outcome.called
        call_args = mock_db.log_outcome.call_args[0][0]
        assert call_args["pnl_usd"] < 0  # loss
        assert call_args["symbol"] == "ETH"

    def test_estimated_tag(self, config):
        """Estimated flag appends (estimated) to exit_reason."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        pos = MagicMock()
        pos.symbol = "BTC"
        pos.side = "long"
        pos.entry_price = 100.0
        pos.size = 1.0
        pos.opened_at = datetime.now(timezone.utc)
        pos.dsl_state = MagicMock()
        pos.dsl_state.leverage = 10.0

        mock_db = MagicMock()
        sp.tracker.account_equity = 1000.0

        with patch.object(_su_mod, "_db", mock_db):
            sp._log_outcome(pos, 105.0, "ai_close", estimated=True)

        call_args = mock_db.log_outcome.call_args[0][0]
        assert "estimated" in call_args["exit_reason"]


# ── _write_ai_result ──────────────────────────────────────────────────

class TestWriteAiResult:
    """Tests for _write_ai_result()."""

    def test_writes_json_file(self, config, tmp_path):
        """Writes a valid JSON result file with expected fields."""
        mock_api = MagicMock()
        mock_api.get_mark_price = MagicMock(return_value=50000.0)
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)

        # Redirect result file to tmp_path
        result_file = tmp_path / "ai_result.json"
        mock_bot._ai_result_file = result_file
        mock_bot._result_dirty = False

        # Add a position to tracker
        sp.tracker.add_position(1, "BTC", "long", 50000.0, 0.1)

        decision = {"action": "open", "symbol": "BTC", "decision_id": "test-123"}
        sp._write_ai_result(decision, success=True)

        assert result_file.exists()
        data = json.loads(result_file.read_text())
        assert data["processed_decision_id"] == "test-123"
        assert data["success"] is True
        assert data["decision_action"] == "open"
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "BTC"


# ── Kill switch ───────────────────────────────────────────────────────

class TestKillSwitch:
    """Tests for kill switch behavior in _process_signals."""

    @pytest.mark.asyncio
    async def test_skips_when_kill_switch_active(self, config, no_sleep):
        """_process_signals returns immediately when kill switch is active."""
        mock_api = MagicMock()
        mock_bot = MagicMock()
        sp = _make_processor(config, mock_api, mock_bot)
        mock_bot._kill_switch_active = True

        # Write a valid signals file
        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        signals_file.write_text(json.dumps({
            "timestamp": "2025-01-01T00:00:00Z",
            "opportunities": [
                {"marketId": 1, "symbol": "BTC", "compositeScore": 80,
                 "direction": "long", "dailyVolatility": 0.03},
            ],
            "config": {},
        }))

        mock_bot._signals_file = str(signals_file)
        await sp._process_signals()

        # Should not have called open_position
        mock_api.open_position.assert_not_called()

        # Cleanup
        signals_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_processes_when_kill_switch_inactive(self, config, mock_bot, no_sleep):
        """_process_signals proceeds when kill switch is inactive."""
        mock_api = MagicMock()
        mock_api.get_price = AsyncMock(return_value=50000.0)
        mock_api.open_position = AsyncMock(return_value=True)
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_api.get_price_with_mark_fallback = AsyncMock(return_value=50000.0)
        mock_api.get_market_leverage = AsyncMock(return_value=10.0)
        mock_api.volume_quota_remaining = 100

        mock_bot._get_balance = AsyncMock(return_value=1000)
        mock_bot._opened_signals = set()
        mock_bot._last_signal_hash = None
        mock_bot.bot_managed_market_ids = set()
        mock_bot._save_state = MagicMock()
        mock_bot._min_score = 60

        sp = _make_processor(config, mock_api, mock_bot)
        mock_bot._kill_switch_active = False

        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        signals_file.write_text(json.dumps({
            "timestamp": "2025-01-01T00:00:01Z",
            "opportunities": [
                {"marketId": 1, "symbol": "BTC", "compositeScore": 80,
                 "direction": "long", "dailyVolatility": 0.03},
            ],
            "config": {},
        }))

        mock_bot._signals_file = str(signals_file)
        await sp._process_signals()

        # Should have attempted to open
        mock_api.get_price.assert_called()

        # Cleanup
        signals_file.unlink(missing_ok=True)


# ── Signal hash dedup ─────────────────────────────────────────────────

class TestSignalHashDedup:
    """Tests for content-based signal deduplication."""

    @pytest.mark.asyncio
    async def test_same_hash_skips_processing(self, config, no_sleep):
        """Identical signal content (same hash) is skipped."""
        mock_api = MagicMock()
        mock_api.get_price = AsyncMock(return_value=50000.0)

        mock_bot = MagicMock()
        mock_bot._last_signal_hash = "abc123"
        mock_bot._get_balance = AsyncMock(return_value=1000)

        sp = _make_processor(config, mock_api, mock_bot)

        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        # Same opportunities as what generates hash "abc123"
        signals_data = {
            "timestamp": "2025-01-01T00:00:00Z",
            "opportunities": [{"symbol": "BTC", "marketId": 1, "compositeScore": 80,
                               "direction": "long", "dailyVolatility": 0.03}],
            "config": {},
        }
        signals_file.write_text(json.dumps(signals_data))

        # Compute the actual hash so we can set it
        opp_hash = real_hashlib.sha256(
            json.dumps(signals_data.get("opportunities", []), sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        mock_bot._last_signal_hash = opp_hash

        mock_bot._signals_file = str(signals_file)
        await sp._process_signals()

        # get_price should NOT be called (processing was skipped)
        mock_api.get_price.assert_not_called()

        # Cleanup
        signals_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_new_hash_processes(self, config, mock_bot, no_sleep):
        """New signal content (different hash) triggers processing."""
        mock_api = MagicMock()
        mock_api.get_price = AsyncMock(return_value=50000.0)
        mock_api.open_position = AsyncMock(return_value=True)
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_api.get_price_with_mark_fallback = AsyncMock(return_value=50000.0)
        mock_api.get_market_leverage = AsyncMock(return_value=10.0)
        mock_api.volume_quota_remaining = 100

        mock_bot._last_signal_hash = "old_hash_123456"
        mock_bot._get_balance = AsyncMock(return_value=1000)
        mock_bot._opened_signals = set()
        mock_bot.bot_managed_market_ids = set()
        mock_bot._save_state = MagicMock()
        mock_bot._min_score = 60
        mock_bot._kill_switch_active = False

        sp = _make_processor(config, mock_api, mock_bot)

        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        signals_file.write_text(json.dumps({
            "timestamp": "2025-01-01T00:00:02Z",
            "opportunities": [{"symbol": "ETH", "marketId": 2, "compositeScore": 80,
                               "direction": "short", "dailyVolatility": 0.03}],
            "config": {},
        }))

        mock_bot._signals_file = str(signals_file)
        await sp._process_signals()

        # get_price SHOULD be called (new hash, processing proceeded)
        mock_api.get_price.assert_called()

        # Cleanup
        signals_file.unlink(missing_ok=True)


# ── Min score filter ──────────────────────────────────────────────────

class TestMinScoreFilter:
    """Tests for minimum score filtering in _process_signals."""

    @pytest.mark.asyncio
    async def test_below_min_score_skipped(self, config, no_sleep):
        """Signals below _min_score are skipped."""
        mock_api = MagicMock()
        mock_api.get_price = AsyncMock(return_value=50000.0)

        mock_bot = MagicMock()
        mock_bot._last_signal_hash = None
        mock_bot._get_balance = AsyncMock(return_value=1000)

        sp = _make_processor(config, mock_api, mock_bot)
        sp._min_score = 70

        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        signals_file.write_text(json.dumps({
            "timestamp": "2025-01-01T00:00:03Z",
            "opportunities": [
                {"marketId": 1, "symbol": "BTC", "compositeScore": 50,  # below min
                 "direction": "long", "dailyVolatility": 0.03},
                {"marketId": 2, "symbol": "ETH", "compositeScore": 80,  # above min
                 "direction": "long", "dailyVolatility": 0.03},
            ],
            "config": {},
        }))

        mock_bot._signals_file = str(signals_file)

        # Patch open_position to track calls
        mock_api.open_position = AsyncMock(return_value=True)
        mock_api.get_positions = AsyncMock(return_value=[])
        mock_api.get_price_with_mark_fallback = AsyncMock(return_value=50000.0)
        mock_api.get_market_leverage = AsyncMock(return_value=10.0)
        mock_api.volume_quota_remaining = 100
        mock_bot._opened_signals = set()
        mock_bot.bot_managed_market_ids = set()
        mock_bot._save_state = MagicMock()

        await sp._process_signals()

        # Should have processed only ETH (score 80 >= 70)
        # BTC (score 50) should be skipped
        # We can verify by checking get_price was called for market 2 but not market 1
        # (Both may be called due to the loop, but open_position should only be called once)
        assert mock_api.open_position.call_count <= 1

        # Cleanup
        signals_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_all_below_min_score_no_opens(self, config, no_sleep):
        """When all signals are below min score, no positions are opened."""
        mock_api = MagicMock()
        mock_api.get_price = AsyncMock(return_value=50000.0)

        mock_bot = MagicMock()
        mock_bot._last_signal_hash = None
        mock_bot._get_balance = AsyncMock(return_value=1000)

        sp = _make_processor(config, mock_api, mock_bot)
        sp._min_score = 90

        signals_file = Path(config.signals_file)
        signals_file.parent.mkdir(parents=True, exist_ok=True)
        signals_file.write_text(json.dumps({
            "timestamp": "2025-01-01T00:00:04Z",
            "opportunities": [
                {"marketId": 1, "symbol": "BTC", "compositeScore": 70,
                 "direction": "long", "dailyVolatility": 0.03},
            ],
            "config": {},
        }))

        mock_api.open_position = AsyncMock(return_value=True)
        mock_bot._signals_file = str(signals_file)

        await sp._process_signals()

        mock_api.open_position.assert_not_called()

        # Cleanup
        signals_file.unlink(missing_ok=True)
