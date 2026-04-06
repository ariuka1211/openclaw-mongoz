"""BTC Grid Bot — Dynamic Grid Adjuster

Incremental grid modifications without full redeployment.
Wraps an existing GridManager instance.
"""

import asyncio
import logging
import copy
from datetime import datetime, timezone


class GridAdjuster:
    """Incremental grid adjuster that modifies existing grid instead of full redeploy."""
    
    def __init__(self, grid_manager):
        self.gm = grid_manager
        self.api = grid_manager.api

    async def roll_edge(self, direction: str, new_levels: list[float]) -> bool:
        """Roll one edge. direction='up' (new sells) or 'down' (new buys).
        Cancels existing orders on that edge, places new ones."""
        state = self.gm.state
        size = state.get("size_per_level", 0)
        
        if direction == "up":
            # Cancel existing sell orders, place new ones above price
            old_sells = [(o["order_id"], o["price"]) for o in state.get("orders", [])
                        if o.get("status") == "open" and o.get("side") == "sell"]
            for oid, price in old_sells:
                try:
                    await self.api.cancel_order(oid)
                    logging.info(f"Cancelled sell @ ${price:,.0f}")
                except Exception as e:
                    logging.warning(f"Failed to cancel sell: {e}")
                await asyncio.sleep(0.1)
            
            placed = []
            for price in new_levels:
                try:
                    order = await self.api.place_limit_order("sell", price, size)
                    order["status"] = "open"
                    order["layer"] = "grid"
                    placed.append(order)
                    logging.info(f"Placed roll sell @ ${price:,.0f}")
                except Exception as e:
                    logging.error(f"Failed to place roll sell @ ${price:,.0f}: {e}")
                await asyncio.sleep(0.3)
            
            state["levels"]["sell"] = new_levels
            state["orders"] = [o for o in state.get("orders", [])
                             if o.get("status") not in ("cancelled", "filled") and o.get("side") == "buy"]
            state["orders"].extend(placed)
            state["range_high"] = max(new_levels) if new_levels else state.get("range_high", 0)
            
        elif direction == "down":
            # Cancel existing buy orders, place new ones below price
            old_buys = [(o["order_id"], o["price"]) for o in state.get("orders", [])
                       if o.get("status") == "open" and o.get("side") == "buy"]
            for oid, price in old_buys:
                try:
                    await self.api.cancel_order(oid)
                    logging.info(f"Cancelled buy @ ${price:,.0f}")
                except Exception as e:
                    logging.warning(f"Failed to cancel buy: {e}")
                await asyncio.sleep(0.1)
            
            placed = []
            for price in new_levels:
                try:
                    order = await self.api.place_limit_order("buy", price, size)
                    order["status"] = "open"
                    order["layer"] = "grid"
                    placed.append(order)
                    logging.info(f"Placed roll buy @ ${price:,.0f}")
                except Exception as e:
                    logging.error(f"Failed to place roll buy @ ${price:,.0f}: {e}")
                await asyncio.sleep(0.3)
            
            state["levels"]["buy"] = new_levels
            state["orders"] = [o for o in state.get("orders", [])
                             if o.get("status") not in ("cancelled", "filled") and o.get("side") == "sell"]
            state["orders"].extend(placed)
            state["range_low"] = min(new_levels) if new_levels else state.get("range_low", 0)
        
        else:
            logging.error(f"Invalid roll direction: {direction}")
            return False
        
        # Update roll metadata
        state["roll_count"] = state.get("roll_count", 0) + 1
        state["last_roll"] = datetime.now(timezone.utc).isoformat()
        self.gm._save_state()
        return True

    async def reconcile_position(self) -> dict:
        """Check exchange vs local state, return discrepancy report."""
        try:
            exchange_btc = await self.api.get_btc_balance()
            local_btc = self.gm.state.get("position_size_btc", 0)
            
            local_orders = [o for o in self.gm.state.get("orders", []) if o.get("status") == "open"]
            exchange_orders = await self.api.get_open_orders()
            
            # Find orders on exchange but not tracked locally
            exchange_keys = {(o["side"], round(o["price"])) for o in exchange_orders}
            local_keys = {(o["side"], round(o["price"])) for o in local_orders}
            untracked = exchange_keys - local_keys
            
            # Find orders tracked locally but not on exchange (should have been filled)
            missing = local_keys - exchange_keys
            
            report = {
                "exchange_btc": round(exchange_btc, 6),
                "local_btc": round(local_btc, 6),
                "btc_discrepancy": round(exchange_btc - local_btc, 6),
                "untracked_orders": len(untracked),
                "missing_orders": len(missing),
                "needs_correction": untracked or missing or abs(exchange_btc - local_btc) > 0.0001,
            }
            
            if report["needs_correction"]:
                logging.warning(f"Position discrepancy: {report}")
            
            return report
        except Exception as e:
            logging.error(f"Reconciliation failed: {e}")
            return {"error": str(e), "needs_correction": False}

    def calculate_roll_levels(self, current_price: float, direction: str, 
                               num_levels: int = None, atr: float = None, 
                               spacing_atr: float = 0.5) -> list[float]:
        """Calculate new levels for a roll based on current price and ATR.
        direction: 'up' (sell levels above price) or 'down' (buy levels below price)"""
        if num_levels is None:
            num_levels = len(self.gm.state["levels"].get("sell" if direction == "up" else "buy", []))
        
        if atr is None:
            atr = current_price * 0.005  # fallback: 0.5% of price
        
        spacing = atr * spacing_atr
        
        if direction == "up":
            return [round(current_price + spacing * (i + 1), 1) for i in range(num_levels)]
        else:
            return [round(current_price - spacing * (i + 1), 1) for i in range(num_levels)]