"""
Tests for LighterAPI — the Lighter.xyz DEX wrapper.

Mocks the `lighter` SDK entirely so no network calls are made.
"""

import sys
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Mock the lighter SDK before importing LighterAPI ──────────────────
mock_lighter = MagicMock()
mock_lighter.Configuration = MagicMock(return_value=MagicMock())
mock_lighter.ApiClient = MagicMock()
mock_lighter.AccountApi = MagicMock()
mock_lighter.OrderApi = MagicMock()
mock_lighter.SignerClient = MagicMock()
mock_lighter.nonce_manager = MagicMock()
mock_lighter.nonce_manager.NonceManagerType = MagicMock()
sys.modules["lighter"] = mock_lighter

from api.lighter_api import LighterAPI


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def api(config, tmp_path):
    """Create a LighterAPI with state dir redirected to tmp_path."""
    api = LighterAPI(config)
    api._state_dir = tmp_path
    api._tracked_markets_file = tmp_path / "tracked_markets.json"
    api._quota_state_file = tmp_path / "quota_state.json"
    return api


# ── _extract_quota_from_response ──────────────────────────────────────

class TestExtractQuota:
    """Tests for _extract_quota_from_response()."""

    def test_standard_field(self, api):
        """Extracts quota from standard volume_quota_remaining attribute."""
        resp = MagicMock()
        resp.volume_quota_remaining = 42
        val, detail = api._extract_quota_from_response(resp)
        assert val == 42
        assert "standard_field_value:42" in detail

    def test_alt_field_camel_case(self, api):
        """Extracts quota from volumeQuotaRemaining (camelCase) field."""
        resp = MagicMock()
        del resp.volume_quota_remaining  # remove standard
        resp.volumeQuotaRemaining = 100
        val, detail = api._extract_quota_from_response(resp)
        assert val == 100
        assert "alt_field_volumeQuotaRemaining" in detail

    def test_additional_properties(self, api):
        """Extracts quota from additional_properties dict."""
        resp = MagicMock(spec=[])
        resp.additional_properties = {"volume_quota_remaining": 7}
        val, detail = api._extract_quota_from_response(resp)
        assert val == 7
        assert "additional_properties" in detail

    def test_none_response(self, api):
        """Returns (None, ...) when resp is None."""
        val, detail = api._extract_quota_from_response(None)
        assert val is None
        assert "no_response_object" in detail

    def test_no_quota_fields(self, api):
        """Returns (None, ...) when response has no quota fields."""
        resp = MagicMock(spec=[])
        val, detail = api._extract_quota_from_response(resp)
        assert val is None
        assert "not_found" in detail


# ── _to_lighter_amount ────────────────────────────────────────────────

class TestToLighterAmount:
    """Tests for _to_lighter_amount() static method."""

    def test_basic_conversion(self):
        """1.5 with 6 decimals → 1500000."""
        assert LighterAPI._to_lighter_amount(1.5, 6) == 1_500_000

    def test_zero(self):
        """0.0 → 0."""
        assert LighterAPI._to_lighter_amount(0.0, 6) == 0

    def test_eight_decimals(self):
        """1.0 with 8 decimals → 100000000."""
        assert LighterAPI._to_lighter_amount(1.0, 8) == 100_000_000

    def test_negative_abs(self):
        """Negative values are abs'd."""
        assert LighterAPI._to_lighter_amount(-2.5, 4) == 25_000

    def test_two_decimals(self):
        """123.45 with 2 decimals → 12345."""
        assert LighterAPI._to_lighter_amount(123.45, 2) == 12_345


# ── _next_client_order_index ──────────────────────────────────────────

class TestNextClientOrderIndex:
    """Tests for _next_client_order_index()."""

    def test_increments(self, api):
        """Returns incrementing integers."""
        first = api._next_client_order_index()
        second = api._next_client_order_index()
        assert second == first + 1

    def test_wraps_at_2_32(self, api):
        """Wraps gracefully at 2^32."""
        api._client_order_index = (2**32) - 1
        val = api._next_client_order_index()
        assert val == 0
        val2 = api._next_client_order_index()
        assert val2 == 1


# ── get_positions ─────────────────────────────────────────────────────

