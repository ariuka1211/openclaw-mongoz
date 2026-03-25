"""Tests for llm/parser.py — parse_decision_json()"""
import json
import pytest

from llm.parser import parse_decision_json


class TestParseCleanJson:
    def test_clean_json(self):
        raw = '{"action": "open", "symbol": "BTC-USDT", "confidence": 0.8}'
        result = parse_decision_json(raw)
        assert result["action"] == "open"
        assert result["symbol"] == "BTC-USDT"
        assert result["confidence"] == 0.8

    def test_nested_objects(self):
        data = {
            "action": "open",
            "symbol": "ETH-USDT",
            "metadata": {"entry": {"price": 3000.0}, "stop": {"price": 2900.0}},
            "confidence": 0.9,
        }
        raw = json.dumps(data)
        result = parse_decision_json(raw)
        assert result["metadata"]["entry"]["price"] == 3000.0
        assert result["metadata"]["stop"]["price"] == 2900.0

    def test_newlines_and_whitespace(self):
        raw = '  \n  {"action": "hold", "reasoning": "test", "confidence": 0.5}  \n  '
        result = parse_decision_json(raw)
        assert result["action"] == "hold"
        assert result["confidence"] == 0.5


class TestParseMarkdownFences:
    def test_json_code_block(self):
        raw = '```json\n{"action": "open", "symbol": "BTC-USDT", "confidence": 0.7}\n```'
        result = parse_decision_json(raw)
        assert result["action"] == "open"
        assert result["symbol"] == "BTC-USDT"

    def test_plain_code_block(self):
        raw = '```\n{"action": "hold", "confidence": 0.5}\n```'
        result = parse_decision_json(raw)
        assert result["action"] == "hold"


class TestParseExtraText:
    def test_text_before_json(self):
        raw = 'Here is my decision:\n{"action": "open", "symbol": "SOL-USDT", "confidence": 0.6}'
        result = parse_decision_json(raw)
        assert result["action"] == "open"
        assert result["symbol"] == "SOL-USDT"

    def test_text_after_json(self):
        raw = '{"action": "hold", "confidence": 0.3}\n\nThis trade is risky because...'
        result = parse_decision_json(raw)
        assert result["action"] == "hold"


class TestParseFailures:
    def test_no_json_at_all(self):
        raw = "I don't think we should trade right now. The market is too volatile."
        result = parse_decision_json(raw)
        assert result["action"] == "hold"
        assert result["confidence"] == 0

    def test_unmatched_braces(self):
        raw = '{"action": "open", "symbol": "BTC-USDT"'
        result = parse_decision_json(raw)
        assert result["action"] == "hold"
        assert result["confidence"] == 0

    def test_empty_string(self):
        result = parse_decision_json("")
        assert result["action"] == "hold"
        assert result["confidence"] == 0
