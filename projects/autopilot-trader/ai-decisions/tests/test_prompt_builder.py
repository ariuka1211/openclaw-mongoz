"""Tests for context/prompt_builder.py — PromptBuilder class"""
import pytest

from context.prompt_builder import PromptBuilder


class TestCalcRoe:
    def test_long_position_in_profit(self):
        position = {
            "entry_price": 100.0,
            "current_price": 110.0,
            "side": "long",
            "position_size_usd": 500.0,
        }
        roe = PromptBuilder._calc_roe(position, equity=1000)
        # raw = (110-100)/100 * 100 = 10%
        # effective_lev = 500/1000 = 0.5
        # roe = 10 * 0.5 = 5.0%
        assert roe == pytest.approx(5.0, abs=0.01)

    def test_short_position_in_profit(self):
        position = {
            "entry_price": 100.0,
            "current_price": 90.0,
            "side": "short",
            "position_size_usd": 500.0,
        }
        roe = PromptBuilder._calc_roe(position, equity=1000)
        # raw = (90-100)/100 * 100 = -10%, short flips → 10%
        # effective_lev = 500/1000 = 0.5
        # roe = 10 * 0.5 = 5.0%
        assert roe == pytest.approx(5.0, abs=0.01)

    def test_zero_entry_price(self):
        position = {
            "entry_price": 0,
            "current_price": 100.0,
            "side": "long",
            "position_size_usd": 500.0,
        }
        assert PromptBuilder._calc_roe(position, equity=1000) == 0.0

    def test_cross_margin_uses_notional_over_equity(self):
        position = {
            "entry_price": 100.0,
            "current_price": 105.0,
            "side": "long",
            "position_size_usd": 2000.0,
        }
        roe = PromptBuilder._calc_roe(position, equity=1000)
        # raw = (105-100)/100 * 100 = 5%
        # effective_lev = 2000/1000 = 2.0
        # roe = 5 * 2.0 = 10.0%
        assert roe == pytest.approx(10.0, abs=0.01)

    def test_isolated_margin_fallback_no_equity(self):
        position = {
            "entry_price": 100.0,
            "current_price": 110.0,
            "side": "long",
            "position_size_usd": 0,
            "leverage": 5.0,
        }
        roe = PromptBuilder._calc_roe(position, equity=0)
        # raw = 10%, notional=0 so falls through to isolated margin
        # roe = 10 * 5.0 = 50.0%
        assert roe == pytest.approx(50.0, abs=0.01)

    def test_isolated_margin_fallback_no_equity_using_size_usd(self):
        """Test fallback to size_usd field when position_size_usd not set."""
        position = {
            "entry_price": 200.0,
            "current_price": 210.0,
            "side": "long",
            "size_usd": 1000.0,
            "leverage": 3.0,
        }
        # equity=0 → notional check fails → isolated margin fallback
        roe = PromptBuilder._calc_roe(position, equity=0)
        # raw = (210-200)/200 * 100 = 5%
        # roe = 5 * 3.0 = 15.0%
        assert roe == pytest.approx(15.0, abs=0.01)


class TestBuildPrompt:
    @pytest.fixture
    def mock_trader(self):
        from unittest.mock import MagicMock
        trader = MagicMock()
        trader.config = {"safety": {"max_positions": 8}}
        trader.pattern_engine.decay_patterns.return_value = None
        trader.pattern_engine.build_section.return_value = ""
        trader.stats_formatter.build_live_stats_section.return_value = ""
        trader.stats_formatter.build_hold_regret_section.return_value = ""
        trader.db.get_performance_stats.return_value = {
            "win_rate": 50, "total_trades": 10, "wins": 5, "avg_win": 50, "avg_loss": -30,
        }
        trader.db.get_daily_pnl.return_value = 100.0
        trader.db.get_recent_decisions.return_value = []
        return trader

    def test_build_prompt_returns_nonempty_string(self, mock_trader):
        builder = PromptBuilder(mock_trader)
        signals = [{"symbol": "BTC-USDT", "compositeScore": 80, "direction": "long",
                     "safetyPass": True, "fundingSpread8h": 0.01, "dailyVolumeUsd": 500000, "dailyPriceChange": 2.5}]
        positions = []
        history = []
        outcomes = []
        config = {"accountEquity": 1000}
        result = builder.build_prompt(signals, positions, history, outcomes, config)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Open Positions" in result
        assert "Market Opportunities" in result
        assert "Account" in result

    def test_build_prompt_with_positions(self, mock_trader):
        builder = PromptBuilder(mock_trader)
        signals = []
        positions = [{"symbol": "BTC-USDT", "side": "long", "entry_price": 50000,
                       "current_price": 51000, "position_size_usd": 1000}]
        history = []
        outcomes = []
        config = {"accountEquity": 1000}
        result = builder.build_prompt(signals, positions, history, outcomes, config)
        assert "BTC-USDT" in result
        assert "LONG" in result