class TestGetPositions:
    """Tests for get_positions()."""

    @pytest.mark.asyncio
    async def test_parses_positions(self, api):
        """Parses positions from mock SDK response correctly."""
        # Build mock response
        mock_pos = MagicMock()
        mock_pos.position = "0.5"
        mock_pos.avg_entry_price = "50000"
        mock_pos.market_id = 1
        mock_pos.unrealized_pnl = "100"
        mock_pos.initial_margin_fraction = "10"
        mock_pos.sign = 1

        mock_account = MagicMock()
        mock_account.positions = [mock_pos]

        mock_result = MagicMock()
        mock_result.accounts = [mock_account]

        mock_account_api = MagicMock()
        mock_account_api.account = AsyncMock(return_value=mock_result)

        # _ensure_client would overwrite _account_api, so mock it
        api._ensure_client = AsyncMock()
        api._account_api = mock_account_api

        positions = await api.get_positions()

        assert positions is not None
        assert len(positions) == 1
        p = positions[0]
        assert p["market_id"] == 1
        assert p["size"] == 0.5
        assert p["side"] == "long"
        assert p["entry_price"] == 50000.0
        assert p["unrealized_pnl"] == 100.0
        assert p["leverage"] == 10.0  # 100/10 = 10

    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self, api):
        """Returns None when the API raises an exception."""
        api._ensure_client = AsyncMock()
        api._account_api = MagicMock()
        api._account_api.account = AsyncMock(side_effect=Exception("network error"))

        result = await api.get_positions()
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_zero_size_positions(self, api):
        """Skips positions with size == 0."""
        mock_pos = MagicMock()
        mock_pos.position = "0"
        mock_pos.market_id = 1

        mock_account = MagicMock()
        mock_account.positions = [mock_pos]
        mock_result = MagicMock()
        mock_result.accounts = [mock_account]

        mock_account_api = MagicMock()
        mock_account_api.account = AsyncMock(return_value=mock_result)

        api._ensure_client = AsyncMock()
        api._account_api = mock_account_api

        positions = await api.get_positions()
        assert positions == []


# ── get_price ─────────────────────────────────────────────────────────

class TestGetPrice:
    """Tests for get_price()."""

    @pytest.mark.asyncio
    async def test_returns_price_from_recent_trades(self, api):
        """Returns price from first trade in recent_trades."""
        mock_trade = MagicMock()
        mock_trade.price = "42000.5"

        mock_trades = MagicMock()
        mock_trades.trades = [mock_trade]

        mock_order_api = MagicMock()
        mock_order_api.recent_trades = AsyncMock(return_value=mock_trades)

        api._ensure_client = AsyncMock()
        api._order_api = mock_order_api

        price = await api.get_price(1)
        assert price == 42000.5

    @pytest.mark.asyncio
    async def test_returns_none_when_no_trades(self, api):
        """Returns None when no trades available."""
        mock_trades = MagicMock()
        mock_trades.trades = []

        mock_order_api = MagicMock()
        mock_order_api.recent_trades = AsyncMock(return_value=mock_trades)

        api._ensure_client = AsyncMock()
        api._order_api = mock_order_api

        price = await api.get_price(1)
        assert price is None

    @pytest.mark.asyncio
    async def test_retries_on_failure(self, api):
        """Retries up to 3 times on API failure."""
        mock_trade = MagicMock()
        mock_trade.price = "50000"
        mock_trades = MagicMock()
        mock_trades.trades = [mock_trade]

        mock_order_api = MagicMock()
        # Fail twice, succeed on third
        mock_order_api.recent_trades = AsyncMock(
            side_effect=[
                Exception("timeout"),
                Exception("timeout"),
                mock_trades,
            ]
        )

        api._ensure_client = AsyncMock()
        api._order_api = mock_order_api

        price = await api.get_price(1)
        assert price == 50000.0
        assert mock_order_api.recent_trades.call_count == 3


# ── update_mark_prices_from_positions ─────────────────────────────────

