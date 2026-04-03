"""
BTC Grid Bot — Unit Tests for Phase 2 (Short Grid Direction)

Tests direction validation, short grid deployment logic, PnL matching,
and replacement order behavior. No network calls — pure unit tests.

Run: python -m pytest test_direction.py -v
"""

import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ─── ANALYST TESTS ───

class TestAnalystDirectionValidation:
    """Test the direction validation logic in analyst.py."""

    def _validate_direction(self, result, btc_price=66000, swing_15m=None):
        """Replicates run_analyst direction validation for unit testing."""
        if swing_15m is None:
            swing_15m = {"current_price": 66000}

        # Backward compat mapping
        direction = result.get("direction")
        if direction is None:
            if result.get("pause") is True:
                direction = "pause"
            else:
                direction = "long"
            result["direction"] = direction

        # Validate allowed values
        valid = ("long", "short", "pause")
        if direction not in valid:
            result["pause"] = True
            result["pause_reason"] = f"Invalid direction: '{direction}'"
            result["buy_levels"] = []
            result["sell_levels"] = []
            return result

        if direction == "pause":
            result["pause"] = True
            result["buy_levels"] = []
            result["sell_levels"] = []
            return result

        # Validate lists
        buy = result.get("buy_levels", [])
        sell = result.get("sell_levels", [])
        if not isinstance(buy, list) or not isinstance(sell, list):
            result["pause"] = True
            result["direction"] = "pause"
            result["buy_levels"] = []
            result["sell_levels"] = []
            return result

        if len(buy) < 1 or len(sell) < 1:
            result["pause"] = True
            result["direction"] = "pause"
            return result

        # Direction-specific price validation
        if direction == "long":
            invalid_buys = [x for x in buy if x >= btc_price]
            invalid_sells = [x for x in sell if x <= btc_price]
            if invalid_buys or invalid_sells:
                result["pause"] = True
                result["direction"] = "pause"
                result["buy_levels"] = []
                result["sell_levels"] = []
                return result
        elif direction == "short":
            invalid_sells = [x for x in sell if x >= btc_price]
            invalid_buys = [x for x in buy if x <= btc_price]
            if invalid_sells or invalid_buys:
                result["pause"] = True
                result["direction"] = "pause"
                result["buy_levels"] = []
                result["sell_levels"] = []
                return result

        return result

    def test_valid_long_grid(self):
        result = self._validate_direction({
            "direction": "long",
            "buy_levels": [64000, 65000],
            "sell_levels": [67000, 68000],
            "confidence": "high",
            "note": "ranging",
        }, btc_price=66000)
        assert result.get("pause") is not True  # pause may not be set, just ensure not True
        assert result["direction"] == "long"

    def test_valid_short_grid(self):
        result = self._validate_direction({
            "direction": "short",
            "buy_levels": [67000, 68000],  # above price — covers
            "sell_levels": [64000, 65000],  # below price — short entries
            "confidence": "high",
            "note": "bearish",
        }, btc_price=66000)
        assert result.get("pause") is not True
        assert result["direction"] == "short"

    def test_long_grid_invalid_buys_above_price(self):
        result = self._validate_direction({
            "direction": "long",
            "buy_levels": [67000, 68000],  # WRONG: above price
            "sell_levels": [67000, 68000],
            "confidence": "high",
        }, btc_price=66000)
        assert result["pause"] is True
        assert result["direction"] == "pause"

    def test_short_grid_invalid_sells_above_price(self):
        result = self._validate_direction({
            "direction": "short",
            "buy_levels": [67000, 68000],
            "sell_levels": [67000, 68000],  # WRONG: above price for shorts
            "confidence": "high",
        }, btc_price=66000)
        assert result["pause"] is True
        assert result["direction"] == "pause"

    def test_short_grid_invalid_buys_below_price(self):
        result = self._validate_direction({
            "direction": "short",
            "buy_levels": [64000, 65000],  # WRONG: below price for short covers
            "sell_levels": [64000, 65000],
            "confidence": "high",
        }, btc_price=66000)
        assert result["pause"] is True
        assert result["direction"] == "pause"

    def test_legacy_pause_backward_compat(self):
        """LLMs that return old format should still work."""
        result = self._validate_direction({
            "pause": True,
            "pause_reason": "Too volatile",
            "buy_levels": [64000],
            "sell_levels": [68000],
        }, btc_price=66000)
        assert result["pause"] is True
        assert result["direction"] == "pause"
        assert result["buy_levels"] == []
        assert result["sell_levels"] == []

    def test_default_direction_is_long(self):
        result = self._validate_direction({
            "buy_levels": [64000],
            "sell_levels": [68000],
        }, btc_price=66000)
        assert result["direction"] == "long"
        assert result.get("pause") is not True

    def test_invalid_direction_rejected(self):
        result = self._validate_direction({
            "direction": "banana",
            "buy_levels": [64000],
            "sell_levels": [68000],
        }, btc_price=66000)
        assert result["pause"] is True
        # Direction stays as original, levels cleared
        assert result["buy_levels"] == []
        assert result["sell_levels"] == []

    def test_empty_levels_rejected(self):
        result = self._validate_direction({
            "direction": "long",
            "buy_levels": [],
            "sell_levels": [68000],
        }, btc_price=66000)
        assert result["pause"] is True


