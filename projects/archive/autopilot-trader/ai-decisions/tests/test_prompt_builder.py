"""Tests for context/prompt_builder.py — PromptBuilder class"""
import pytest

from context.prompt_builder import PromptBuilder


class TestCalcMovePct:
    def test_long_position_in_profit(self):
        position = {
            "entry_price": 100.0,
            "current_price": 110.0,
            "side": "long",
            "position_size_usd": 500.0,
        }
        move_pct = PromptBuilder._calc_move_pct(position, equity=1000)
        # raw move = (110-100)/100 * 100 = 10%
        assert move_pct == pytest.approx(10.0, abs=0.01)

    def test_short_position_in_profit(self):
        position = {
            "entry_price": 100.0,
            "current_price": 90.0,
            "side": "short",
            "position_size_usd": 500.0,
        }
        move_pct = PromptBuilder._calc_move_pct(position, equity=1000)
        # raw move = (100-90)/100 * 100 = 10% (profitable for short)
        assert move_pct == pytest.approx(10.0, abs=0.01)

    def test_zero_entry_price(self):
        position = {
            "entry_price": 0,
            "current_price": 100.0,
            "side": "long",
            "position_size_usd": 500.0,
        }
        assert PromptBuilder._calc_move_pct(position, equity=1000) == 0.0

    def test_cross_margin_move_pct(self):
        position = {
            "entry_price": 100.0,
            "current_price": 105.0,
            "side": "long",
            "position_size_usd": 2000.0,
        }
        move_pct = PromptBuilder._calc_move_pct(position, equity=1000)
        # raw move = (105-100)/100 * 100 = 5%
        assert move_pct == pytest.approx(5.0, abs=0.01)

    def test_isolated_margin_fallback_no_equity(self):
        position = {
            "entry_price": 100.0,
            "current_price": 110.0,
            "side": "long",
            "position_size_usd": 0,
        }
        move_pct = PromptBuilder._calc_move_pct(position, equity=0)
        # raw move = (110-100)/100 * 100 = 10%
        assert move_pct == pytest.approx(10.0, abs=0.01)

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
        move_pct = PromptBuilder._calc_move_pct(position, equity=0)
        # raw move = (210-200)/200 * 100 = 5%
        assert move_pct == pytest.approx(5.0, abs=0.01)


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
        trader.data_reader.read_equity.return_value = 1000.0
        return trader

    def test_build_prompt_returns_nonempty_string(self, mock_trader):
        builder = PromptBuilder(mock_trader)
        signals = [{"symbol": "BTC-USDT", "compositeScore": 80, "direction": "long",
                     "fundingSpread8h": 0.01, "dailyVolumeUsd": 500000, "dailyPriceChange": 2.5}]
        positions = []
        history = []
        outcomes = []
        config = {}
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
        config = {}
        result = builder.build_prompt(signals, positions, history, outcomes, config)
        assert "BTC-USDT" in result
        assert "LONG" in result
