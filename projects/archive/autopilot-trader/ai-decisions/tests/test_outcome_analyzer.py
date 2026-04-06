"""Tests for context/outcome_analyzer.py"""
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from context.outcome_analyzer import OutcomeAnalyzer


def _make_trader(tmp_path):
    """Create a mock ai_trader with pattern_engine."""
    config_path = str(tmp_path / "config.json")
    with open(config_path, "w") as f:
        json.dump({}, f)

    from context.pattern_engine import PatternEngine

    trader = MagicMock()
    trader.config = {"_config_path": config_path, "patterns_file": "state/patterns.json"}
    trader.pattern_engine = PatternEngine(trader)
    return trader


def _make_outcome(symbol="BTC", direction="long", pnl_usd=10.0, hold_secs=1800, hours_ago=1):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "symbol": symbol,
        "direction": direction,
        "pnl_usd": pnl_usd,
        "hold_time_seconds": hold_secs,
        "timestamp": ts,
    }


class TestOutcomeAnalyzer:
    def test_skips_when_few_outcomes(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        analyzer.analyze_and_update([_make_outcome()], [])
        # Should not call reinforce with < 3 outcomes
        patterns = trader.pattern_engine.read_patterns()
        assert len(patterns) == 0

    def test_reinforces_winning_session_pattern(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        # 4 wins, 1 loss for longs in US session
        outcomes = [_make_outcome(direction="long", pnl_usd=10)] * 4 + [_make_outcome(direction="long", pnl_usd=-5)]
        analyzer.analyze_and_update(outcomes, [])
        patterns = trader.pattern_engine.read_patterns()
        pattern_rules = [p["rule"] for p in patterns]
        assert any("long_us" in r for r in pattern_rules)

    def test_skips_low_win_rate(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        # 1 win, 3 losses — below 60% threshold
        outcomes = [_make_outcome(direction="long", pnl_usd=10)] + [_make_outcome(direction="long", pnl_usd=-5)] * 3
        analyzer.analyze_and_update(outcomes, [])
        patterns = trader.pattern_engine.read_patterns()
        # No pattern should be reinforced for long_us
        long_us_patterns = [p for p in patterns if "long_us" in p["rule"]]
        assert len(long_us_patterns) == 0

    def test_quick_exit_pattern(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        # 4 quick exits that win
        outcomes = [_make_outcome(hold_secs=120, pnl_usd=5)] * 4
        analyzer.analyze_and_update(outcomes, [])
        patterns = trader.pattern_engine.read_patterns()
        pattern_rules = [p["rule"] for p in patterns]
        assert "quick_exit" in pattern_rules

    def test_long_hold_pattern(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        # 4 long holds that win
        outcomes = [_make_outcome(hold_secs=7200, pnl_usd=20)] * 4
        analyzer.analyze_and_update(outcomes, [])
        patterns = trader.pattern_engine.read_patterns()
        pattern_rules = [p["rule"] for p in patterns]
        assert "long_hold" in pattern_rules

    def test_symbol_direction_pattern(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        outcomes = [_make_outcome(symbol="ETH", direction="short", pnl_usd=8)] * 4
        analyzer.analyze_and_update(outcomes, [])
        patterns = trader.pattern_engine.read_patterns()
        pattern_rules = [p["rule"] for p in patterns]
        assert "eth_short" in pattern_rules

    def test_high_confidence_pattern_with_decision(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        history = [{"action": "open", "symbol": "BTC", "executed": True, "confidence": 0.85}]
        outcomes = [_make_outcome(symbol="BTC", pnl_usd=10)] * 4
        analyzer.analyze_and_update(outcomes, history)
        patterns = trader.pattern_engine.read_patterns()
        pattern_rules = [p["rule"] for p in patterns]
        assert "high_confidence" in pattern_rules

    def test_no_crash_on_empty_outcomes(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        analyzer.analyze_and_update([], [])
        # Should not raise

    def test_no_crash_on_missing_timestamp(self, tmp_path):
        trader = _make_trader(tmp_path)
        analyzer = OutcomeAnalyzer(trader)
        outcomes = [{"symbol": "BTC", "direction": "long", "pnl_usd": 5, "hold_time_seconds": 600}] * 4
        analyzer.analyze_and_update(outcomes, [])
        # Should not raise, session pattern skipped but others work