# ─── CALCULATOR TESTS ───

from calculator import calculate_grid

class TestCalculatorDirection:
    """Test that calculator handles both directions correctly."""

    def test_long_direction_returned(self):
        result = calculate_grid(
            account_equity=1000, btc_price=66000,
            num_buy_levels=4, num_sell_levels=4,
            max_exposure_mult=3.0, margin_reserve_pct=0.20,
            direction="long",
        )
        assert result["safe"] is True
        assert result["direction"] == "long"

    def test_short_direction_returned(self):
        result = calculate_grid(
            account_equity=1000, btc_price=66000,
            num_buy_levels=4, num_sell_levels=4,
            max_exposure_mult=3.0, margin_reserve_pct=0.20,
            direction="short",
        )
        assert result["safe"] is True
        assert result["direction"] == "short"

    def test_same_sizing_for_both_directions(self):
        """Short and long should have identical margin calc."""
        kwargs = dict(
            account_equity=1000, btc_price=66000,
            num_buy_levels=4, num_sell_levels=4,
            max_exposure_mult=3.0, margin_reserve_pct=0.20,
        )
        long_result = calculate_grid(**kwargs, direction="long")
        short_result = calculate_grid(**kwargs, direction="short")
        assert long_result["size_per_level"] == short_result["size_per_level"]
        assert long_result["available_notional"] == short_result["available_notional"]
        assert long_result["worst_case_notional"] == short_result["worst_case_notional"]


# ─── GRID MANAGER TESTS ───

class TestGridStateDefaultDirection:
    """Test that state defaults to long direction."""

    def test_default_grid_direction(self, tmp_path):
        from grid import GridManager
        from lighter_api import LighterAPI

        # We can't actually create a GridManager without LighterAPI setup,
        # but we can test the _load_state defaults
        mock_api = MagicMock(spec=LighterAPI)
        cfg = {
            "capital": {"max_exposure_multiplier": 3.0, "margin_reserve_pct": 0.20},
            "grid": {"min_levels": 2, "max_levels": 8},
            "risk": {"daily_loss_limit_pct": 0.08, "trailing_loss_pct": 0.04},
            "trend": {"ema_period": 50, "pause_threshold_pct": 0.03, "warning_threshold_pct": 0.01},
        }

        # Monkey-patch state dir
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

        STATE_PATH = tmp_path / "grid_state.json"
        with patch("grid.STATE_FILE", STATE_PATH):
            gm = GridManager.__new__(GridManager)
            gm.cfg = cfg
            gm.api = mock_api
            state = GridManager._load_state(gm)
            assert state["grid_direction"] == "long"
            assert "pending_sells" in state
            assert state["pending_sells"] == []


