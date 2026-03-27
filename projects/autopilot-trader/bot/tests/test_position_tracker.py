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


# ── _compute_hard_floor_price() ─────────────────────────────────────

class TestComputeHardFloorPrice:
    """Tests for PositionTracker._compute_hard_floor_price()."""

    def test_compute_hard_floor_price_long_no_per_position_sl(self, config_no_dsl):
        """Long, no per-position sl_pct → entry * (1 - config.hard_sl_pct/100)."""
        tracker = PositionTracker(config_no_dsl)
        # hard_sl_pct=1.25, entry=50000 → SL = 50000 * (1 - 1.25/100) = 49375
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        assert tracker._compute_hard_floor_price(pos) == pytest.approx(49375.0)

    def test_compute_hard_floor_price_long_per_position_sl_overrides(self, config_no_dsl):
        """Long, per-position sl_pct overrides config default."""
        tracker = PositionTracker(config_no_dsl)
        # config hard_sl_pct=1.25, but position has sl_pct=2.0
        # entry=50000 → SL = 50000 * (1 - 2.0/100) = 49000
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            sl_pct=2.0,
        )
        assert tracker._compute_hard_floor_price(pos) == pytest.approx(49000.0)

    def test_compute_hard_floor_price_short_no_per_position_sl(self, config_no_dsl):
        """Short, no per-position sl_pct → entry * (1 + config.hard_sl_pct/100)."""
        tracker = PositionTracker(config_no_dsl)
        # hard_sl_pct=1.25, entry=3000 → SL = 3000 * (1 + 1.25/100) = 3037.5
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
        )
        assert tracker._compute_hard_floor_price(pos) == pytest.approx(3037.5)

    def test_compute_hard_floor_price_short_per_position_sl_overrides(self, config_no_dsl):
        """Short, per-position sl_pct overrides config default."""
        tracker = PositionTracker(config_no_dsl)
        # config hard_sl_pct=1.25, but position has sl_pct=2.0
        # entry=3000 → SL = 3000 * (1 + 2.0/100) = 3060
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
            sl_pct=2.0,
        )
        assert tracker._compute_hard_floor_price(pos) == pytest.approx(3060.0)


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
        assert result is None  # trigger not reached yet

    def test_update_price_long_trailing_sl_triggers(self, config_no_dsl):
        """Long: price drops to hard floor → returns "trailing_sl"."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        tracker.positions[1] = pos
        # Price drops to hard floor: 50000 * (1 - 1.25/100) = 49375
        result = tracker.update_price(1, 49375.0)
        assert result == "trailing_sl"

    def test_update_price_long_trailing_sl_activates_and_ratchets(self, config_no_dsl):
        """Long: price reaches trigger → SL activates and ratchets up."""
        # Use trigger=0.5%, step=0.95%
        config_no_dsl.trailing_sl_trigger_pct = 0.5
        config_no_dsl.trailing_sl_step_pct = 0.95
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
        )
        tracker.positions[1] = pos
        
        # First: push price to trigger level: 50000 * (1 + 0.5/100) = 50250
        result = tracker.update_price(1, 50250.0)
        assert result is None  # No action yet, just activated
        assert pos.trailing_sl_activated is True
        assert pos.trailing_sl_level is not None

        # Second: price rises further, SL should ratchet up
        result = tracker.update_price(1, 50500.0)
        first_level = pos.trailing_sl_level
        result = tracker.update_price(1, 51000.0)
        second_level = pos.trailing_sl_level
        assert second_level > first_level  # Ratcheted up

        # Third: price drops but not to SL level → no trigger
        result = tracker.update_price(1, 50800.0)
        assert result is None
        assert pos.trailing_sl_level == second_level  # Unchanged

    def test_update_price_short_drops_hwm_updates(self, config_no_dsl):
        """Short: price drops, HWM updates (lower)."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
        )
        tracker.positions[2] = pos
        result = tracker.update_price(2, 2950.0)
        assert pos.high_water_mark == 2950.0
        assert result is None

    def test_update_price_short_trailing_sl_triggers(self, config_no_dsl):
        """Short: price rises to hard floor → returns "trailing_sl"."""
        tracker = PositionTracker(config_no_dsl)
        pos = TrackedPosition(
            market_id=2, symbol="ETH", side="short",
            entry_price=3000.0, size=1.0, high_water_mark=3000.0,
        )
        tracker.positions[2] = pos
        # Price rises to hard floor: 3000 * (1 + 1.25/100) = 3037.5
        result = tracker.update_price(2, 3037.5)
        assert result == "trailing_sl"


# ── update_price() — DSL mode ───────────────────────────────────────

