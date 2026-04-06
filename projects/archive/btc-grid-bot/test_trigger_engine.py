#!/usr/bin/env python3
"""
Quick unit test for trigger_engine.py
"""

import time
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.trigger_engine import TriggerEngine, MarketSnapshot, create_trigger_engine


def test_no_triggers():
    """Test stable market conditions."""
    engine = create_trigger_engine()
    
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=44000,
        volume_5m=80,
        volume_1h_avg=80,
        atr_5m=150,
        atr_1h=150,
        grid_center=44000,  # No drift
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 1800,  # 30 min ago
        fill_count_24h=12,
        market_hours="off_hours"
    )
    
    result = engine.evaluate(snapshot)
    assert result.action == "none"
    print("✓ No triggers test passed")


def test_price_drift():
    """Test price drift trigger."""
    engine = create_trigger_engine()
    
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=45000,
        volume_5m=80,
        volume_1h_avg=80,
        atr_5m=150,
        atr_1h=150,
        grid_center=44000,  # 2.27% drift
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 1800,
        fill_count_24h=12,
        market_hours="off_hours"
    )
    
    result = engine.evaluate(snapshot)
    assert result.action == "ai_reanalysis"
    assert "price_drift" in result.conditions_met
    print("✓ Price drift test passed")


def test_grid_edge():
    """Test grid edge proximity trigger."""
    engine = create_trigger_engine()
    
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=45850,  # Very close to upper edge (46000), within 5% threshold
        volume_5m=80,
        volume_1h_avg=80,
        atr_5m=150,
        atr_1h=150,
        grid_center=45850,  # Set grid center to price to avoid drift trigger
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 1800,
        fill_count_24h=12,
        market_hours="off_hours"
    )
    
    result = engine.evaluate(snapshot)
    assert result.action == "roll_grid"
    assert result.direction == "up"
    assert result.urgency == "high"
    print("✓ Grid edge test passed")


def test_volume_spike():
    """Test volume spike trigger."""
    engine = create_trigger_engine()
    
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=44000,
        volume_5m=320,  # 4x average
        volume_1h_avg=80,
        atr_5m=150,
        atr_1h=150,
        grid_center=44000,
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 1800,
        fill_count_24h=12,
        market_hours="off_hours"
    )
    
    result = engine.evaluate(snapshot)
    assert result.action == "ai_reanalysis"
    assert "volume_spike" in result.conditions_met
    print("✓ Volume spike test passed")


def test_cooldown():
    """Test cooldown functionality."""
    engine = create_trigger_engine({
        "cooldown_minutes": {"ai_reanalysis": 60}
    })
    
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=45000,  # Will trigger price drift
        volume_5m=80,
        volume_1h_avg=80,
        atr_5m=150,
        atr_1h=150,
        grid_center=44000,
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 1800,
        fill_count_24h=12,
        market_hours="off_hours"
    )
    
    # First trigger
    result1 = engine.evaluate(snapshot)
    assert result1.action == "ai_reanalysis"
    
    # Second trigger immediately - should be blocked by cooldown
    result2 = engine.evaluate(snapshot)
    assert result2.action == "none"
    
    print("✓ Cooldown test passed")


def test_escalation():
    """Test escalation logic for multiple triggers."""
    config = {
        "price_drift_pct": 1.0,  # Lower threshold to trigger easily
        "volume_spike_ratio": 2.0,  # Lower threshold
        "escalation_window_minutes": 15,
        "cooldown_minutes": {
            "ai_reanalysis": 0,  # No cooldown for test
            "ai_redeploy": 0
        }
    }
    engine = create_trigger_engine(config)
    
    # Snapshot that triggers both price drift AND volume spike
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=45000,  # 2.27% drift
        volume_5m=200,  # 2.5x volume spike
        volume_1h_avg=80,
        atr_5m=150,
        atr_1h=150,
        grid_center=44000,
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 1800,
        fill_count_24h=12,
        market_hours="off_hours"
    )
    
    result = engine.evaluate(snapshot)
    # Should escalate to ai_redeploy due to multiple conditions
    assert result.action == "ai_redeploy"
    assert result.urgency == "high"
    assert len(result.conditions_met) >= 2
    print("✓ Escalation test passed")


if __name__ == "__main__":
    print("Running trigger engine tests...")
    test_no_triggers()
    test_price_drift()
    test_grid_edge()
    test_volume_spike()
    test_cooldown()
    test_escalation()
    print("✅ All tests passed!")