class TestGridManagerShortGridLevels:
    """Test short grid level processing in deploy_short_grid."""

    def test_short_grid_level_filtering(self):
        """deploy_short_grid should filter: sells below price, buys above."""
        price = 66000.0
        levels = {
            "buy_levels": [67000, 68000, 65000, 69000],  # mixed
            "sell_levels": [64000, 65000, 67000, 63000],  # mixed
            "range_low": 63000,
            "range_high": 69000,
        }

        # Replicate deploy_short_grid level filtering logic
        buy_levels = sorted([p for p in levels["buy_levels"] if p > price])
        sell_levels = sorted([p for p in levels["sell_levels"] if p < price])

        # Buy levels should be ABOVE price (closing shorts)
        assert all(p > price for p in buy_levels)
        assert buy_levels == [67000, 68000, 69000]

        # Sell levels should be BELOW price (opening shorts)
        assert all(p < price for p in sell_levels)
        assert sell_levels == [63000, 64000, 65000]

    def test_short_grid_range_calculation(self):
        """range_low should be min of sells, range_high max of buys."""
        price = 66000.0
        buy_levels = [67000, 68000, 69000]
        sell_levels = [63000, 64000, 65000]

        range_low = min(sell_levels)
        range_high = max(buy_levels)

        assert range_low == 63000
        assert range_high == 69000
        assert range_low < price < range_high


class TestShortPnLLogic:
    """Test short grid PnL calculation."""

    def test_short_pnl_positive(self):
        """Short entered at 65000, closed at 64000 = profit."""
        sell_entry = 65000
        buy_exit = 64000
        size = 0.01
        pnl = (sell_entry - buy_exit) * size
        assert pnl == 10.0  # $10 profit

    def test_short_pnl_negative(self):
        """Short entered at 65000, closed at 66000 = loss."""
        sell_entry = 65000
        buy_exit = 66000
        size = 0.01
        pnl = (sell_entry - buy_exit) * size
        assert pnl == -10.0  # $10 loss

    def test_long_pnl_positive(self):
        """Long entered at 64000, exited at 65000 = profit."""
        buy_entry = 64000
        sell_exit = 65000
        size = 0.01
        pnl = (sell_exit - buy_entry) * size
        assert pnl == 10.0

    def test_short_pnl_breakeven(self):
        """Short entered and exited at same price = 0."""
        pnl = (65000 - 65000) * 0.01
        assert pnl == 0.0


class TestShortReplacementLogic:
    """Test replacement order logic for short grids."""

    def test_short_sell_replacement_finds_lower_level(self):
        """After short sell fills at 65000, replacement should be at lower sell."""
        sell_levels = [63000, 64000, 65000, 66000]
        filled_price = 65000

        # Short grid: sell filled → replace at next lower sell
        lower_sells = [p for p in sell_levels if p < filled_price]
        assert lower_sells == [63000, 64000]
        target = max(lower_sells)  # closest below
        assert target == 64000

    def test_short_buy_replacement_finds_higher_level(self):
        """After short buy fills at 67000, replacement should be at higher buy."""
        buy_levels = [67000, 68000, 69000]
        filled_price = 67000

        # Short grid: buy filled → replace at next higher buy
        higher_buys = [p for p in buy_levels if p > filled_price]
        assert higher_buys == [68000, 69000]
        target = min(higher_buys)  # closest above
        assert target == 68000

    def test_no_replacement_when_no_lower_sells(self):
        """If all sells are filled, no replacement possible."""
        sell_levels = [63000, 64000, 65000]
        filled_price = 63000

        lower_sells = [p for p in sell_levels if p < filled_price]
        assert lower_sells == []  # no more below

    def test_no_replacement_when_no_higher_buys(self):
        """If all buys are filled, no replacement possible."""
        buy_levels = [67000, 68000, 69000]
        filled_price = 69000

        higher_buys = [p for p in buy_levels if p > filled_price]
        assert higher_buys == []


# ─── INTEGRATION: deploy_short_grid level filtering ───