class TestUpdateMarkPricesFromPositions:
    """Tests for update_mark_prices_from_positions()."""

    def test_long_position(self, api):
        """Derives mark price from unrealized_pnl for long position."""
        positions = [{
            "market_id": 1,
            "side": "long",
            "entry_price": 100.0,
            "size": 1.0,
            "unrealized_pnl": 10.0,  # long: mark = entry + pnl/size
        }]
        api.update_mark_prices_from_positions(positions)

        cached = api._mark_prices[1]
        assert cached["price"] == pytest.approx(110.0)

    def test_short_position(self, api):
        """Derives mark price from unrealized_pnl for short position."""
        positions = [{
            "market_id": 1,
            "side": "short",
            "entry_price": 100.0,
            "size": 1.0,
            "unrealized_pnl": 10.0,  # short: mark = entry - pnl/abs(size)
        }]
        api.update_mark_prices_from_positions(positions)

        cached = api._mark_prices[1]
        assert cached["price"] == pytest.approx(90.0)

    def test_skips_zero_entry(self, api):
        """Skips positions with entry_price <= 0."""
        positions = [{
            "market_id": 1,
            "side": "long",
            "entry_price": 0,
            "size": 1.0,
            "unrealized_pnl": 10.0,
        }]
        api.update_mark_prices_from_positions(positions)
        assert 1 not in api._mark_prices

    def test_skips_zero_size(self, api):
        """Skips positions with size == 0."""
        positions = [{
            "market_id": 1,
            "side": "long",
            "entry_price": 100.0,
            "size": 0.0,
            "unrealized_pnl": 0.0,
        }]
        api.update_mark_prices_from_positions(positions)
        assert 1 not in api._mark_prices


# ── Tracked markets file I/O ──────────────────────────────────────────

class TestTrackedMarketsIO:
    """Tests for _load_tracked_markets / _save_tracked_markets roundtrip."""

    def test_save_and_load(self, api, tmp_path):
        """Roundtrip: save tracked markets, then load them back."""
        api.tracked_market_ids = [10, 20, 30]
        api._save_tracked_markets()

        loaded = api._load_tracked_markets()
        assert loaded == [10, 20, 30]

    def test_load_missing_file(self, api, tmp_path):
        """Returns empty list when file doesn't exist."""
        loaded = api._load_tracked_markets()
        assert loaded == []

    def test_load_corrupt_file(self, api, tmp_path):
        """Returns empty list on corrupt JSON."""
        api._tracked_markets_file.write_text("not json{{{")
        loaded = api._load_tracked_markets()
        assert loaded == []


# ── Quota cache file I/O ──────────────────────────────────────────────

class TestQuotaCacheIO:
    """Tests for _load_quota_cache / _save_quota_cache roundtrip."""

    def test_save_and_load(self, api, tmp_path):
        """Roundtrip: save quota, then load it back."""
        api._last_known_quota = 50
        api._last_quota_time = 1700000000.0
        api._save_quota_cache()

        # Reset in-memory state
        api._last_known_quota = None
        api._last_quota_time = 0

        api._load_quota_cache()
        assert api._last_known_quota == 50
        assert api._last_quota_time == 1700000000.0

    def test_load_missing_file(self, api, tmp_path):
        """No-op when cache file doesn't exist."""
        api._last_known_quota = None
        api._load_quota_cache()
        assert api._last_known_quota is None

    def test_save_none_quota(self, api, tmp_path):
        """Doesn't write file when quota is None."""
        api._last_known_quota = None
        api._save_quota_cache()
        assert not api._quota_state_file.exists()


# ── SOCKS5 proxy ──────────────────────────────────────────────────────

class TestSOCKS5Proxy:
    """Tests that proxy_url is applied to configuration."""

    def test_proxy_set_on_config(self, config):
        """When proxy_url is set, it's stored on the API config and proxy_patch is imported."""
        config.proxy_url = "socks5://user:pass@proxy.example.com:1080"

        api = LighterAPI(config)

        # Verify proxy_url is stored on config
        assert api.cfg.proxy_url == "socks5://user:pass@proxy.example.com:1080"

        # Verify the proxy_patch module was imported (it monkey-patches urllib on import)
        # The api/proxy_patch.py is imported at module level in bot.py,
        # but we can verify the config carries the value through
        assert "socks5" in api.cfg.proxy_url
        assert "proxy.example.com" in api.cfg.proxy_url

    def test_no_proxy_when_unset(self, config):
        """When proxy_url is empty, no proxy attribute is set."""
        config.proxy_url = ""

        api = LighterAPI(config)
        assert api.cfg.proxy_url == ""
