"""
BTC Smart Grid — Grid Manager

Responsibilities:
- Place buy/sell limit orders at AI-provided levels
- Poll for fills every 30s
- On fill: place replacement order one level up/down
- Pause if price breaks outside range
- Daily reset: cancel all → accept new levels → redeploy
"""

import asyncio
import copy
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lighter_api import LighterAPI
from calculator import calculate_grid
from telegram import send_alert
from indicators import calc_bollinger_bands, calc_atr, calc_trend_skew

STATE_FILE = Path("state/grid_state.json")


class GridManager:
    def __init__(self, cfg: dict, api: LighterAPI):
        self.cfg = cfg
        self.api = api
        self.state = self._load_state()
        self._running = False

    # ── State management ────────────────────────────────────────

    def _load_state(self) -> dict:
        """Load state from disk or return empty default."""
        STATE_FILE.parent.mkdir(exist_ok=True)
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
        return {
            "active": False,
            "paused": False,
            "pause_reason": "",
            "levels": {"buy": [], "sell": []},
            "range_low": 0,
            "range_high": 0,
            "size_per_level": 0,
            "orders": [],
            "last_reset": None,
            "daily_pnl": 0.0,
            "fill_count": 0,
            "equity_at_reset": 0.0,
            "roll_count": 0,
            "last_roll": None,
        }

    def _save_state(self):
        """Persist state to disk."""
        STATE_FILE.parent.mkdir(exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    # ── Grid deployment ─────────────────────────────────────────

    async def deploy(self, levels: dict, equity: float, btc_price: float):
        """
        Deploy a new grid from AI analyst output.

        levels: {
            "buy_levels": [81200, 81800, 82400],
            "sell_levels": [83100, 83700, 84200],
            "range_low": 81200,
            "range_high": 84200,
            "pause": False
        }
        """
        if levels.get("pause"):
            await send_alert(f"⚠️ AI Analyst recommends pause: {levels.get('pause_reason', 'no reason given')}")
            self.state["paused"] = True
            self.state["pause_reason"] = levels.get("pause_reason", "AI recommendation")
            self._save_state()
            return

        buy_levels = sorted(levels["buy_levels"])    # ascending
        sell_levels = sorted(levels["sell_levels"])  # ascending
        num_buy = len(buy_levels)
        num_sell = len(sell_levels)

        min_levels = self.cfg["grid"].get("min_levels", 2)
        max_levels = self.cfg["grid"].get("max_levels", 8)
        total = num_buy + num_sell
        if total < min_levels:
            msg = f"Too few grid levels ({total} < {min_levels} min). Grid not deployed."
            logging.error(msg)
            await send_alert(msg)
            return
        if total > max_levels:
            logging.warning(f"Grid levels exceed max ({total} > {max_levels}) — deploying anyway")

        # Run capital calculator
        calc = calculate_grid(
            equity, btc_price, num_buy, num_sell,
            self.cfg["capital"]["max_exposure_multiplier"],
            self.cfg["capital"]["margin_reserve_pct"],
        )
        if not calc["safe"]:
            msg = f"🚨 Capital check FAILED: {calc['reason']}. Grid not deployed."
            logging.error(msg)
            await send_alert(msg)
            return

        size = calc["size_per_level"]
        logging.info(f"Deploying grid: {num_buy} buys + {num_sell} sells, size={size:.6f} BTC/level")

        # Cancel existing orders first
        cancelled = await self.api.cancel_all_orders()
        if cancelled > 0:
            logging.info(f"Cancelled {cancelled} existing orders")

        # Place all orders
        placed_orders = []
        for price in buy_levels:
            try:
                order = await self.api.place_limit_order("buy", price, size)
                order["status"] = "open"
                order["layer"] = "grid"
                placed_orders.append(order)
                logging.info(f"Placed BUY @ ${price:,.0f}")
                await asyncio.sleep(0.3)  # pace order placement
            except Exception as e:
                logging.error(f"Failed to place BUY @ ${price}: {e}")

        for price in sell_levels:
            try:
                order = await self.api.place_limit_order("sell", price, size)
                order["status"] = "open"
                order["layer"] = "grid"
                placed_orders.append(order)
                logging.info(f"Placed SELL @ ${price:,.0f}")
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to place SELL @ ${price}: {e}")

        # Update state
        self.state.update({
            "active": True,
            "paused": False,
            "pause_reason": "",
            "levels": {"buy": buy_levels, "sell": sell_levels},
            "range_low": levels.get("range_low", min(buy_levels)),
            "range_high": levels.get("range_high", max(sell_levels)),
            "size_per_level": size,
            "orders": placed_orders,
            "last_reset": datetime.now(timezone.utc).isoformat(),
            "equity_at_reset": equity,
            "daily_pnl": 0.0,
            "fill_count": 0,
        })
        self._save_state()

        alert = (
            f"📊 Grid deployed · BTC @ ${btc_price:,.0f}\n"
            f"Range: ${min(buy_levels):,.0f} – ${max(sell_levels):,.0f}\n"
            f"{num_buy} buys + {num_sell} sells · {size:.5f} BTC/level"
        )
        await send_alert(alert)

    # ── Fill detection ───────────────────────────────────────────

    async def check_fills(self, btc_price: float):
        """
        Compare current open orders against state.
        If an order is no longer on the exchange → it filled.
        Place replacement order.
        
        Matching is by (side, price) since the exchange assigns order_ids server-side
        and our place_limit_order doesn't get the real order_id back.
        """
        if not self.state["active"] or self.state["paused"]:
            return

        # Check if price is near band edge → trigger roll instead of pause
        range_low = self.state["range_low"]
        range_high = self.state["range_high"]
        
        # Use 5% buffer inside the range — roll before price fully exits
        buffer = (range_high - range_low) * 0.10  # 10% of range
        
        # Enforce minimum 5-minute cooldown between rolls
        last_roll = self.state.get("last_roll")
        if last_roll:
            try:
                from datetime import datetime
                last_roll_time = datetime.fromisoformat(last_roll)
                if datetime.now(timezone.utc) - last_roll_time < timedelta(minutes=5):
                    logging.info("Roll cooldown active — skipping roll check")
                    return  # Don't roll yet
            except Exception:
                pass  # If parsing fails, allow roll
        
        near_top = btc_price >= range_high - buffer
        near_bottom = btc_price <= range_low + buffer
        
        if near_top or near_bottom:
            direction = "up" if near_top else "down"
            logging.info(f"Price ${btc_price:,.0f} near {direction} band edge — triggering grid roll")
            await self.roll_grid(btc_price, direction)
            return

        # Get current open orders from exchange
        live_orders = await self.api.get_open_orders()

        # Build set of (side, price_rounded) from live orders
        live_keys = set()
        for o in live_orders:
            live_keys.add((o["side"], round(o["price"], 0)))

        # Update our state orders with real order_ids from exchange
        for order in self.state["orders"]:
            if order["status"] == "open":
                key = (order["side"], round(order["price"], 0))
                for lo in live_orders:
                    if lo["side"] == order["side"] and round(lo["price"], 0) == round(order["price"], 0):
                        order["order_id"] = lo["order_id"]  # update with real exchange order_id
                        break

        buy_levels = self.state["levels"]["buy"]
        sell_levels = self.state["levels"]["sell"]
        size = self.state["size_per_level"]

        # Collect existing open prices to avoid duplicate replacements
        open_prices = {(o["side"], round(o["price"], 0)) for o in live_orders}

        fills_processed = []
        for order in self.state["orders"]:
            if order["status"] != "open":
                continue
            key = (order["side"], round(order["price"], 0))
            if key not in live_keys:
                # This order is no longer on the exchange → filled
                order["status"] = "filled"
                fills_processed.append(order)
                logging.info(f"Fill detected: {order['side'].upper()} @ ${order['price']:,.0f}")
                await send_alert(
                    f"✅ {order['side'].upper()} filled @ ${order['price']:,.0f} · {size:.5f} BTC"
                )

        # Place replacements for each fill
        for order in fills_processed:
            await self._place_replacement(order, buy_levels, sell_levels, size, open_prices)

        # Update daily PnL from equity delta
        try:
            equity = await self.api.get_equity()
            equity_at_reset = self.state.get("equity_at_reset", equity)
            self.state["daily_pnl"] = round(equity - equity_at_reset, 2)
        except Exception:
            pass  # Skip PnL update if equity fetch fails

        # Increment fill count
        self.state["fill_count"] = self.state.get("fill_count", 0) + len(fills_processed)

        # Remove filled orders from state to prevent re-detection
        self.state["orders"] = [o for o in self.state["orders"] if o.get("status") != "filled"]
        self._save_state()

    async def _place_replacement(self, filled_order: dict, buy_levels: list, sell_levels: list, size: float, open_prices: set):
        """After a fill, place the next order in the grid.

        Implements retry logic with exponential backoff (up to 3 retries) and cancels the filled order
        if all retries fail to prevent unintended positions.
        """
        filled_price = filled_order["price"]
        filled_side = filled_order["side"]
        filled_order_id = filled_order.get("order_id")

        # If we don't have the filled order ID, we can't cancel it later — abort
        if not filled_order_id:
            logging.error("Cannot cancel filled order: missing order_id")
            return

        try:
            if filled_side == "buy":
                # Buy filled → place sell at next level up
                higher = [p for p in sell_levels if p > filled_price]
                if not higher:
                    logging.info(f"No higher sell level after fill @ ${filled_price:,.0f}")
                    return
                target_price = min(higher)
                # Skip if sell already exists at target price
                if ("sell", round(target_price, 0)) in open_prices:
                    logging.info(f"Sell already open at ${target_price:,.0f}, skipping replacement")
                    return
                # Retry placing the sell order up to 3 times with exponential backoff
                max_retries = 3
                for attempt in range(max_retries + 1):
                    if attempt > 0:
                        wait_time = 2 ** attempt  # exponential backoff: 2, 4, 8 seconds
                        logging.info(f"Retrying replacement sell order (attempt {attempt}/{max_retries})")
                        await asyncio.sleep(wait_time)
                    try:
                        new_order = await self.api.place_limit_order("sell", target_price, size)
                        new_order["status"] = "open"
                        new_order["layer"] = "grid"
                        self.state["orders"].append(new_order)
                        self._save_state()
                        break  # success, exit retry loop
                    except Exception as e:
                        logging.error(f"Attempt {attempt + 1} failed: {e}")
                        if attempt == max_retries:
                            # All retries failed — bot now holds an unhedged long position.
                            # The buy already filled (removed from the book), cancel can't close it.
                            logging.error(f"All {max_retries} retries failed for sell replacement after BUY fill @ ${filled_price:,.0f}. Position is now UNHEDGED.")
                            await send_alert(
                                f"🚨 UNHEDGED POSITION · BUY filled @ ${filled_price:,.0f}\n"
                                f"SELL replacement failed after {max_retries} retries.\n"
                                f"Position is open — manual intervention required."
                            )
                        # Continue to next retry attempt
                else:
                    # Retry loop exited without success
                    logging.info("Retry loop exited without success")
            else:
                # Sell filled → place buy at next level down
                lower = [p for p in buy_levels if p < filled_price]
                if not lower:
                    logging.info(f"No lower buy level after fill @ ${filled_price:,.0f}")
                    return
                target_price = max(lower)
                # Skip if buy already exists at target price
                if ("buy", round(target_price, 0)) in open_prices:
                    logging.info(f"Buy already open at ${target_price:,.0f}, skipping replacement")
                    return
                # Retry placing the buy order up to 3 times with exponential backoff
                max_retries = 3
                for attempt in range(max_retries + 1):
                    if attempt > 0:
                        wait_time = 2 ** attempt  # exponential backoff: 2, 4, 8 seconds
                        logging.info(f"Retrying replacement buy order (attempt {attempt}/{max_retries})")
                        await asyncio.sleep(wait_time)
                    try:
                        new_order = await self.api.place_limit_order("buy", target_price, size)
                        new_order["status"] = "open"
                        new_order["layer"] = "grid"
                        self.state["orders"].append(new_order)
                        self._save_state()
                        break  # success, exit retry loop
                    except Exception as e:
                        logging.error(f"Attempt {attempt + 1} failed: {e}")
                        if attempt == max_retries:
                            # All retries failed — log error but do NOT cancel the filled sell order
                            # (since a filled sell closes a position, not opens one)
                            logging.error(f"All {max_retries} retries failed for buy replacement after SELL fill")
                            await send_alert(
                                f"⚠️ BUY REPLACEMENT FAILED · SELL filled @ ${filled_price:,.0f} · Replacement order failed"
                            )
        except Exception as e:
            logging.error(f"Unexpected error in _place_replacement: {e}")
            await send_alert(f"⚠️ Unexpected error in _place_replacement: {e}")

    # ── Rolling Grid ─────────────────────────────────────────────

    @staticmethod
    def generate_levels_from_bands(bands: dict, atr: dict, skew: dict, current_price: float) -> dict:
        """Generate grid levels from Bollinger Bands + ATR + Trend Skew.
        
        Returns same format as analyst output:
        {"buy_levels": [...], "sell_levels": [...], "range_low": X, "range_high": Y}
        """
        spacing = atr["suggested_spacing"]
        if spacing <= 0:
            spacing = current_price * 0.002  # fallback: 0.2% of price
        
        upper = bands["upper"]
        lower = bands["lower"]
        middle = bands["middle"]
        
        buy_pct = skew.get("buy_pct", 50) / 100.0
        sell_pct = skew.get("sell_pct", 50) / 100.0
        
        # Total levels: how many fit in the band range with ATR spacing
        total_range = upper - lower
        total_levels = max(4, min(12, int(total_range / spacing)))  # 4-12 levels max
        
        num_buys = max(2, int(total_levels * buy_pct))
        num_sells = max(2, int(total_levels * sell_pct))
        
        # Generate buy levels below current price (extend to lower band if needed)
        buy_levels = []
        price = current_price - spacing
        while len(buy_levels) < num_buys and price >= lower:
            buy_levels.append(round(price / 50) * 50)  # round to $50
            price -= spacing
        buy_levels.sort()
        
        # Generate sell levels above current price (extend to upper band if needed)
        sell_levels = []
        price = current_price + spacing
        while len(sell_levels) < num_sells and price <= upper:
            sell_levels.append(round(price / 50) * 50)  # round to $50
            price += spacing
        sell_levels.sort()
        
        # If we couldn't fit enough levels due to band constraints, place some at the edge
        if len(buy_levels) < 2 and current_price > lower:
            buy_levels = [round(lower / 50) * 50, round((current_price - spacing/2) / 50) * 50]
            buy_levels = sorted(set(buy_levels))
        if len(sell_levels) < 2 and current_price < upper:
            sell_levels = [round(upper / 50) * 50, round((current_price + spacing/2) / 50) * 50]
            sell_levels = sorted(set(sell_levels))
        
        return {
            "buy_levels": buy_levels,
            "sell_levels": sell_levels,
            "range_low": lower,
            "range_high": upper,
        }

    async def roll_grid(self, current_price: float, direction: str):
        """Roll the grid to follow price when it hits band edges.

        - Cancel old orders (backup state first)
        - Fetch fresh candle data and calculate indicators
        - Generate new grid levels from bands + ATR + skew
        - Deploy new grid
        - If any step fails, revert to previous grid state
        """
        logging.info(f"Rolling grid at ${current_price:,.0f}")
        
        # Backup current state in case we need to revert
        backup_state = copy.deepcopy(self.state)
        
        # Cancel existing orders first
        cancelled = await self.api.cancel_all_orders()
        logging.info(f"Cancelled {cancelled} old orders for roll")
        
        # Fetch fresh candles for indicator recalculation
        try:
            from analyst import fetch_candles
            from market_intel import gather_all_intel
            from indicators import gather_indicators
            
            candles_15m = await fetch_candles("15m", limit=200)
            candles_30m = await fetch_candles("30m", limit=200)
            candles_4h = await fetch_candles("4H", limit=48)
            market_intel = await gather_all_intel(self.cfg)
            
            indicators = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel)
            bands = indicators["bollinger"]
            atr = indicators["atr"]
            skew = indicators["skew"]
        except Exception as e:
            logging.error(f"Failed to fetch indicators for roll: {e}")
            await send_alert(f"⚠️ Grid roll failed: {e}. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return
        
        # Generate new levels from bands + ATR + skew
        new_levels = self.generate_levels_from_bands(bands, atr, skew, current_price)
        
        if not new_levels["buy_levels"] or not new_levels["sell_levels"]:
            logging.warning("Roll generated empty levels — reverting")
            await send_alert("⚠️ Grid roll generated no levels. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return
        
        # Get equity for sizing
        try:
            equity = await self.api.get_equity()
        except Exception as e:
            logging.error(f"Failed to get equity for roll: {e}")
            await send_alert(f"⚠️ Grid roll failed to fetch equity: {e}. Reverting.")
            await self._redeploy_backup(backup_state)
            return
        
        # Deploy new grid (reuses the deploy method)
        try:
            await self.deploy(new_levels, equity, current_price)
        except Exception as e:
            logging.error(f"Failed to deploy new grid: {e}")
            await send_alert(f"⚠️ Grid roll failed to deploy: {e}. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return
        
        # Track roll
        self.state["roll_count"] = self.state.get("roll_count", 0) + 1
        self.state["last_roll"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        
        await send_alert(
            (
                f"🔄 Grid rolled {direction} · BTC @ ${current_price:,.0f}\n"
                f"New range: ${min(new_levels['buy_levels']):,.0f} – ${max(new_levels['sell_levels']):,.0f}\n"
                f"Skew: {skew['buy_pct']}% buy / {skew['sell_pct']}% sell\n"
                f"Spacing: ${atr['suggested_spacing']:,.0f} (ATR-based)"
            )
        )



    async def _redeploy_backup(self, backup_state: dict):
        """Redeploy the grid from a backup state (used when roll fails)."""
        logging.info("Reverting to backup grid state")
        
        # Cancel any current orders (if any)
        try:
            await self.api.cancel_all_orders()
        except Exception as e:
            logging.error(f"Failed to cancel orders during revert: {e}")
            # Continue anyway - we'll try to redeploy on top of possibly existing orders
        
        # Restore state from backup
        self.state = backup_state.copy()
        
        # Redeploy using the backup levels and size
        try:
            await self.deploy(
                {"buy_levels": backup_state["levels"]["buy"], "sell_levels": backup_state["levels"]["sell"], "range_low": backup_state["range_low"], "range_high": backup_state["range_high"]},
                backup_state["equity_at_reset"] if "equity_at_reset" in backup_state else await self.api.get_equity(),
                await self.api.get_btc_price()
            )
        except Exception as e:
            logging.error(f"Failed to redeploy backup grid: {e}")
            await send_alert(f"⚠️ Failed to restore grid after roll failure: {e}")
            # If we can't redeploy, pause the bot
            self.state["paused"] = True
            self.state["active"] = False
            self.state["pause_reason"] = f"Backup redeploy failed: {e}"
            self._save_state()

    async def _pause(self, reason: str):
        """Cancel all orders and pause the grid."""
        logging.warning(f"Pausing grid: {reason}")
        cancelled = await self.api.cancel_all_orders()
        self.state["paused"] = True
        self.state["pause_reason"] = reason
        self.state["active"] = False
        self._save_state()
        await send_alert(f"⚠️ Grid paused: {reason}\nCancelled {cancelled} orders.")

    async def cancel_all(self):
        """Cancel all open orders (used on reset)."""
        return await self.api.cancel_all_orders()

    # ── Status ───────────────────────────────────────────────────

    def status_summary(self) -> str:
        """Return a human-readable status string."""
        s = self.state
        if not s["active"] and not s["paused"]:
            return "Grid: not deployed"
        open_orders = [o for o in s["orders"] if o["status"] == "open"]
        status = "PAUSED" if s["paused"] else "ACTIVE"
        rolls = s.get("roll_count", 0)
        return (
            f"Grid: {status}\n"
            f"Range: ${s['range_low']:,.0f}–${s['range_high']:,.0f}\n"
            f"Open orders: {len(open_orders)}\n"
            f"Fills today: {s.get('fill_count', 0)}\n"
            f"Daily PnL: ${s['daily_pnl']:.2f}\n"
            f"Rolls: {rolls}\n"
            f"Last reset: {s['last_reset']}"
        )