class TestUpdatePriceDsl:
    """Tests for PositionTracker.update_price() in DSL mode."""

    def test_update_price_dsl_enabled_delegates_to_evaluate_dsl(self, config):
        """DSL enabled + dsl_state → delegates to evaluate_dsl (high_water_move_pct updated)."""
        tracker = PositionTracker(config)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            dsl_state=DSLState(
                side="long", entry_price=50000.0,
                leverage=10.0,
                high_water_price=50000.0,
                high_water_time=datetime.now(timezone.utc),
            ),
        )
        tracker.positions[1] = pos
        # Small price change: move=0.2%, below min tier trigger (0.3%), so no DSL actions
        result = tracker.update_price(1, 50100.0)
        assert result is None
        # Verify DSL state was updated (high_water_move_pct reflects the move)
        assert pos.dsl_state.high_water_move_pct == pytest.approx(0.2)  # (50100-50000)/50000*100 = 0.2%

    def test_update_price_dsl_has_trailing_sl_alongside(self, config):
        """DSL mode includes trailing SL evaluation after DSL."""
        tracker = PositionTracker(config)
        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=50000.0,
            dsl_state=DSLState(
                side="long", entry_price=50000.0,
                leverage=10.0,
                high_water_price=50000.0,
                high_water_time=datetime.now(timezone.utc),
            ),
        )
        tracker.positions[1] = pos
        
        # Price reaches trailing SL trigger but not DSL tier trigger
        # trailing_sl_trigger_pct=0.5%, entry=50000 → trigger at 50250
        # Note: 0.5% move = stagnation_move_pct threshold, so we mark stagnation_alerted
        # to prevent the first-time timer-start tuple from being returned
        pos._stagnation_alerted = True
        result = tracker.update_price(1, 50250.0)
        assert result is None  # Just activated, no exit yet
        assert pos.trailing_sl_activated is True

    def test_update_price_dsl_returns_tier_lock(self, config):
        """Returns "dsl_tier_lock" when DSL triggers tier lock."""
        tracker = PositionTracker(config)
        dsl_state = DSLState(
            side="long", entry_price=50000.0,
            leverage=10.0,
            high_water_price=51000.0,
            high_water_time=datetime.now(timezone.utc),
        )
        # Pre-set DSL state to simulate a position that already hit high tier
        dsl_state.high_water_move_pct = 1.5
        # Use DSLTier object directly (not dict from config)
        dsl_state.current_tier = DSLTier(
            trigger_pct=0.7, lock_hw_pct=40,
            trailing_buffer_pct=0.5, consecutive_breaches=3,
        )
        dsl_state.locked_floor_pct = 1.0  # locked at 1% move
        dsl_state.breach_count = 3

        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=51000.0,
            dsl_state=dsl_state,
        )
        tracker.positions[1] = pos

        # Price drops so move < locked_floor_pct (1%)
        # At entry=50000, move < 1% when price < 50500
        result = tracker.update_price(1, 49800.0)
        assert result is not None
        assert isinstance(result, tuple)
        assert result[0] == "dsl_tier_lock"

    def test_update_price_dsl_returns_stagnation_timer(self, config):
        """Returns ("dsl_stagnation_timer", {...}) when stagnation timer starts."""
        tracker = PositionTracker(config)
        dsl_state = DSLState(
            side="long", entry_price=50000.0,
            leverage=10.0,
            high_water_price=52000.0,
            high_water_time=datetime.now(timezone.utc),
        )
        # Pre-set: high_water_move_pct above min trigger, stagnation starts
        dsl_state.high_water_move_pct = 0.5  # above min tier trigger (0.3%)

        pos = TrackedPosition(
            market_id=1, symbol="BTC", side="long",
            entry_price=50000.0, size=0.1, high_water_mark=52000.0,
            dsl_state=dsl_state,
        )
        tracker.positions[1] = pos

        # Price movement causes high_water_move_pct update which triggers stagnation_started.
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

    def test_add_position_leverage_uses_config(self, config):
        """Leverage comes from config (not derived from equity)."""
        tracker = PositionTracker(config)
        tracker.account_equity = 1000.0
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        pos = tracker.positions[1]
        assert pos.dsl_state.leverage == config.dsl_leverage

    def test_add_position_leverage_falls_back_to_config(self, config):
        """Falls back to config leverage when equity = 0."""
        tracker = PositionTracker(config)
        tracker.account_equity = 0.0
        tracker.add_position(1, "BTC", "long", 50000.0, 0.1)
        pos = tracker.positions[1]
        assert pos.dsl_state.leverage == config.dsl_leverage

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