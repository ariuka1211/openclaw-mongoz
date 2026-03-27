"""
Tests for TrackedPosition and BotState data models.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.models import TrackedPosition, BotState


class TestTrackedPosition:
    """TrackedPosition dataclass tests."""

    def test_creation_required_fields(self):
        """TrackedPosition creation with required fields."""
        pos = TrackedPosition(
            market_id=1,
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            size=0.1,
            high_water_mark=50000.0,
        )
        assert pos.market_id == 1
        assert pos.symbol == "BTC"
        assert pos.side == "long"
        assert pos.entry_price == 50000.0
        assert pos.size == 0.1
        assert pos.high_water_mark == 50000.0

    def test_defaults(self):
        """TrackedPosition defaults (trailing_sl_activated=False, dsl_state=None, etc.)."""
        pos = TrackedPosition(
            market_id=1,
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            size=0.1,
            high_water_mark=50000.0,
        )
        assert pos.trailing_sl_activated is False  # New trailing SL field
        assert pos.trailing_sl_level is None
        assert pos.dsl_state is None
        assert pos.sl_pct is None
        assert pos.unverified_at is None
        assert pos.unverified_ticks == 0
        assert pos.active_sl_order_id is None

    def test_dsl_state_set_accessible(self, dsl_state_long):
        """TrackedPosition with dsl_state set → accessible."""
        pos = TrackedPosition(
            market_id=1,
            symbol="BTC",
            side="long",
            entry_price=100.0,
            size=0.5,
            high_water_mark=100.0,
            dsl_state=dsl_state_long,
        )
        assert pos.dsl_state is not None
        assert pos.dsl_state.entry_price == 100.0
        assert pos.dsl_state.leverage == 10.0
        assert pos.dsl_state.side == "long"

    def test_opened_at_defaults_to_nowish(self):
        """TrackedPosition opened_at defaults to now-ish."""
        before = datetime.now(timezone.utc)
        pos = TrackedPosition(
            market_id=1,
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            size=0.1,
            high_water_mark=50000.0,
        )
        after = datetime.now(timezone.utc)

        assert isinstance(pos.opened_at, datetime)
        assert pos.opened_at.tzinfo == timezone.utc
        assert before <= pos.opened_at <= after


class TestBotState:
    """BotState dataclass tests."""

    def test_creation_default_factories(self):
        """BotState creation → all default factories produce empty containers."""
        state = BotState()
        assert state.opened_signals == set()
        assert state.recently_closed == {}
        assert state.close_attempts == {}
        assert state.close_attempt_cooldown == {}
        assert state.dsl_close_attempts == {}
        assert state.dsl_close_attempt_cooldown == {}
        assert state.ai_close_cooldown == {}
        assert state.bot_managed_market_ids == set()
        assert state.pending_sync == set()
        assert state.verifying_close == set()
        assert state.api_lag_warnings == {}
        assert state.no_price_ticks == {}

    def test_opened_signals_is_set(self):
        """BotState opened_signals is a set (not list)."""
        state = BotState()
        assert isinstance(state.opened_signals, set)
        state.opened_signals.add(42)
        assert 42 in state.opened_signals
        state.opened_signals.add(42)  # dupes allowed in set
        assert len(state.opened_signals) == 1

    def test_recently_closed_is_dict(self):
        """BotState recently_closed is a dict."""
        state = BotState()
        assert isinstance(state.recently_closed, dict)
        state.recently_closed[1] = 50000.0
        assert state.recently_closed[1] == 50000.0

    def test_defaults_none_fields(self):
        """BotState optional string fields default to None."""
        state = BotState()
        assert state.last_signal_timestamp is None
        assert state.last_signal_hash is None
        assert state.last_ai_decision_ts is None
        assert state.saved_positions is None

    def test_defaults_scalar_fields(self):
        """BotState scalar defaults."""
        state = BotState()
        assert state.signal_processed_this_tick is False
        assert state.result_dirty is False
        assert state.last_order_time == 0
        assert state.idle_tick_count == 0
        assert state.kill_switch_active is False
        assert state.position_sync_failures == 0
