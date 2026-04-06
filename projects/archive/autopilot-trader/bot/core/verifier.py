"""
Position verification functions extracted from SignalProcessor.

Verify positions are opened/closed on the exchange and check active orders.
"""

import asyncio
import logging

from auth_helper import LighterAuthManager


async def verify_position_opened(api, market_id: int, expected_size: float, symbol: str) -> dict | None:
    """Verify a position exists on the exchange after open_order returns success.

    Retries up to 3 times with 1-second delays (max ~3s total).
    Returns position dict with actual filled size on success, None on failure.

    Handles:
    - Exchange rejecting the order after SDK returned success (phantom positions)
    - Partial fills (actual size < requested size)
    - EDGE-03: get_positions() returning None on network failure
    """
    for attempt in range(1, 4):
        await asyncio.sleep(1)
        try:
            live_positions = await api.get_positions()
            # Handle EDGE-03: get_positions() returns None on failure
            if live_positions is None:
                logging.warning(f"⚠️ {symbol}: get_positions() returned None during open verification (attempt {attempt}/3)")
                continue
            for p in live_positions:
                if p["market_id"] == market_id and abs(p.get("size", 0)) > 0.001:
                    actual_size = p["size"]
                    # Fix #14: Reject severely underfilled positions (< 20%)
                    # Bot DSL logic is designed for full-size — don't manage tiny positions
                    fill_ratio = actual_size / expected_size if expected_size > 0 else 0
                    if fill_ratio < 0.3:
                        logging.error(
                            f"❌ {symbol}: position severely underfilled, REJECTING "
                            f"(actual={actual_size:.6f}, expected={expected_size:.6f}, fill={fill_ratio:.1%})"
                        )
                        return None  # Abort — don't manage severely underfilled positions
                    elif fill_ratio < 0.9:
                        logging.info(
                            f"📊 {symbol}: partial fill detected "
                            f"(actual={actual_size:.6f}, expected={expected_size:.6f}, fill={fill_ratio:.1%})"
                        )
                    else:
                        logging.info(
                            f"✅ {symbol}: position verified on exchange "
                            f"(size={actual_size:.6f}, attempt {attempt})"
                        )
                    return p
            logging.info(f"⏳ {symbol}: position not yet visible on exchange (attempt {attempt}/3)")
        except Exception as e:
            logging.warning(f"⚠️ {symbol}: error during open verification (attempt {attempt}/3): {e}")
    logging.error(f"❌ {symbol}: position NOT found on exchange after 3 verification attempts — order may have been rejected")
    return None


async def verify_position_closed(bot, api, market_id: int, symbol: str) -> bool:
    """Poll the Lighter API to verify a position is actually closed after a close order.
    Uses progressively longer delays to account for exchange processing time.
    MED-25: Adds market_id to _verifying_close to skip DSL/SL evaluation during verification.
    """
    bot._verifying_close.add(market_id)
    try:
        delays = [3, 5, 7, 10]  # MED-25: reduced from [5,10,15,20] — 25s total instead of 50s
        for attempt, delay in enumerate(delays):
            await asyncio.sleep(delay)
            try:
                live_positions = await api.get_positions()
                still_open = any(p["market_id"] == market_id and abs(p.get("size", 0)) > 0.001 for p in live_positions)
                if not still_open:
                    logging.info(f"✅ {symbol}: position closure verified (attempt {attempt + 1}, after {delay}s)")
                    return True
                # Also check if there are any active orders (the close order might still be pending)
                active_orders = await check_active_orders(bot, api, market_id)
                sl_orders = [o for o in active_orders]  # all active orders are close-related (both long close and short close)
                logging.info(
                    f"⏳ {symbol}: position still open (attempt {attempt + 1}/{len(delays)}), "
                    f"active_orders={len(active_orders)}, sl_orders={len(sl_orders)}"
                )
            except Exception as e:
                logging.warning(f"⚠️ {symbol}: error verifying closure (attempt {attempt + 1}): {e}")
        return False
    finally:
        bot._verifying_close.discard(market_id)


async def check_active_orders(bot, api, market_id: int) -> list[dict]:
    """Check if there are any active (unfilled) orders for this market on our account.

    Used by MED-18 cancel logic to find order_index for cancellation.
    """
    try:
        await api._ensure_client()
        # Generate auth token for the request
        auth = None
        try:
            await api._ensure_signer()
            if api._signer is not None:
                if not hasattr(bot, '_auth_manager'):
                    bot._auth_manager = LighterAuthManager(
                        signer=api._signer,
                        account_index=bot.cfg.account_index
                    )
                auth = bot._auth_manager.get_auth_token()
        except Exception as auth_err:
            logging.debug(f"Auth generation skipped: {auth_err}")
        orders = await api._order_api.account_active_orders(
            account_index=bot.cfg.account_index,
            market_id=market_id,
            auth=auth,
            _request_timeout=30,
        )
        active = []
        if hasattr(orders, 'orders') and orders.orders:
            for o in orders.orders:
                active.append({
                    'order_index': getattr(o, 'order_index', None),
                    'price': getattr(o, 'price', None),
                    'base_amount': getattr(o, 'base_amount', None),
                    'is_ask': getattr(o, 'is_ask', None),
                    'order_type': getattr(o, 'order_type', None),
                    'status': getattr(o, 'status', None),
                })
        return active
    except Exception as e:
        logging.warning(f"⚠️ Could not check active orders for market {market_id}: {e}")
        return []
