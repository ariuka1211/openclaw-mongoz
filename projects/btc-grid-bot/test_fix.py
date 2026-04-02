"""
Test script to verify the bug fix in _place_replacement method.
The fix ensures that when a sell replacement order fails after a buy fill,
the filled buy order is cancelled to close the position.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Add the project directory to path
sys.path.insert(0, '/root/.openclaw/workspace/projects/btc-grid-bot')

from grid import GridManager

async def test_buy_replacement_failure_cancels_position():
    """
    Test that when a buy order is filled and then the sell replacement fails,
    the filled buy order is cancelled to close the position.
    """
    print("🧪 Testing buy replacement failure with cancellation...")
    
    # Create mock config and API
    cfg = {
        "capital": {
            "max_exposure_multiplier": 1.0,
            "margin_reserve_pct": 0.1
        }
    }
    
    api = MagicMock()
    api.place_limit_order = AsyncMock()
    api.cancel_order = AsyncMock()
    api.get_open_orders = AsyncMock(return_value=[
        # This represents the filled buy order that we need to cancel
        {"side": "buy", "price": 50000, "order_id": "filled_buy_order_123", "status": "open"}
    ])
    
    # Mock the state to have a filled buy order
    gm = GridManager(cfg, api)
    gm.state = {
        "active": True,
        "paused": False,
        "pause_reason": "",
        "levels": {
            "buy": [49000, 49500, 50000, 50500, 51000],
            "sell": [51500, 52000, 52500, 53000, 53500]
        },
        "range_low": 49000,
        "range_high": 53500,
        "size_per_level": 1.0,
        "orders": [
            {
                "side": "buy",
                "price": 50000,
                "order_id": "filled_buy_order_123",
                "status": "filled"  # This is the filled order
            }
        ],
        "last_reset": "2024-01-01T00:00:00Z",
        "equity_at_reset": 100000.0,
        "daily_pnl": 0.0,
        "roll_count": 0,
        "last_roll": None,
    }
    
    # Simulate a failed sell replacement (3 retries, all fail)
    async def fail_place_order(*args, **kwargs):
        await asyncio.sleep(0.1)
        raise Exception("Simulated network error: order placement failed")
    
    api.place_limit_order.side_effect = fail_place_order
    
    # Call _place_replacement with a filled buy order
    filled_buy = {
        "side": "buy",
        "price": 50000,
        "order_id": "filled_buy_order_123"
    }
    
    try:
        await gm._place_replacement(
            filled_buy,
            [49000, 49500, 50000, 50500, 51000],  # buy_levels
            [51500, 52000, 52500, 53000, 53500],  # sell_levels
            1.0,  # size
            set()  # open_prices
        )
    except Exception as e:
        print(f"❌ Test failed with unexpected exception: {e}")
        return False
    
    # Verify that cancel_order was called on the filled buy order
    if api.cancel_order.called:
        call_args = api.cancel_order.call_args
        cancelled_order_id = call_args[0][0]
        if cancelled_order_id == "filled_buy_order_123":
            print("✅ SUCCESS: Filled buy order was cancelled after replacement failures")
            print(f"   Order ID cancelled: {cancelled_order_id}")
            return True
        else:
            print(f"❌ FAILURE: Wrong order ID was cancelled: {cancelled_order_id}")
            return False
    else:
        print("❌ FAILURE: cancel_order was NOT called - buy position remains open!")
        return False

async def test_sell_replacement_failure_no_cancel():
    """
    Test that when a sell order is filled and the buy replacement fails,
    the filled sell order is NOT cancelled (since it's a closing position).
    """
    print("\n🧪 Testing sell replacement failure without cancellation...")
    
    # Create mock config and API
    cfg = {
        "capital": {
            "max_exposure_multiplier": 1.0,
            "margin_reserve_pct": 0.1
        }
    }
    
    api = MagicMock()
    api.place_limit_order = AsyncMock()
    api.cancel_order = AsyncMock()
    api.get_open_orders = AsyncMock(return_value=[
        {"side": "sell", "price": 53000, "order_id": "filled_sell_order_456", "status": "open"}
    ])
    
    # Mock the state to have a filled sell order
    gm = GridManager(cfg, api)
    gm.state = {
        "active": True,
        "paused": False,
        "pause_reason": "",
        "levels": {
            "buy": [49000, 49500, 50000, 50500, 51000],
            "sell": [51500, 52000, 52500, 53000, 53500]
        },
        "range_low": 49000,
        "range_high": 53500,
        "size_per_level": 1.0,
        "orders": [
            {
                "side": "sell",
                "price": 53000,
                "order_id": "filled_sell_order_456",
                "status": "filled"  # This is the filled order
            }
        ],
        "last_reset": "2024-01-01T00:00:00Z",
        "equity_at_reset": 100000.0,
        "daily_pnl": 0.0,
        "roll_count": 0,
        "last_roll": None,
    }
    
    # Simulate a failed buy replacement (3 retries, all fail)
    async def fail_place_order(*args, **kwargs):
        await asyncio.sleep(0.1)
        raise Exception("Simulated network error: order placement failed")
    
    api.place_limit_order.side_effect = fail_place_order
    
    # Call _place_replacement with a filled sell order
    filled_sell = {
        "side": "sell",
        "price": 53000,
        "order_id": "filled_sell_order_456"
    }
    
    try:
        await gm._place_replacement(
            filled_sell,
            [49000, 49500, 50000, 50500, 51000],  # buy_levels
            [51500, 52000, 52500, 53000, 53500],  # sell_levels
            1.0,  # size
            set()  # open_prices
        )
    except Exception as e:
        print(f"❌ Test failed with unexpected exception: {e}")
        return False
    
    # Verify that cancel_order was NOT called on the filled sell order
    if api.cancel_order.called:
        print("❌ FAILURE: cancel_order was called on filled sell order - should NOT be cancelled!")
        return False
    else:
        print("✅ SUCCESS: Filled sell order was NOT cancelled after replacement failures")
        return True

async def main():
    print("=" * 70)
    print("🔍 Verifying Bug Fix: _place_replacement Error Handling")
    print("=" * 70)
    
    # Test 1: Buy replacement failure should cancel the filled buy
    test1_passed = await test_buy_replacement_failure_cancels_position()
    
    # Test 2: Sell replacement failure should NOT cancel the filled sell
    test2_passed = await test_sell_replacement_failure_no_cancel()
    
    print("\n" + "=" * 70)
    print("📊 Test Results Summary")
    print("=" * 70)
    print(f"Test 1 (Buy → Sell replacement failure): {'PASS' if test1_passed else 'FAIL'}")
    print(f"Test 2 (Sell → Buy replacement failure): {'PASS' if test2_passed else 'FAIL'}")
    
    if test1_passed and test2_passed:
        print("\n✅ All tests passed! The fix is working correctly.")
        print("   - Filled buy orders are cancelled when sell replacement fails")
        print("   - Filled sell orders are NOT cancelled when buy replacement fails (grid neutrality maintained)")
        return 0
    else:
        print("\n❌ Some tests failed. Please review the fix.")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)