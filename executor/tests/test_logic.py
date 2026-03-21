"""Quick test of the trailing TP/SL logic (no API needed)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import BotConfig, PositionTracker

def test_trailing_tp_long():
    cfg = BotConfig(
        trailing_tp_trigger_pct=3.0,
        trailing_tp_delta_pct=1.0,
        sl_pct=2.0,
        dsl_enabled=False,
    )
    tracker = PositionTracker(cfg)
    tracker.add_position(1, "BTC", "long", 100.0, 0.1)

    # Price at entry — nothing happens
    assert tracker.update_price(1, 100.0) is None
    assert not tracker.positions[1].trailing_active

    # Price at +2% — trigger not yet reached
    assert tracker.update_price(1, 102.0) is None
    assert not tracker.positions[1].trailing_active

    # Price at +3% — trailing activates
    result = tracker.update_price(1, 103.0)
    assert tracker.positions[1].trailing_active
    # May return ("trailing_activated", ...) alert or None
    if result is not None:
        assert result[0] == "trailing_activated"

    # Price rises to +5% — new high
    assert tracker.update_price(1, 105.0) is None
    assert tracker.positions[1].high_water_mark == 105.0

    # Price drops to 103.5 (3.3% from 105 peak) → TRIGGER (well below 103.95 threshold)
    result = tracker.update_price(1, 103.5)
    assert result == "trailing_take_profit", f"Expected trailing_take_profit, got {result}"
    print("✅ Trailing TP (long) works")

def test_stop_loss_long():
    cfg = BotConfig(
        trailing_tp_trigger_pct=3.0,
        trailing_tp_delta_pct=1.0,
        sl_pct=2.0,
        dsl_enabled=False,
    )
    tracker = PositionTracker(cfg)
    tracker.add_position(2, "ETH", "long", 3000.0, 1.0)

    # Establish high water mark and trailing SL level
    tracker.update_price(2, 3100.0)  # hwm=3100, trailing_sl=3038
    assert tracker.positions[2].trailing_sl_level == 3038.0

    # Price drops below trailing SL → stop loss triggers
    result = tracker.update_price(2, 3037.0)
    assert result == "stop_loss", f"Expected stop_loss, got {result}"
    print("✅ Stop loss (long) works")

def test_trailing_tp_short():
    cfg = BotConfig(
        trailing_tp_trigger_pct=3.0,
        trailing_tp_delta_pct=1.0,
        sl_pct=2.0,
        dsl_enabled=False,
    )
    tracker = PositionTracker(cfg)
    tracker.add_position(3, "BTC", "short", 100.0, 0.1)

    # Price drops -3% → trailing activates
    result = tracker.update_price(3, 97.0)
    assert tracker.positions[3].trailing_active
    if result is not None:
        assert result[0] == "trailing_activated"

    # Price drops to -5% → new low
    assert tracker.update_price(3, 95.0) is None

    # Price rises to 96 (-4% from entry, +1% from 95 low) → TRIGGER
    result = tracker.update_price(3, 96.0)
    assert result == "trailing_take_profit", f"Expected trailing_take_profit, got {result}"
    print("✅ Trailing TP (short) works")

if __name__ == "__main__":
    test_trailing_tp_long()
    test_stop_loss_long()
    test_trailing_tp_short()
    print("\n🎉 All tests passed!")
