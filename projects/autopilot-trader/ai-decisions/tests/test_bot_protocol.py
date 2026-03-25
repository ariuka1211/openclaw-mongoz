"""Tests for ipc/bot_protocol.py — BotProtocol class"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ipc.bot_protocol import BotProtocol


def _make_mock_ai_trader(tmp_path):
    """Create a mock ai_trader with file paths and state."""
    decision_file = tmp_path / "decision.json"
    result_file = tmp_path / "result.json"
    return type("Mock", (), {
        "decision_file": decision_file,
        "result_file": result_file,
        "_ack_timeout_seconds": 30,
        "_last_sent_decision_id": None,
        "emergency_halt": False,
        "db": MagicMock(),
    })()


@pytest.fixture
def trader(tmp_path):
    return _make_mock_ai_trader(tmp_path)


@pytest.fixture
def protocol(trader):
    return BotProtocol(trader)


class TestCheckResult:
    @pytest.mark.asyncio
    async def test_matching_decision_id(self, protocol, trader):
        trader.result_file.write_text(json.dumps({
            "processed_decision_id": "abc123",
            "success": True,
            "positions": [],
        }))
        result = await protocol.check_result("abc123")
        assert result is not None
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_non_matching_decision_id(self, protocol, trader):
        trader.result_file.write_text(json.dumps({
            "processed_decision_id": "xyz789",
            "success": True,
        }))
        result = await protocol.check_result("abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_result_file(self, protocol, trader):
        # File doesn't exist
        result = await protocol.check_result("abc123")
        assert result is None


class TestSendDecision:
    @pytest.mark.asyncio
    async def test_send_decision_writes_file(self, protocol, trader):
        decision = {
            "action": "open",
            "symbol": "BTC-USDT",
            "direction": "long",
            "size_pct_equity": 5.0,
            "leverage": 3,
            "stop_loss_pct": 2.0,
            "reasoning": "Strong momentum",
            "confidence": 0.8,
        }
        result = await protocol.send_decision(decision, equity=1000)
        assert result is True
        assert trader.decision_file.exists()
        written = json.loads(trader.decision_file.read_text())
        assert written["action"] == "open"
        assert written["symbol"] == "BTC-USDT"
        assert written["decision_id"] is not None
        assert trader._last_sent_decision_id == written["decision_id"]

    @pytest.mark.asyncio
    async def test_send_decision_skips_when_unacked(self, protocol, trader):
        """If there's a current decision that hasn't been ACKed, skip."""
        now = datetime.now(timezone.utc).isoformat()
        current = {
            "decision_id": "pending1",
            "timestamp": now,
            "action": "open",
            "symbol": "ETH-USDT",
        }
        trader.decision_file.write_text(json.dumps(current))
        # No ACK file exists → unacked
        decision = {"action": "close", "symbol": "BTC-USDT"}
        result = await protocol.send_decision(decision)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_decision_overwrites_stale(self, protocol, trader):
        """If current decision is stale (past timeout), overwrite it."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        current = {
            "decision_id": "stale1",
            "timestamp": old_time.isoformat(),
            "action": "open",
            "symbol": "ETH-USDT",
        }
        trader.decision_file.write_text(json.dumps(current))
        # No ACK file → stale
        decision = {"action": "hold", "symbol": "BTC-USDT"}
        result = await protocol.send_decision(decision)
        assert result is True
