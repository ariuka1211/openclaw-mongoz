"""
BTC Smart Grid - Capital Management Mixin

Contains compounding, deployment with position, position recovery,
and existing order adoption logic.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from api.lighter import LighterAPI
from core.calculator import calculate_grid
from notifications.alerts import send_alert
from indicators import calc_atr, calc_ema_single


class CapitalMixin:
    def _compounding_factor(self) -> float:
        """Calculate a compounding multiplier based on PnL history.

        Returns:
          - >1.0 if grid is profitable (scale up sizing)
          - <1.0 if grid is losing (scale down sizing)
          - 1.0 on startup (no history)

        Factors:
          1. Win rate (weight: 40%)
          2. Avg PnL per trade as % of equity (weight: 30%)
          3. Recent momentum - last 5 trades vs all trades (weight: 30%)
        """
        trades = self.state.get("trades", [])
        if len(trades) < 3:
            return 1.0  # Not enough data

        equity_at_reset = self.state.get("equity_at_reset", 0)
        if equity_at_reset <= 0:
            return 1.0

        # --- Factor 1: Win rate ---
        wins = sum(1 for t in trades if t.get("pnl", 0) >= 0)
        win_rate = wins / len(trades)

        if win_rate >= 0.70:
            win_score = 1.0
        elif win_rate >= 0.60:
            win_score = 0.5
        elif win_rate >= 0.50:
            win_score = 0.0
        else:
            win_score = -0.5

        # --- Factor 2: Avg PnL per trade as % of equity ---
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        avg_pnl_pct = total_pnl / equity_at_reset / len(trades) * 100  # percent per trade

        if avg_pnl_pct >= 0.5:
            pnl_score = 1.0
        elif avg_pnl_pct >= 0.1:
            pnl_score = 0.5
        elif avg_pnl_pct >= 0:
            pnl_score = 0.0
        else:
            pnl_score = -1.0

        # --- Factor 3: Recent momentum (last 5 vs all) ---
        last_n = trades[-5:] if len(trades) > 5 else trades
        recent_wr = sum(1 for t in last_n if t.get("pnl", 0) >= 0) / len(last_n)
        all_wr = win_rate

        if recent_wr > all_wr + 0.1:
            momentum_score = 1.0  # improving
        elif recent_wr > all_wr:
            momentum_score = 0.5  # slightly improving
        elif recent_wr > all_wr - 0.1:
            momentum_score = 0.0  # flat
        else:
            momentum_score = -1.0  # deteriorating

        # --- Combine with weights ---
        compound_score = 0.4 * win_score + 0.3 * pnl_score + 0.3 * momentum_score

        # Map score [-1, 1] to multiplier [0.7, 1.3]
        # -1.0 → 0.7x, 0.0 → 1.0x, +1.0 → 1.3x
        mult = 1.0 + 0.3 * compound_score
        mult = max(0.7, min(1.3, mult))  # clamp

        return round(mult, 3)

    async def adopt_existing_orders(self, btc_price: float) -> bool:
        """Check for existing grid orders on the exchange and adopt them into our state.

        If valid grid orders exist, reconstruct state from them.
        Returns True if orders were adopted, False if no valid orders found.
        """
        try:
            live_orders = await self.api.get_open_orders()
        except Exception as e:
            logging.error(f"Failed to fetch open orders on startup: {e}")
            return False

        # Filter to grid-layer orders (orders that look like ours based on side/price patterns)
        # Since we didn't have order tagging before, we detect by structure:
        # Grid orders should have multiple orders on each side at regular-ish intervals
        buy_orders = [o for o in live_orders if o["side"] == "buy"]
        sell_orders = [o for o in live_orders if o["side"] == "sell"]

        # Need at least 2 buy + 2 sell orders to consider it a valid grid
        if len(buy_orders) < 2 or len(sell_orders) < 2:
            logging.info(f"Existing orders don't look like a grid ({len(buy_orders)} buy, {len(sell_orders)} sell) - will deploy new")
            return False

        # Reconstruct levels and order state
        orders = []
        for o in buy_orders:
            o["status"] = "open"
            o["layer"] = "grid"  # tag them
            orders.append(o)
        for o in sell_orders:
            o["status"] = "open"
            o["layer"] = "grid"  # tag them
            orders.append(o)

        buy_prices = sorted([o["price"] for o in buy_orders])
        sell_prices = sorted([o["price"] for o in sell_orders])

        # Calculate range
        range_low = min(buy_prices)
        range_high = max(sell_prices)

        # Detect size_per_level (all orders should have same size in a grid)
        all_sizes = [o["size"] for o in orders]
        avg_size = sum(all_sizes) / len(all_sizes) if all_sizes else 0

        equity = self.state.get("equity_at_reset", 0)
        if equity == 0:
            try:
                equity = await self.api.get_equity()
            except Exception:
                pass

        # Update state
        self.state.update({
            "active": True,
            "paused": False,
            "pause_reason": "",
            "levels": {"buy": buy_prices, "sell": sell_prices},
            "range_low": range_low,
            "range_high": range_high,
            "size_per_level": round(avg_size, 6),
            "orders": orders,
            "equity_at_reset": equity,
            "realized_pnl": self.state.get("realized_pnl", 0.0),
            "trades": self.state.get("trades", []),
            "pending_buys": self.state.get("pending_buys", []),
            "trend_warning_count": 0,
            "roll_count": self.state.get("roll_count", 0),
        })
        # Don't overwrite last_reset - it's still the original deployment time

        self._save_state()

        num_adopted = len(orders)
        logging.info(f"Adopted {num_adopted} existing orders ({len(buy_orders)} buy + {len(sell_orders)} sell)")
        return True

    async def recover_position(self, btc_amount: float, btc_price: float, cfg: dict) -> dict:
        """Recover from a naked BTC position (long or short).

        Long position  → place SELL orders to close.
        Short position → place BUY orders (cover). Both use absolute sizes.

        Position sizing tiers (based on absolute position value):
        - <50% of equity: close fast, at/near current price
        - 50-200% of equity: ladder away from current price
        - >200% of equity: aggressive exit - all at/below current price

        Returns:
            {"safe": bool, "sell_levels": [...], "buy_levels": [...], "reason": ""}
        """
        # First, cancel any existing stale orders
        await self.api.cancel_all_orders()

        pos_value_usd = abs(btc_amount) * btc_price
        equity = await self.api.get_equity()
        pos_pct = pos_value_usd / equity if equity > 0 else 0

        pos_cfg = cfg.get("position", {})
        close_threshold = pos_cfg.get("close_threshold_pct", 0.50)

        # Detect long vs short
        is_short = btc_amount < 0
        exit_side = "buy" if is_short else "sell"
        exit_action = "cover" if is_short else "close"

        # ── ATR-based spacing for exit laddering ───────────────────
        try:
            from analysis.analyst import fetch_candles
            from indicators import calc_atr
            candles_15m = await fetch_candles("15m", limit=100)
            atr_data = calc_atr(candles_15m, period=14)
            atr = atr_data.get("atr", 0)
        except Exception:
            atr = btc_price * 0.005  # fallback: 0.5% of price

        # ── Tier-based exit strategy ───────────────────────────────
        spacing = max(atr * 0.5, btc_price * 0.002)  # half ATR or 0.2%
        exit_levels = []
        exit_sizes = []
        abs_btc = abs(btc_amount)

        if pos_pct > 2.0:
            # Very large (>200% equity) - exit ASAP
            num_exits = 3
            exit_size = abs_btc / num_exits
            for i in range(num_exits):
                if is_short:
                    # Short: BUY at/above current to cover
                    price = btc_price + (spacing * i * 0.5)
                else:
                    # Long: SELL at/below current to close
                    price = btc_price - (spacing * (num_exits - 1 - i))
                exit_levels.append(round(price, 0))
                exit_sizes.append(exit_size)
            exit_sizes[-1] = abs_btc - sum(exit_sizes[:-1])  # fix rounding
            msg = (
                f"🚨 CRITICAL: {'Short' if is_short else 'Long'} position {abs_btc:.6f} BTC "
                f"(${pos_value_usd:.2f}) is {pos_pct:.0%} of equity\n"
                f"Aggressive exit: {num_exits} {exit_side}s to {exit_action}"
            )
        elif pos_pct > close_threshold:
            # Large (close_threshold-200% equity) - ladder exits
            num_exits = 4
            exit_size = abs_btc / num_exits
            for i in range(num_exits):
                if is_short:
                    # Short: BUY ladder above current
                    price = btc_price + (spacing * i * 0.7)
                else:
                    # Long: SELL ladder above current
                    price = btc_price + (spacing * i * 0.7)
                exit_levels.append(round(price, 0))
                exit_sizes.append(exit_size)
            exit_sizes[-1] = abs_btc - sum(exit_sizes[:-1])
            msg = (
                f"⚠️ Large {'short' if is_short else 'long'} position: {abs_btc:.6f} BTC "
                f"(${pos_value_usd:.2f}) is {pos_pct:.0%} of equity\n"
                f"Ladder exit: {num_exits} {exit_side}s from ${exit_levels[0]:,.0f} to ${exit_levels[-1]:,.0f}"
            )
        else:
            # Small (<close_threshold equity) - close fast
            num_exits = 2
            exit_size = abs_btc / num_exits
            for i in range(num_exits):
                if is_short:
                    # Short: BUY at current and slightly above
                    price = btc_price + (spacing * 0.5 * i)
                else:
                    # Long: SELL at current and slightly above
                    price = btc_price + (spacing * 0.5 * i)
                exit_levels.append(round(price, 0))
                exit_sizes.append(exit_size)
            exit_sizes[-1] = abs_btc - sum(exit_sizes[:-1])
            msg = (
                f"{'Short' if is_short else 'Long'} position: {abs_btc:.6f} BTC "
                f"(${pos_value_usd:.2f}) is {pos_pct:.0%} of equity\n"
                f"Quick exit: {num_exits} {exit_side}s at ${exit_levels[0]:,.0f} / ${exit_levels[-1]:,.0f}"
            )

        # ── Place exit orders (buys for shorts, sells for longs) ──
        placed = []
        for i, (price, size) in enumerate(zip(exit_levels, exit_sizes)):
            try:
                order = await self.api.place_limit_order(exit_side, price, size)
                order["status"] = "open"
                order["layer"] = "recovery"
                order["exit_order"] = True  # mark as position exit, not grid
                placed.append(order)
                logging.info(f"Placed EXIT {exit_side.upper()} @ ${price:,.0f} · {size:.6f} BTC")
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to place exit {exit_side.upper()} @ ${price}: {e}")

        # ── State update ───────────────────────────────────────────
        if is_short:
            level_state = {"buy": exit_levels, "sell": []}
        else:
            level_state = {"buy": [], "sell": exit_levels}

        self.state.update({
            "active": True,
            "paused": False,
            "pause_reason": "",
            "levels": level_state,
            "range_low": min(exit_levels),
            "range_high": max(exit_levels),
            "size_per_level": exit_sizes[0],
            "orders": placed,
            "equity_at_reset": equity,
            "peak_equity": equity,  # starts at reset equity
            "recovering_position": True,
            "position_size_btc": btc_amount,
            "position_value_usd": pos_value_usd,
        })
        self._save_state()

        await send_alert(f"♻️ Position recovery deployed\n{msg}")

        return {
            "safe": True,
            "sell_levels": [] if is_short else exit_levels,
            "buy_levels": exit_levels if is_short else [],
            "position_size": btc_amount,
            "position_value": pos_value_usd,
        }

    async def deploy_with_position(self, btc_amount: float, btc_price: float, equity: float, cfg: dict, funding_adj: float = 1.0):
        """Deploy a fresh grid while accounting for a small existing long position.

        Run AI analysis, then adjust sizing and levels to incorporate the position.

        Returns:
            {"buy_levels": [...], "sell_levels": [...], "reason": ""}
        """
        from analysis.analyst import run_analyst
        from analysis.direction import check_direction

        pos_value = btc_amount * btc_price
        pos_pct = pos_value / equity if equity > 0 else 0

        logging.info(f"Deploying grid with small position: {btc_amount:.6f} BTC = ${pos_value:.2f} ({pos_pct:.1%} equity)")

        # Cancel any stale orders
        await self.api.cancel_all_orders()

        # Run AI analysis for grid levels
        levels = await run_analyst(cfg)

        # Multi-signal direction check
        direction_ok = "neutral_prefer_long"
        try:
            dir_result = await check_direction(cfg, btc_price)
            direction_ok = dir_result.get("recommendation", "neutral_prefer_long")
        except Exception:
            logging.warning("Direction check failed, defaulting to neutral_prefer_long")
        if direction_ok == "pause":
            # Strong bearish signals
            if abs(pos_pct) < 0.01:
                # Dust position — return empty, no grid this cycle
                logging.warning(f"Direction score says pause with dust position ({pos_pct:.1%}) — no grid deployed. Returning empty levels.")
                return {"buy_levels": [], "sell_levels": [], "reason": "Direction pause with dust position"}
            # Real position — better to close position than add more
            logging.warning(f"Direction score says pause + position exists - deploying recovery instead")
            return await self.recover_position(btc_amount, btc_price, cfg)

        buy_levels = sorted(levels["buy_levels"])
        sell_levels = sorted(levels["sell_levels"])

        # Add an extra buy level below for position averaging (slightly reduce size_per_level to account for position risk)
        # The existing position acts like an already-filled buy at the current price
        # We'll add one more buy below the lowest level to DCA if price dips
        from core.calculator import calculate_grid
        num_buy = len(buy_levels)
        num_sell = len(sell_levels)

        # Compute ATR for volatility sizing
        atr_pct = None
        try:
            from analysis.analyst import fetch_candles
            from indicators import calc_atr
            candles_15m = await fetch_candles("15m", limit=100)
            atr_data = calc_atr(candles_15m, period=14)
            if atr_data.get("atr", 0) > 0 and btc_price > 0:
                atr_pct = atr_data["atr"] / btc_price
        except Exception:
            pass

        vol_cfg = cfg.get("volatility", {})

        # Auto-compounding multiplier
        compounding_mult = self._compounding_factor()

        calc = calculate_grid(
            equity, btc_price, num_buy, num_sell,
            cfg["capital"]["max_exposure_multiplier"],
            cfg["capital"]["margin_reserve_pct"],
            atr_pct=atr_pct,
            vol_cfg=vol_cfg if vol_cfg else None,
            compounding_mult=compounding_mult,
            funding_adj=funding_adj,
        )
        if not calc["safe"]:
            logging.error(f"Capital check failed with position: {calc['reason']}")
            raise RuntimeError(f"Safety check failed: {calc['reason']}")

        size_per_level = calc["size_per_level"]

        # Place orders: all buy/sell levels + tag that we have a position
        placed = []

        for price_lvl in buy_levels:
            try:
                order = await self.api.place_limit_order("buy", price_lvl, size_per_level)
                order["status"] = "open"
                order["layer"] = "grid"
                placed.append(order)
                logging.info(f"Placed BUY @ ${price_lvl:,.0f}")
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to place BUY @ ${price_lvl}: {e}")

        for price_lvl in sell_levels:
            try:
                order = await self.api.place_limit_order("sell", price_lvl, size_per_level)
                order["status"] = "open"
                order["layer"] = "grid"
                placed.append(order)
                logging.info(f"Placed SELL @ ${price_lvl:,.0f}")
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to place SELL @ ${price_lvl}: {e}")

        # Update state - note the existing position
        self.state.update({
            "active": True,
            "paused": False,
            "pause_reason": "",
            "levels": {"buy": buy_levels, "sell": sell_levels},
            "range_low": min(buy_levels),
            "range_high": max(sell_levels),
            "size_per_level": size_per_level,
            "orders": placed,
            "equity_at_reset": equity,
            "peak_equity": equity,  # starts at reset equity
            "recovering_position": False,
            "has_existing_position": True,
            "position_size_btc": btc_amount,
            "position_value_usd": pos_value,
            "position_avg_price": btc_price,  # approximate
        })
        self._save_state()

        await send_alert(
            f"📎 Grid with existing position ({btc_amount:.6f} BTC = ${pos_value:.2f}, {pos_pct:.1%} equity)\n"
            f"Range: ${min(buy_levels):,.0f}-${max(sell_levels):,.0f}\n"
            f"{len(buy_levels)} buys + {len(sell_levels)} sells · {size_per_level:.5f} BTC/level"
        )

        return {
            "buy_levels": buy_levels,
            "sell_levels": sell_levels,
            "reason": "",
        }
