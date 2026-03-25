"""Tests for context/pattern_engine.py — PatternEngine class"""
import json
import os
import tempfile

import pytest

from context.pattern_engine import PatternEngine


def _make_mock_ai_trader(tmpdir):
    """Create a mock ai_trader with config pointing to tmpdir."""
    config_path = os.path.join(tmpdir, "config.json")
    # Write a dummy config file so dirname works
    with open(config_path, "w") as f:
        json.dump({}, f)
    return type("Mock", (), {
        "config": {
            "_config_path": config_path,
            "patterns_file": "state/patterns.json",
        }
    })()


@pytest.fixture
def engine(tmp_path):
    tmpdir = str(tmp_path)
    trader = _make_mock_ai_trader(tmpdir)
    return PatternEngine(trader)


class TestPatternEngineLoad:
    def test_load_no_file(self, engine):
        result = engine._load()
        assert result == {"patterns": []}

    def test_save_load_roundtrip(self, engine):
        data = {"patterns": [{"rule": "buy_dip", "confidence": 0.7}]}
        engine._save(data)
        loaded = engine._load()
        assert len(loaded["patterns"]) == 1
        assert loaded["patterns"][0]["rule"] == "buy_dip"
        assert loaded["patterns"][0]["confidence"] == 0.7

    def test_load_corrupt_json(self, engine):
        engine.patterns_file.parent.mkdir(parents=True, exist_ok=True)
        engine.patterns_file.write_text("not valid json{{{")
        result = engine._load()
        assert result == {"patterns": []}


class TestPatternEngineReadPatterns:
    def test_filters_by_confidence(self, engine):
        data = {"patterns": [
            {"rule": "high_conf", "confidence": 0.8},
            {"rule": "mid_conf", "confidence": 0.4},
            {"rule": "low_conf", "confidence": 0.2},
        ]}
        engine._save(data)
        result = engine.read_patterns()
        assert len(result) == 2
        names = [p["rule"] for p in result]
        assert "high_conf" in names
        assert "mid_conf" in names
        assert "low_conf" not in names


class TestPatternEngineDecay:
    def test_decay_reduces_confidence(self, engine):
        data = {"patterns": [{"rule": "test_rule", "confidence": 0.8}]}
        engine._save(data)
        engine.decay_patterns(decay=0.1)
        loaded = engine._load()
        assert loaded["patterns"][0]["confidence"] == pytest.approx(0.7, abs=0.01)

    def test_decay_drops_below_threshold(self, engine):
        data = {"patterns": [{"rule": "weak_rule", "confidence": 0.35}]}
        engine._save(data)
        engine.decay_patterns(decay=0.1)  # 0.35 - 0.1 = 0.25 < 0.3 → dropped
        loaded = engine._load()
        assert len(loaded["patterns"]) == 0

    def test_decay_no_file(self, engine):
        """Should not crash when patterns file doesn't exist."""
        engine.decay_patterns()  # should not raise


class TestPatternEngineReinforce:
    def test_reinforce_existing_pattern(self, engine):
        data = {"patterns": [{"rule": "buy_dip", "confidence": 0.6}]}
        engine._save(data)
        engine.reinforce_pattern("buy_dip", boost=0.2)
        loaded = engine._load()
        assert loaded["patterns"][0]["confidence"] == pytest.approx(0.8, abs=0.01)

    def test_reinforce_new_pattern(self, engine):
        data = {"patterns": []}
        engine._save(data)
        engine.reinforce_pattern("new_rule")
        loaded = engine._load()
        assert len(loaded["patterns"]) == 1
        assert loaded["patterns"][0]["rule"] == "new_rule"
        assert loaded["patterns"][0]["confidence"] == 0.5

    def test_reinforce_clamps_at_1_0(self, engine):
        data = {"patterns": [{"rule": "top_rule", "confidence": 0.95}]}
        engine._save(data)
        engine.reinforce_pattern("top_rule", boost=0.2)  # 0.95 + 0.2 = 1.15 → clamped to 1.0
        loaded = engine._load()
        assert loaded["patterns"][0]["confidence"] == 1.0


class TestPatternEngineBuildSection:
    def test_build_section_formats_correctly(self, engine):
        data = {"patterns": [
            {"rule": "high_rule", "confidence": 0.9},
            {"rule": "mid_rule", "confidence": 0.5},
        ]}
        engine._save(data)
        result = engine.build_section()
        assert "## Learned Patterns" in result
        assert "high_rule" in result
        assert "mid_rule" in result
        # Should be sorted by confidence descending
        assert result.index("high_rule") < result.index("mid_rule")

    def test_build_section_no_patterns(self, engine):
        result = engine.build_section()
        assert result == ""
