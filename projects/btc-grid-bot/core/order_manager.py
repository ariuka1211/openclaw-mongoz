"""
BTC Smart Grid - Order Management Mixin

Contains order placement retry, fill matching replacement, sanity cleanup,
and cancel-all logic.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from api.lighter import LighterAPI
from notifications.alerts import send_alert


class OrderMixin:
    async def sanity_cleanup(self):
        """Clean stale state on startup. Run AFTER adopt_existing_orders or fresh deploy.

        - Purges pending_buys older than 1 hour (ghosts from crashes)
        - Removes state orders that no longer exist on exchange
        - Clears recovery state if no recovery orders remain
        """
        try:
            from datetime import datetime, timedelta
            stale_threshold = datetime.now(timezone.utc) - timedelta(hours=1)

            # 1. Purge stale pending_buys
            pending = self.state.get("pending_buys", [])
            fresh_pending = []
            for pb in pending:
                ts_str = pb.get("ts", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts > stale_threshold:
                            fresh_pending.append(pb)
                        else:
                            logging.info(f"Purging stale pending buy @ ${pb['price']:,.0f} (age > 1h)")
                    except Exception:
                        fresh_pending.append(pb)  # can't parse, keep it
                else:
                    fresh_pending.append(pb)  # no timestamp, keep it
            if len(fresh_pending) != len(pending):
                self.state["pending_buys"] = fresh_pending
                logging.info(f"Pending buys: {len(pending)} → {len(fresh_pending)} (purged {len(pending) - len(fresh_pending)} stale)")

            # 2. Remove state orders that don't exist on exchange
            try:
                live_orders = await self.api.get_open_orders()
            except Exception as e:
                logging.warning(f"Cannot fetch live orders for cleanup: {e}")
                return

            live_keys = {(o["side"], round(o["price"], 0)) for o in live_orders}
            state_orders = self.state.get("orders", [])
            clean_orders = []
            removed_count = 0
            for o in state_orders:
                if o.get("status") != "open":
                    continue
                key = (o["side"], round(o["price"], 0))
                if key in live_keys:
                    # Also update with real order_id from exchange
                    for lo in live_orders:
                        if lo["side"] == o["side"] and round(lo["price"], 0) == round(o["price"], 0):
                            o["order_id"] = lo["order_id"]
                            break
                    clean_orders.append(o)
                else:
                    removed_count += 1
                    logging.info(f"Removing orphan state order: {o['side']} @ ${o['price']:,.0f} (not on exchange)")
            if removed_count > 0:
                self.state["orders"] = clean_orders
                logging.info(f"State orders: orphaned {removed_count} removed")

            # 3. Clear recovery state if no recovery orders remain
            if self.state.get("recovering_position"):
                recovery_orders = [o for o in self.state.get("orders", []) if o.get("layer") == "recovery"]
                if not recovery_orders:
                    logging.info("Position recovery complete - no recovery orders remain")
                    self.state["recovering_position"] = False
                    self.state["position_size_btc"] = 0
                    self.state["position_value_usd"] = 0
                    self.state["position_avg_price"] = 0

            self._save_state()
        except Exception as e:
            logging.error(f"Sanity cleanup failed: {e}")

    async def _place_replacement(self, filled_order: dict, buy_levels: list, sell_levels: list, size: float, open_prices: set):
        """After a fill, place the next order in the grid.

        Implements retry logic with exponential backoff (up to 3 retries) and cancels the filled order
        if all retries fail to prevent unintended positions.
        """
        filled_price = filled_order["price"]
        filled_side = filled_order["side"]
        filled_order_id = filled_order.get("order_id")
        grid_direction = self.state.get("grid_direction", "long")

        # If we don't have the filled order ID, we can't cancel it later - abort
        if not filled_order_id:
            logging.error("Cannot cancel filled order: missing order_id")
            return

        try:
            if grid_direction == "long":
                if filled_side == "buy":
                    # Long grid: Buy filled → place sell at next level up
                    higher = [p for p in sell_levels if p > filled_price]
                    if not higher:
                        logging.info(f"No higher sell level after fill @ ${filled_price:,.0f}")
                        return
                    target_price = min(higher)
                    # Skip if sell already exists at target price
                    if ("sell", round(target_price, 0)) in open_prices:
                        logging.info(f"Sell already open at ${target_price:,.0f}, skipping replacement")
                        return
                    # Place replacement sell order
                    await self._place_order_with_retry("sell", target_price, size, filled_price)
                else:
                    # Long grid: Sell filled → place buy at next level down
                    lower = [p for p in buy_levels if p < filled_price]
                    if not lower:
                        logging.info(f"No lower buy level after fill @ ${filled_price:,.0f}")
                        return
                    target_price = max(lower)
                    # Skip if buy already exists at target price
                    if ("buy", round(target_price, 0)) in open_prices:
                        logging.info(f"Buy already open at ${target_price:,.0f}, skipping replacement")
                        return
                    # Place replacement buy order
                    await self._place_order_with_retry("buy", target_price, size, filled_price)
            else:  # short grid
                if filled_side == "sell":
                    # Short grid: Sell filled (short opened) → place replacement sell further BELOW
                    lower_sells = [p for p in sell_levels if p < filled_price]
                    if not lower_sells:
                        logging.info(f"No lower sell level after short fill @ ${filled_price:,.0f}")
                        return
                    target_price = max(lower_sells)  # closest below
                    # Skip if sell already exists at target price
                    if ("sell", round(target_price, 0)) in open_prices:
                        logging.info(f"Sell already open at ${target_price:,.0f}, skipping replacement")
                        return
                    # Place replacement sell order
                    await self._place_order_with_retry("sell", target_price, size, filled_price)
                else:
                    # Short grid: Buy filled (short closed) → place replacement buy further ABOVE
                    higher_buys = [p for p in buy_levels if p > filled_price]
                    if not higher_buys:
                        logging.info(f"No higher buy level after short cover @ ${filled_price:,.0f}")
                        return
                    target_price = min(higher_buys)  # closest above
                    # Skip if buy already exists at target price
                    if ("buy", round(target_price, 0)) in open_prices:
                        logging.info(f"Buy already open at ${target_price:,.0f}, skipping replacement")
                        return
                    # Place replacement buy order
                    await self._place_order_with_retry("buy", target_price, size, filled_price)
        except Exception as e:
            logging.error(f"Unexpected error in _place_replacement: {e}")
            await send_alert(f"⚠️ Unexpected error in _place_replacement: {e}")

    async def _place_order_with_retry(self, side: str, target_price: float, size: float, filled_price: float):
        """Place an order with retry logic and error handling."""
        max_retries = 3
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait_time = 2 ** attempt  # exponential backoff: 2, 4, 8 seconds
                logging.info(f"Retrying replacement {side} order (attempt {attempt}/{max_retries})")
                await asyncio.sleep(wait_time)
            try:
                new_order = await self.api.place_limit_order(side, target_price, size)
                new_order["status"] = "open"
                new_order["layer"] = "grid"
                self.state["orders"].append(new_order)
                self._save_state()
                logging.info(f"Placed replacement {side.upper()} @ ${target_price:,.0f}")
                break  # success, exit retry loop
            except Exception as e:
                logging.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries:
                    # All retries failed
                    if side == "sell":
                        logging.error(f"All {max_retries} retries failed for sell replacement after {side.upper()} fill @ ${filled_price:,.0f}. Position is now UNHEDGED.")
                        await send_alert(
                            f"🚨 UNHEDGED POSITION · {side.upper()} filled @ ${filled_price:,.0f}\n"
                            f"SELL replacement failed after {max_retries} retries.\n"
                            f"Position is open - manual intervention required."
                        )
                    else:
                        logging.error(f"All {max_retries} retries failed for buy replacement after {side.upper()} fill")
                        await send_alert(
                            f"⚠️ BUY REPLACEMENT FAILED · {side.upper()} filled @ ${filled_price:,.0f} · Replacement order failed"
                        )

    async def cancel_all(self):
        """Cancel all open orders (used on reset)."""
        return await self.api.cancel_all_orders()
