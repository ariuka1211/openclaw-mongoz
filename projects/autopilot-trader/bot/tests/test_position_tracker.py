"""
Tests for core.position_tracker.PositionTracker
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import TrackedPosition
from core.position_tracker import PositionTracker
from dsl import DSLState, DSLTier


# ── compute_tp_price() ──────────────────────────────────────────────

class TestComputeTpPrice:
    """Tests for PositionTracker.compute_tp_price()."""

    def test_compute_tp_price_long_hwm_below_trigger_returns_none(self, config_no_dsl):
        """Long: HWM below trigger → None."""
        tracker = PositionTracker(config_no_dsl)
        # trailing_tp_trigger_pct=3%, entry=50000 → trigger=51500
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=51000.0,
        )
        assert tracker.compute_tp_price(pos) is None

    def test_compute_tp_price_long_hwm_above_trigger_returns_delta(self, config_no_dsl):
        """Long: HWM above trigger → HWM * (1 - delta_pct/100)."""
        tracker = PositionTracker(config_no_dsl)
        # trailing_tp_trigger_pct=3%, entry=50000 → trigger=51500
        # trailing_tp_delta_pct=1%, HWM=52000 → TP = 52000 * 0.99 = 51480
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=52000.0,
        )
        assert tracker.compute_tp_price(pos) == pytest.approx(51480.0)

    def test_compute_tp_price_short_hwm_above_trigger_returns_none(self, config_no_dsl):
        """Short: HWM above trigger → None (trigger is below entry for shorts)."""
        tracker = PositionTracker(config_no_dsl)
        # trailing_tp_trigger_pct=3%, entry=3000 → trigger=2910
        # For short, trigger is below entry. If HWM=2950 (above trigger), returns None
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=2950.0,
        )
        assert tracker.compute_tp_price(pos) is None

    def test_compute_tp_price_short_hwm_below_trigger_returns_delta(self, config_no_dsl):
        """Short: HWM below trigger → HWM * (1 + delta_pct/100)."""
        tracker = PositionTracker(config_no_dsl)
        # trailing_tp_trigger_pct=3%, entry=3000 → trigger=2910
        # HWM=2850 (below trigger) → TP = 2850 * 1.01 = 2878.5
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=2850.0,
        )
        assert tracker.compute_tp_price(pos) == pytest.approx(2878.5)


# ── compute_sl_price() ──────────────────────────────────────────────

class TestComputeSlPrice:
    """Tests for PositionTracker.compute_sl_price()."""

    def test_compute_sl_price_long_no_trailing_sl(self, config_no_dsl):
        """Long, no trailing_sl_level → entry * (1 - sl_pct/100)."""
        tracker = PositionTracker(config_no_dsl)
        # sl_pct=1.25, entry=50000 → SL = 50000 * (1 - 1.25/100) = 49375
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        assert tracker.compute_sl_price(pos) == pytest.approx(49375.0)

    def test_compute_sl_price_long_trailing_sl_set(self, config_no_dsl):
        """Long, trailing_sl_level set → returns that level."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=52000.0,
            trailing_sl_level=51000.0,
        )
        assert tracker.compute_sl_price(pos) == 51000.0

    def test_compute_sl_price_short_no_trailing_sl(self, config_no_dsl):
        """Short, no trailing_sl_level → entry * (1 + sl_pct/100)."""
        tracker = PositionTracker(config_no_dsl)
        # sl_pct=1.25, entry=3000 → SL = 3000 * (1 + 1.25/100) = 3037.5
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
        )
        assert tracker.compute_sl_price(pos) == pytest.approx(3037.5)

    def test_compute_sl_price_per_position_sl_pct_overrides_config(self, config_no_dsl):
        """Per-position sl_pct overrides config default."""
        tracker = PositionTracker(config_no_dsl)
        # config sl_pct=1.25, but position has sl_pct=2.0
        # entry=50000 → SL = 50000 * (1 - 2.0/100) = 49000
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            sl_pct=2.0,
        )
        assert tracker.compute_sl_price(pos) == pytest.approx(49000.0)


# ── _get_sl_pct() ───────────────────────────────────────────────────

class TestGetSlPct:
    """Tests for PositionTracker._get_sl_pct()."""

    def test_get_sl_pct_none_uses_config(self, config_no_dsl):
        """Position sl_pct=None → uses config.sl_pct."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            sl_pct=None,
        )
        assert tracker._get_sl_pct(pos) == config_no_dsl.sl_pct

    def test_get_sl_pct_per_position_value(self, config_no_dsl):
        """Position sl_pct=2.0 → uses 2.0."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            sl_pct=2.0,
        )
        assert tracker._get_sl_pct(pos) == 2.0


# ── update_price() — Legacy mode ────────────────────────────────────

