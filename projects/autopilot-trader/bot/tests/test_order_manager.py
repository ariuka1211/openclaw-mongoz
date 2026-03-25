"""
Tests for core.order_manager.OrderManager
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.order_manager import OrderManager


def _make_order_manager(config, mock_api, mock_bot):
    """Create an OrderManager with tracker and bot_managed_market_ids wired up."""
    om = OrderManager(config, mock_api, mock_bot)
    om.tracker = mock_bot.tracker
    # _prune_caches references self.bot_managed_market_ids (set externally in bot.py)
    om.bot_managed_market_ids = mock_bot.bot_managed_market_ids
    return om


# ── _prune_caches() ─────────────────────────────────────────────────

class TestPruneCaches:
    """Tests for OrderManager._prune_caches()."""

    def test_prune_caches_expired_ai_close_cooldown(self, config, mock_api, mock_bot):
        """Expired ai_close_cooldown entries removed."""
        om = _make_order_manager(config, mock_api, mock_bot)
        now = time.monotonic()
        mock_bot._ai_close_cooldown = {
            "BTC": now - 100,   # expired
            "ETH": now + 1000,  # valid
        }
        om._prune_caches()
        assert "BTC" not in mock_bot._ai_close_cooldown
        assert "ETH" in mock_bot._ai_close_cooldown

    def test_prune_caches_expired_recently_closed(self, config, mock_api, mock_bot):
        """Expired recently_closed entries removed."""
        om = _make_order_manager(config, mock_api, mock_bot)
        now = time.monotonic()
        mock_bot._recently_closed = {
            1: now - 100,   # expired
            2: now + 1000,  # valid
        }
        om._prune_caches()
        assert 1 not in mock_bot._recently_closed
        assert 2 in mock_bot._recently_closed

    def test_prune_caches_expired_close_attempt_cooldown(self, config, mock_api, mock_bot):
        """Expired close_attempt_cooldown entries removed."""
        om = _make_order_manager(config, mock_api, mock_bot)
        now = time.monotonic()
        mock_bot._close_attempt_cooldown = {
            "BTC": now - 100,   # expired
            "ETH": now + 1000,  # valid
        }
        om._prune_caches()
        assert "BTC" not in mock_bot._close_attempt_cooldown
        assert "ETH" in mock_bot._close_attempt_cooldown

    def test_prune_caches_close_attempts_without_cooldown_pruned(self, config, mock_api, mock_bot):
        """close_attempts for symbols without cooldown are pruned."""
        om = _make_order_manager(config, mock_api, mock_bot)
        now = time.monotonic()
        # BTC has active cooldown, ETH does not
        mock_bot._close_attempt_cooldown = {"BTC": now + 1000}
        mock_bot._close_attempts = {"BTC": 2, "ETH": 1, "SOL": 3}
        om._prune_caches()
        # BTC stays (cooldown active), ETH and SOL pruned (no cooldown)
        assert "BTC" in mock_bot._close_attempts
        assert "ETH" not in mock_bot._close_attempts
        assert "SOL" not in mock_bot._close_attempts

    def test_prune_caches_expired_api_lag_warnings(self, config, mock_api, mock_bot):
        """Expired api_lag_warnings (> 1hr) removed."""
        om = _make_order_manager(config, mock_api, mock_bot)
        now = time.monotonic()
        mock_bot._api_lag_warnings = {
            "BTC": now - 4000,   # > 3600s, expired
            "ETH": now - 1000,   # < 3600s, valid
        }
        om._prune_caches()
        assert "BTC" not in mock_bot._api_lag_warnings
        assert "ETH" in mock_bot._api_lag_warnings

    def test_prune_caches_orphaned_no_price_ticks(self, config, mock_api, mock_bot):
        """Orphaned no_price_ticks (position no longer tracked) removed."""
        om = _make_order_manager(config, mock_api, mock_bot)
        # Add a position to tracker
        mock_bot.tracker.positions[1] = object()  # dummy
        # no_price_ticks has tracked (1) and orphaned (999) entries
        mock_bot._no_price_ticks = {1: 5, 999: 10}
        om._prune_caches()
        assert 1 in mock_bot._no_price_ticks
        assert 999 not in mock_bot._no_price_ticks

    def test_prune_caches_stale_bot_managed_market_ids(self, config, mock_api, mock_bot):
        """Stale bot_managed_market_ids pruned."""
        om = _make_order_manager(config, mock_api, mock_bot)
        # Add position 1 to tracker (managed), 2 is stale (not tracked, not recently closed)
        mock_bot.tracker.positions[1] = object()
        mock_bot._recently_closed = {}
        mock_bot.bot_managed_market_ids = {1, 2}
        om.bot_managed_market_ids = mock_bot.bot_managed_market_ids
        om._prune_caches()
        assert 1 in mock_bot.bot_managed_market_ids
        assert 2 not in mock_bot.bot_managed_market_ids


# ── _should_pace_orders() ───────────────────────────────────────────

class TestShouldPaceOrders:
    """Tests for OrderManager._should_pace_orders()."""

    def test_should_pace_orders_quota_low_recent_order(self, config, mock_api, mock_bot):
        """Quota < 35 and last order < 16s ago → True."""
        mock_api.volume_quota_remaining = 30
        mock_bot._last_order_time = time.time() - 10  # 10s ago
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_pace_orders() is True

    def test_should_pace_orders_quota_low_old_order(self, config, mock_api, mock_bot):
        """Quota < 35 and last order > 16s ago → False."""
        mock_api.volume_quota_remaining = 30
        mock_bot._last_order_time = time.time() - 20  # 20s ago
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_pace_orders() is False

    def test_should_pace_orders_quota_sufficient(self, config, mock_api, mock_bot):
        """Quota >= 35 → False (no pacing)."""
        mock_api.volume_quota_remaining = 50
        mock_bot._last_order_time = time.time() - 5  # 5s ago
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_pace_orders() is False


# ── _should_skip_open_for_quota() ───────────────────────────────────

class TestShouldSkipOpenForQuota:
    """Tests for OrderManager._should_skip_open_for_quota()."""

    def test_should_skip_open_quota_low(self, config, mock_api, mock_bot):
        """Quota < 35 → True."""
        mock_api.volume_quota_remaining = 20
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_skip_open_for_quota() is True

    def test_should_skip_open_quota_sufficient(self, config, mock_api, mock_bot):
        """Quota >= 35 → False."""
        mock_api.volume_quota_remaining = 50
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_skip_open_for_quota() is False

    def test_should_skip_open_quota_none(self, config, mock_api, mock_bot):
        """Quota is None → False."""
        mock_api.volume_quota_remaining = None
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_skip_open_for_quota() is False


# ── _is_quota_emergency() ───────────────────────────────────────────

class TestIsQuotaEmergency:
    """Tests for OrderManager._is_quota_emergency()."""

    def test_is_quota_emergency_quota_low(self, config, mock_api, mock_bot):
        """Quota < 5 → True."""
        mock_api.volume_quota_remaining = 3
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._is_quota_emergency() is True

    def test_is_quota_emergency_quota_sufficient(self, config, mock_api, mock_bot):
        """Quota >= 5 → False."""
        mock_api.volume_quota_remaining = 10
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._is_quota_emergency() is False

    def test_is_quota_emergency_quota_none(self, config, mock_api, mock_bot):
        """Quota is None → False."""
        mock_api.volume_quota_remaining = None
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._is_quota_emergency() is False


# ── _should_skip_non_critical_orders() ──────────────────────────────

class TestShouldSkipNonCriticalOrders:
    """Tests for OrderManager._should_skip_non_critical_orders()."""

    def test_should_skip_non_critical_emergency_mode(self, config, mock_api, mock_bot):
        """Emergency mode → True."""
        mock_api.volume_quota_remaining = 3
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_skip_non_critical_orders() is True

    def test_should_skip_non_critical_normal_mode(self, config, mock_api, mock_bot):
        """Normal mode → False."""
        mock_api.volume_quota_remaining = 50
        om = _make_order_manager(config, mock_api, mock_bot)
        assert om._should_skip_non_critical_orders() is False