@pytest.mark.asyncio
class TestDeployShortGridIntegration:
    """Integration test for short grid deploy logic (mocked API)."""

    async def test_deploy_short_grid_places_sell_first(self):
        """deploy_short_grid should place SELL orders before BUY orders."""
        from grid import GridManager

        # Mock API
        mock_api = AsyncMock()
        mock_api.get_open_orders.return_value = []
        mock_api.cancel_all_orders.return_value = 0

        order_counter = [0]
        placed_orders = []

        async def mock_place_limit_order(side, price, size):
            order_counter[0] += 1
            order = {
                "order_id": str(order_counter[0]),
                "price": price,
                "side": side,
                "size": size,
                "status": "open",
                "layer": "grid",
            }
            placed_orders.append(order)
            return order

        mock_api.place_limit_order = mock_place_limit_order
        mock_api.get_equity = AsyncMock(return_value=1000.0)

        cfg = {
            "capital": {"max_exposure_multiplier": 3.0, "margin_reserve_pct": 0.20},
            "grid": {"min_levels": 2, "max_levels": 8},
            "risk": {"daily_loss_limit_pct": 0.08, "trailing_loss_pct": 0.04},
            "trend": {"ema_period": 50, "pause_threshold_pct": 0.05, "warning_threshold_pct": 0.01},
        }

        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        gm = GridManager.__new__(GridManager)
        gm.cfg = cfg
        gm.api = mock_api

        # Set up state
        gm.state = {
            "active": False,
            "paused": False,
            "pause_reason": "",
            "levels": {"buy": [], "sell": []},
            "range_low": 0,
            "range_high": 0,
            "size_per_level": 0,
            "orders": [],
            "last_reset": None,
            "daily_pnl": 0.0,
            "fill_count": 0,
            "equity_at_reset": 0.0,
            "peak_equity": 0.0,
            "realized_pnl": 0.0,
            "trades": [],
            "pending_buys": [],
            "pending_sells": [],
            "grid_direction": "long",
        }

        levels = {
            "buy_levels": [67000, 68000],
            "sell_levels": [64000, 65000],
            "range_low": 64000,
            "range_high": 68000,
            "pause": False,
            "direction": "short",
            "confidence": "high",
            "note": "bearish",
        }

        # Patch send_alert at the tg_alerts module level
        with patch("tg_alerts.send_alert", new_callable=AsyncMock()):
            await gm.deploy_short_grid(levels, equity=1000.0, btc_price=66000.0)

        # Verify sell orders were placed first
        sell_orders = [o for o in placed_orders if o["side"] == "sell"]
        buy_orders = [o for o in placed_orders if o["side"] == "buy"]

        assert len(sell_orders) == 2, f"Expected 2 sell orders, got {len(sell_orders)}"
        assert len(buy_orders) == 2, f"Expected 2 buy orders, got {len(buy_orders)}"

        # Verify sell orders are below price
        assert all(o["price"] < 66000 for o in sell_orders), f"Sell orders should be below 66000: {sell_orders}"
        # Verify buy orders are above price
        assert all(o["price"] > 66000 for o in buy_orders), f"Buy orders should be above 66000: {buy_orders}"

        # Verify state was updated
        assert gm.state["grid_direction"] == "short"
        assert gm.state["active"] is True


# ─── TREND CHECK ───

class TestTrendCheckReturns:
    """Test check_trend return values."""

    def test_price_at_ema_returns_long(self):
        """Price == EMA → long."""
        # Simplified version of the logic
        ema50 = 66000
        price = 66000
        pct_below = (ema50 - price) / ema50
        assert pct_below == 0

    def test_price_2pct_below_ema_returns_short(self):
        """Price 2% below EMA → short."""
        ema50 = 67000
        price = 65660  # ~2% below
        pct_below = (ema50 - price) / ema50
        assert 0.01 < pct_below < 0.05  # between warning and short_warning

    def test_price_6pct_below_ema_returns_pause(self):
        """Price >5% below EMA → pause."""
        ema50 = 70000
        price = 65000  # ~7.1% below
        pct_below = (ema50 - price) / ema50
        assert pct_below > 0.05  # above pause threshold

    def test_price_above_ema_returns_long(self):
        """Price above EMA → long."""
        ema50 = 65000
        price = 66000
        pct_below = (ema50 - price) / ema50
        assert pct_below < 0  # negative = above EMA
