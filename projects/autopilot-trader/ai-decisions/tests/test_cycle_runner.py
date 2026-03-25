"""Tests for cycle_runner.py — CycleRunner.execute()"""
import json
import os
import time
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock

import pytest

from cycle_runner import CycleRunner


def _make_ai_trader(tmp_path):
    """Create a heavily mocked ai_trader for cycle tests."""
    signals_file = tmp_path / "signals.json"
    signals_file.write_text("{}")

    trader = MagicMock()
    trader.data_reader = MagicMock()
    trader.data_reader.signals_file = signals_file
    trader.prompt_builder = MagicMock()
    trader.llm = MagicMock()
    trader.safety = MagicMock()
    trader.bot_ipc = MagicMock()
    trader.db = MagicMock()
    trader.pattern_engine = MagicMock()
    trader.stats_formatter = MagicMock()
    trader.decision_template = "{context}"
    trader.system_prompt = "You are a trading AI."
    trader._last_state_hash = None
    trader._cycles_skipped = 0
    trader._last_sent_decision_id = "test123"
    trader._last_processed_outcome_ts = None
    trader.result_file = tmp_path / "result.json"

    return trader


@pytest.fixture
def trader(tmp_path):
    return _make_ai_trader(tmp_path)


@pytest.fixture
def runner(trader):
    return CycleRunner(trader)


class TestCycleRunnerNoSignals:
    @pytest.mark.asyncio
    async def test_no_signals_returns_early(self, runner, trader):
        trader.data_reader.read_signals.return_value = ([], {})
        await runner.execute("cycle-001")
        # LLM should not be called
        trader.llm.call.assert_not_called()
        trader.db.log_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_signals_returns_early(self, runner, trader, tmp_path):
        signals_file = trader.data_reader.signals_file
        # Write signals data
        signals_data = [{"symbol": "BTC-USDT", "compositeScore": 80, "direction": "long"}]
        trader.data_reader.read_signals.return_value = (signals_data, {"accountEquity": 1000})
        # Set signals file mtime to 700 seconds ago (stale > 600s)
        old_time = time.time() - 700
        os.utime(str(signals_file), (old_time, old_time))
        await runner.execute("cycle-002")
        trader.llm.call.assert_not_called()


class TestCycleRunnerStateHash:
    @pytest.mark.asyncio
    async def test_state_unchanged_skips_llm(self, runner, trader):
        signals_data = [{"symbol": "BTC-USDT", "compositeScore": 80, "direction": "long"}]
        trader.data_reader.read_signals.return_value = (signals_data, {"accountEquity": 1000})
        trader.data_reader.read_positions.return_value = []
        trader.db.get_recent_decisions.return_value = []
        trader.db.get_recent_outcomes.return_value = []

        # Set a pre-existing hash so state appears unchanged
        import hashlib, json as _json
        top_signals = sorted(signals_data, key=lambda s: float(s.get("compositeScore", 0)), reverse=True)[:10]
        state_input = _json.dumps({
            "signals": [(s.get("symbol"), round(float(s.get("compositeScore", 0))), s.get("direction")) for s in top_signals],
            "positions": [],
        }, sort_keys=True)
        state_hash = hashlib.sha256(state_input.encode()).hexdigest()[:16]
        trader._last_state_hash = state_hash

        await runner.execute("cycle-003")
        trader.llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_changed_calls_llm(self, runner, trader):
        signals_data = [{"symbol": "BTC-USDT", "compositeScore": 85, "direction": "long"}]
        trader.data_reader.read_signals.return_value = (signals_data, {"accountEquity": 1000})
        trader.data_reader.read_positions.return_value = []
        trader.db.get_recent_decisions.return_value = []
        trader.db.get_recent_outcomes.return_value = []

        # LLM returns a hold decision
        mock_result = MagicMock()
        mock_result.tokens_in = 100
        mock_result.tokens_out = 20
        trader.llm.call = AsyncMock(return_value=mock_result)
        trader.prompt_builder.build_prompt.return_value = "Test context for LLM"
        trader.safety.validate.return_value = (True, [])
        trader.db.get_performance_stats.return_value = {
            "win_rate": 0.5, "total_trades": 10, "wins": 5, "avg_win": 50, "avg_loss": -30,
        }
        trader.db.get_daily_pnl.return_value = 100.0

        with patch("cycle_runner.parse_decision_json", return_value={"action": "hold", "confidence": 0.5}):
            await runner.execute("cycle-004")

        trader.llm.call.assert_called_once()
        trader.db.log_decision.assert_called_once()