class TestUpdatePriceLegacy:
    """Tests for PositionTracker.update_price() in legacy mode (DSL disabled)."""

    def test_update_price_long_rises_hwm_updates(self, config_no_dsl):
        """Long: price rises, HWM updates."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        tracker.positions[1] = pos
        result = tracker.update_price(1, 51000.0)
        assert pos.high_water_mark == 51000.0
        assert result is None  # trigger not hit yet (3% = 51500)

    def test_update_price_long_hits_trailing_trigger(self, config_no_dsl):
        """Long: price hits trailing trigger → trailing_active=True, returns ("trailing_activated", {...})."""
        tracker = PositionTracker(config_no_dsl)
        tracker.account_equity = 1000.0
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        tracker.positions[1] = pos
        # Price hits 51500 = entry * 1.03 (trigger)
        result = tracker.update_price(1, 51500.0)
        assert pos.trailing_active is True
        assert isinstance(result, tuple)
        assert result[0] == "trailing_activated"
        assert "price" in result[1]
        assert result[1]["price"] == 51500.0

    def test_update_price_long_trailing_sl_ratchets_up(self, config_no_dsl):
        """Long: trailing SL ratchets up, never down."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        tracker.positions[1] = pos

        # First tick: price=51000 → candidate SL = 51000 * 0.9875 = 50362.5
        tracker.update_price(1, 51000.0)
        first_sl = pos.trailing_sl_level
        assert first_sl == pytest.approx(51000.0 * (1 - 1.25 / 100))

        # Second tick: price drops to 50500 → candidate = 50500 * 0.9875 = 49868.75
        # This is lower than first_sl, so trailing_sl_level should NOT change
        tracker.update_price(1, 50500.0)
        assert pos.trailing_sl_level == pytest.approx(first_sl)  # unchanged

        # Third tick: price rises to 52000 → candidate = 52000 * 0.9875 = 51350
        tracker.update_price(1, 52000.0)
        assert pos.trailing_sl_level == pytest.approx(52000.0 * (1 - 1.25 / 100))
        assert pos.trailing_sl_level > first_sl  # ratcheted up

    def test_update_price_long_price_drops_to_sl(self, config_no_dsl):
        """Long: price drops to SL → returns 'stop_loss'."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=52000.0,
            trailing_sl_level=51000.0,
        )
        tracker.positions[1] = pos
        # Price drops to trailing SL level
        result = tracker.update_price(1, 51000.0)
        assert result == "stop_loss"

    def test_update_price_short_drops_hwm_updates(self, config_no_dsl):
        """Short: price drops, HWM updates (lower)."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
        )
        tracker.positions[2] = pos
        # Price 2950: trigger = 3000 * 0.97 = 2910. 2950 > 2910 so no trailing activation
        result = tracker.update_price(2, 2950.0)
        assert pos.high_water_mark == 2950.0
        assert result is None  # trigger not hit yet

    def test_update_price_short_trailing_sl_ratchets_down(self, config_no_dsl):
        """Short: trailing SL ratchets down, never up."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
        )
        tracker.positions[2] = pos

        # First tick: price=2900 → candidate SL = 2900 * 1.0125 = 2936.25
        tracker.update_price(2, 2900.0)
        first_sl = pos.trailing_sl_level
        assert first_sl == pytest.approx(2900.0 * (1 + 1.25 / 100))

        # Price rises to 2950 → candidate = 2950 * 1.0125 = 2986.875
        # Higher than first_sl, should NOT update (short SL ratchets down)
        tracker.update_price(2, 2950.0)
        assert pos.trailing_sl_level == pytest.approx(first_sl)

        # Price drops to 2800 → candidate = 2800 * 1.0125 = 2835
        # Lower than first_sl, should update
        tracker.update_price(2, 2800.0)
        assert pos.trailing_sl_level == pytest.approx(2800.0 * (1 + 1.25 / 100))
        assert pos.trailing_sl_level < first_sl  # ratcheted down


# ── update_price() — DSL mode ───────────────────────────────────────

class TestUpdatePriceDsl:
    """Tests for PositionTracker.update_price() in DSL mode."""

    def test_update_price_dsl_enabled_delegates_to_evaluate_dsl(self, config):
        """DSL enabled + dsl_state → delegates to evaluate_dsl (high_water_roe updated)."""
        tracker = PositionTracker(config)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            dsl_state=DSLState(
                side="long", entry_price=50000.0,
                leverage=10.0, effective_leverage=10.0,
                high_water_price=50000.0,
                high_water_time=datetime.now(timezone.utc),
            ),
        )
        tracker.positions[1] = pos
        # Small price change: ROE=2%, below min tier trigger (3%), so no DSL actions
        result = tracker.update_price(1, 50100.0)
        assert result is None
        # Verify DSL state was updated (high_water_roe reflects the move)
        assert pos.dsl_state.high_water_roe == pytest.approx(2.0)

    def test_update_price_dsl_returns_tier_lock(self, config):
        """Returns ("dsl_tier_lock", {...}) when DSL triggers tier lock."""
        tracker = PositionTracker(config)
        dsl_state = DSLState(
            side="long", entry_price=50000.0,
            leverage=10.0, effective_leverage=10.0,
            high_water_price=51000.0,
            high_water_time=datetime.now(timezone.utc),
        )
        # Pre-set DSL state to simulate a position that already hit high tier
        dsl_state.high_water_roe = 15.0
        # Use DSLTier object directly (not dict from config)
        dsl_state.current_tier = DSLTier(
            trigger_pct=7, lock_hw_pct=40,
            trailing_buffer_roe=5, consecutive_breaches=3,
        )
        dsl_state.locked_floor_roe = 10.0  # locked at 10% ROE
        dsl_state.breach_count = 3

        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=51000.0,
            dsl_state=dsl_state,
        )
        tracker.positions[1] = pos

        # Price drops so ROE < locked_floor_roe (10%)
        # At entry=50000, eff_lev=10 → ROE < 10% when move < 1%, price < 50500
        # evaluate_dsl returns "tier_lock", update_price wraps as ("dsl_tier_lock", {...})
        result = tracker.update_price(1, 49800.0)
        assert result is not None
        assert isinstance(result, tuple)
        assert result[0] == "dsl_tier_lock"

    def test_update_price_dsl_returns_stagnation_timer(self, config):
        """Returns ("dsl_stagnation_timer", {...}) when stagnation timer starts."""
        tracker = PositionTracker(config)
        dsl_state = DSLState(
            side="long", entry_price=50000.0,
            leverage=10.0, effective_leverage=10.0,
            high_water_price=52000.0,
            high_water_time=datetime.now(timezone.utc),
        )
        # Pre-set: high_water_roe above min trigger, stagnation starts
        dsl_state.high_water_roe = 5.0  # above min tier trigger (3%)

        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=52000.0,
            dsl_state=dsl_state,
        )
        tracker.positions[1] = pos

        # Price movement causes high_water_roe update which triggers stagnation_started.
        # The _stagnation_alerted flag fires the alert tuple on first detection.
        result = tracker.update_price(1, 51000.0)
        assert result is not None
        assert isinstance(result, tuple)
        assert result[0] == "dsl_stagnation_timer"


# ── add_position() ──────────────────────────────────────────────────

class TestAddPosition:
    """Tests for PositionTracker.add_position()."""

    def test_add_position_creates_tracked_position(self, config_no_dsl):
        """Creates TrackedPosition in self.positions."""
        tracker = PositionTracker(config_no_dsl)
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        assert 1 in tracker.positions
        pos = tracker.positions[1]
        assert pos.symbol == "BTC"
        assert pos.side == "long"
        assert pos.entry_price == 50000.0
        assert pos.size == 0.1

    def test_add_position_dsl_state_created_when_enabled(self, config):
        """DSL state created when dsl_enabled=True."""
        tracker = PositionTracker(config)
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        pos = tracker.positions[1]
        assert pos.dsl_state is not None
        assert pos.dsl_state.side == "long"
        assert pos.dsl_state.entry_price == 50000.0

    def test_add_position_effective_leverage_uses_equity(self, config):
        """Effective leverage uses account_equity when > 0."""
        tracker = PositionTracker(config)
        tracker.account_equity = 1000.0
        # notional = 0.1 * 50000 = 5000, equity=1000 → eff_lev = 5.0
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        pos = tracker.positions[1]
        assert pos.dsl_state.effective_leverage == pytest.approx(5.0)

    def test_add_position_effective_leverage_falls_back_to_config(self, config):
        """Falls back to config leverage when equity = 0."""
        tracker = PositionTracker(config)
        tracker.account_equity = 0.0
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        pos = tracker.positions[1]
        assert pos.dsl_state.effective_leverage == config.default_leverage

    def test_add_position_per_position_sl_pct_stored(self, config_no_dsl):
        """Per-position sl_pct stored."""
        tracker = PositionTracker(config_no_dsl)
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1, sl_pct=2.5)
        pos = tracker.positions[1]
        assert pos.sl_pct == 2.5


# ── remove_position() ───────────────────────────────────────────────

class TestRemovePosition:
    """Tests for PositionTracker.remove_position()."""

    def test_remove_position_existing_market_id(self, config_no_dsl):
        """Existing market_id → removed."""
        tracker = PositionTracker(config_no_dsl)
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        assert 1 in tracker.positions
        tracker.remove_position(1)
        assert 1 not in tracker.positions

    def test_remove_position_non_existing_market_id(self, config_no_dsl):
        """Non-existing market_id → no error."""
        tracker = PositionTracker(config_no_dsl)
        tracker.remove_position(999)  # should not raise
        assert 999 not in tracker.positions
