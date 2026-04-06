"""
Tests for ai-decisions/safety.py — Verification that leverage is removed from schema validation.
"""
import pytest
from unittest.mock import MagicMock

from safety import SafetyLayer


class TestSafetyLayerLeverageRemoval:
    def test_schema_validation_no_longer_requires_leverage(self):
        """Safety layer should not require 'leverage' field in open decisions."""
        cfg = {"safety": {"max_positions": 3, "max_equity_per_position": 0.1}}
        mock_db = MagicMock(); mock_db.get_daily_pnl.return_value = 0.0
        safety = SafetyLayer(cfg, mock_db)
        
        # Valid decision without leverage field
        decision = {
            "action": "open",
            "symbol": "BTC",
            "direction": "long",
            "size_pct_equity": 5.0,
            "stop_loss_pct": 2.0,
            "reasoning": "Strong signal",
            "confidence": 0.8
        }
        
        approved, errors = safety.validate(decision, [], [], 1000.0)
        assert approved or len(errors) == 0, f"Should accept decision without leverage but got errors: {errors}"

    def test_schema_validation_ignores_leverage_if_present(self):
        """Safety layer should ignore leverage field if somehow present (backward compat)."""
        cfg = {"safety": {"max_positions": 3, "max_equity_per_position": 0.1}}
        mock_db = MagicMock(); mock_db.get_daily_pnl.return_value = 0.0
        safety = SafetyLayer(cfg, mock_db)
        
        # Decision with legacy leverage field (shouldn't break)
        decision = {
            "action": "open",
            "symbol": "BTC",
            "direction": "long", 
            "size_pct_equity": 5.0,
            "leverage": 10.0,  # This should be ignored
            "stop_loss_pct": 2.0,
            "reasoning": "Strong signal",
            "confidence": 0.8
        }
        
        approved, errors = safety.validate(decision, [], [], 1000.0)
        assert approved or len(errors) == 0, f"Should accept decision with ignored leverage field but got: {errors}"

    def test_close_decision_validation_still_works(self):
        """Close decisions should still validate correctly."""
        cfg = {"safety": {"max_positions": 3, "max_equity_per_position": 0.1}}
        mock_db = MagicMock(); mock_db.get_daily_pnl.return_value = 0.0
        safety = SafetyLayer(cfg, mock_db)
        
        decision = {
            "action": "close",
            "symbol": "BTC",
            "reasoning": "Take profit",
            "confidence": 0.9
        }
        
        # Mock position exists for BTC
        positions = [{"symbol": "BTC"}]
        approved, errors = safety.validate(decision, positions, [], 1000.0)
        assert approved or len(errors) == 0, f"Close decisions should validate but got: {errors}"

    def test_hold_decision_validation_still_works(self):
        """Hold decisions should still validate correctly."""
        cfg = {"safety": {"max_positions": 3, "max_equity_per_position": 0.1}}
        mock_db = MagicMock(); mock_db.get_daily_pnl.return_value = 0.0
        safety = SafetyLayer(cfg, mock_db)
        
        decision = {
            "action": "hold",
            "reasoning": "Wait for better setup",
            "confidence": 0.6
        }
        
        approved, errors = safety.validate(decision, [], [], 1000.0)
        assert approved, f"Hold decisions should validate but got: {errors}"

    def test_missing_required_fields_still_caught(self):
        """Safety should still catch missing required fields (just not leverage)."""
        cfg = {"safety": {"max_positions": 3, "max_equity_per_position": 0.1}}
        mock_db = MagicMock(); mock_db.get_daily_pnl.return_value = 0.0
        safety = SafetyLayer(cfg, mock_db)
        
        # Missing stop_loss_pct
        decision = {
            "action": "open",
            "symbol": "BTC",
            "direction": "long",
            "size_pct_equity": 5.0,
            # "stop_loss_pct": 2.0,  # Missing
            "reasoning": "Strong signal",
            "confidence": 0.8
        }
        
        approved, errors = safety.validate(decision, [], [], 1000.0)
        assert not approved, "Should reject decision with missing stop_loss_pct"
        assert any("stop_loss_pct" in err for err in errors), f"Should mention missing stop_loss_pct: {errors}"
        assert not any("leverage" in err for err in errors), f"Should not mention leverage: {errors}"