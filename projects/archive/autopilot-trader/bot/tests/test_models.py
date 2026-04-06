"""
Tests for TrackedPosition data model.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.models import TrackedPosition


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
