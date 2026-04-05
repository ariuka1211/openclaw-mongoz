"""
BTC Smart Grid - Grid Manager

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

from api.lighter import LighterAPI
from core.capital import CapitalMixin
from core.order_manager import OrderMixin
from core.calculator import calculate_grid
from notifications.alerts import send_alert
from indicators import calc_bollinger_bands, calc_atr, calc_trend_skew, calc_ema_single, calc_volume_profile
from analysis.analyst import fetch_candles

STATE_FILE = Path("state/grid_state.json")
DEPLOYMENT_LOG_DIR = Path("state/deployments")
MAX_DEPLOYMENT_LOGS = 50  # default, can be overridden by config


class GridManager(CapitalMixin, OrderMixin):
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
            "realized_pnl": 0.0,
            "trades": [],
            "pending_buys": [],
            "pending_sells": [],
            "equity_pnl": 0.0,
            "recovering_position": False,
            "position_size_btc": 0.0,
            "orphan_sells": [],
            "usdc_spent": 0.0,
            "usdc_received": 0.0,
            "position_value_usd": 0.0,
            "has_existing_position": False,
            "position_avg_price": 0.0,
            "grid_direction": "long",
            "deployment_log_path": None,
            "deploy_start_price": None,
        }

    def _save_state(self):
        """Persist state to disk."""
        STATE_FILE.parent.mkdir(exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    async def _update_previous_deployment_results(self):
        """Fill in results for the previous deployment log."""
        try:
            prev_log_path = self.state.get("deployment_log_path")
            if not prev_log_path or not Path(prev_log_path).exists():
                return  # No previous deployment to update

            with open(prev_log_path) as f:
                prev_log = json.load(f)

            # If result section already filled, skip
            if prev_log.get("result") is not None and prev_log["result"].get("realized_pnl") is not None:
                return

            # Gather results from current state
            trades = self.state.get("trades", [])
            win_rate = 0
            if trades:
                wins = sum(1 for t in trades if t.get("pnl", 0) >= 0)
                win_rate = round(wins / len(trades) * 100, 1)

            # Fetch live equity from API instead of stale state file
            current_equity = await self.api.get_equity()

            # Price change during deployment
            deploy_start_price = self.state.get("deploy_start_price")

            # Number of rolls
            roll_count = self.state.get("roll_count", 0)

            prev_log["result"] = {
                "realized_pnl": round(self.state.get("realized_pnl", 0), 2),
                "trades": len(trades),
                "win_rate": win_rate,
                "roll_count": roll_count,
                "final_equity": round(current_equity, 2),
                "deploy_start_price": deploy_start_price,
            }

            with open(prev_log_path, "w") as f:
                json.dump(prev_log, f, indent=2)
            logging.info(f"Updated previous deployment log results: {prev_log_path}")
        except Exception as e:
            logging.error(f"Failed to update previous deployment log: {e}")

    def _save_deployment_log(self, btc_price: float, equity: float, direction: str, signal_data: dict, levels: dict, size_per_level: float):
        """Save a deployment snapshot to state/deployments/."""
        try:
            DEPLOYMENT_LOG_DIR.mkdir(parents=True, exist_ok=True)

            # Build market context from levels
            market_context = {}
            if levels:
                market_context["num_buy_levels"] = len(levels.get("buy_levels", []))
                market_context["num_sell_levels"] = len(levels.get("sell_levels", []))
                market_context["range_low"] = levels.get("range_low", 0)
                market_context["range_high"] = levels.get("range_high", 0)

            # Build AI analyst summary
            analyst_result = signal_data.get("analyst", {}) if signal_data else {}
            ai_analyst = {
                "direction": analyst_result.get("direction", "unknown"),
                "pause": analyst_result.get("pause", False),
                "note": analyst_result.get("pause_reason", ""),
            }

            # Build direction score summary
            direction_score_data = signal_data.get("direction_score", {}) if signal_data else {}
            direction_score_summary = {
                "score": direction_score_data.get("score", 0),
                "direction": direction_score_data.get("direction", "neutral"),
                "confidence": direction_score_data.get("confidence", "low"),
                "recommendation": direction_score_data.get("recommendation", "neutral_prefer_long"),
                "breakdown": {},
            }
            breakdown = direction_score_data.get("breakdown", {})
            for key, val in breakdown.items():
                if isinstance(val, dict) and "score" in val:
                    direction_score_summary["breakdown"][key] = round(val["score"], 1)
                elif isinstance(val, (int, float)):
                    direction_score_summary["breakdown"][key] = round(val, 1)

            timestamp = datetime.now(timezone.utc).isoformat()
            deployment_id = timestamp.replace(":", "-").replace(".", "_")
            log_file = DEPLOYMENT_LOG_DIR / f"{deployment_id}.json"

            deployment_data = {
                "timestamp": timestamp,
                "btc_price": btc_price,
                "equity": round(equity, 2),
                "direction": direction,
                "direction_score": direction_score_summary,
                "ai_analyst": ai_analyst,
                "resolved_direction": signal_data.get("resolved_direction", direction) if signal_data else direction,
                "market_context": market_context,
                "grid_range": market_context.get("range_high", 0) - market_context.get("range_low", 0),
                "num_levels": market_context.get("num_buy_levels", 0) + market_context.get("num_sell_levels", 0),
                "size_per_level": size_per_level,
                "result": None,
            }

            with open(log_file, "w") as f:
                json.dump(deployment_data, f, indent=2)

            self.state["deployment_log_path"] = str(log_file)
            self.state["deploy_start_price"] = btc_price
            self._save_state()

            logging.info(f"Deployment log saved: {log_file}")
            self._cleanup_deployment_logs()
        except Exception as e:
            logging.error(f"Failed to save deployment log: {e}")

    def _cleanup_deployment_logs(self):
        """Remove old deployment logs, keeping only the most recent MAX_DEPLOYMENT_LOGS."""
        try:
            if not DEPLOYMENT_LOG_DIR.exists():
                return
            log_files = sorted(DEPLOYMENT_LOG_DIR.glob("*.json"))
            if len(log_files) > MAX_DEPLOYMENT_LOGS:
                for old_log in log_files[:-MAX_DEPLOYMENT_LOGS]:
                    old_log.unlink()
                    logging.debug(f"Removed old deployment log: {old_log}")
        except Exception as e:
            logging.error(f"Failed to cleanup deployment logs: {e}")

    # ── Grid deployment ─────────────────────────────────────────

    async def deploy(self, levels: dict, equity: float, btc_price: float, roll_info: dict | None = None, time_adj: float = 1.0, funding_adj: float = 1.0, direction: str = "long", signal_data: dict | None = None):
        """
        Deploy a new grid from AI analyst output.

        levels: {
            "buy_levels": [81200, 81800, 82400],
            "sell_levels": [83100, 83700, 84200],
            "range_low": 81200,
            "range_high": 84200,
            "pause": False
        }
        signal_data: dict with direction_score, analyst, resolved_direction
        """
        if direction == "short":
            return await self.deploy_short_grid(levels, equity, btc_price, roll_info, time_adj, funding_adj, signal_data)
        
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
            logging.warning(f"Grid levels exceed max ({total} > {max_levels}) - deploying anyway")

        # Compute ATR for volatility-adaptive sizing
        atr_pct = None
        try:
            candles_15m = await fetch_candles("15m", limit=100)
            atr_data = calc_atr(candles_15m, period=14)
            if atr_data.get("atr", 0) > 0 and btc_price > 0:
                atr_pct = atr_data["atr"] / btc_price
        except Exception:
            pass  # Fall back to non-adaptive sizing

        # Load vol config from main config if present
        vol_cfg = self.cfg.get("volatility", {})

        # Auto-compounding multiplier
        compounding_mult = self._compounding_factor()

        # Run capital calculator
        calc = calculate_grid(
            equity, btc_price, num_buy, num_sell,
            self.cfg["capital"]["max_exposure_multiplier"],
            self.cfg["capital"]["margin_reserve_pct"],
            atr_pct=atr_pct,
            vol_cfg=vol_cfg,
            compounding_mult=compounding_mult,
            time_adj=time_adj,
            funding_adj=funding_adj,
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
            "peak_equity": equity,  # starts at reset equity, only increases
            "daily_pnl": 0.0,
            "fill_count": 0,
            "grid_direction": direction,
        })
        if roll_info:
            self.state.update(roll_info)
        self._save_state()

        # Log deployment snapshot
        try:
            await self._update_previous_deployment_results()
            self._save_deployment_log(btc_price, equity, direction, signal_data, levels, size)
        except Exception as e:
            logging.error(f"Deployment logging failed (non-fatal): {e}")

        vol_info = ""
        if calc.get("vol_adj") is not None and calc["vol_adj"] != 1.0:
            atr_pct_val = calc.get("atr_pct", 0)
            vol_info = f"\nVol adj: {calc['vol_adj']:.2f}x (ATR {atr_pct_val:.2%})"

        direction_str = f" ({direction.upper()})" if direction != "long" else ""
        alert = (
            f"📊 Grid deployed{direction_str} · BTC @ ${btc_price:,.0f}\n"
            f"Range: ${min(buy_levels):,.0f} - ${max(sell_levels):,.0f}\n"
            f"{num_buy} buys + {num_sell} sells · {size:.5f} BTC/level{vol_info}"
        )
        await send_alert(alert)

    async def deploy_short_grid(self, levels: dict, equity: float, btc_price: float, roll_info: dict | None = None, time_adj: float = 1.0, funding_adj: float = 1.0, signal_data: dict | None = None):
        """
        Deploy a short grid from AI analyst output.
        
        For short grids:
        - buy_levels should be ABOVE current price (to close shorts)
        - sell_levels should be BELOW current price (to open shorts)
        - Place sell orders first (shorts opening)
        signal_data: dict with direction_score, analyst, resolved_direction
        """
        if levels.get("pause"):
            await send_alert(f"⚠️ AI Analyst recommends pause: {levels.get('pause_reason', 'no reason given')}")
            self.state["paused"] = True
            self.state["pause_reason"] = levels.get("pause_reason", "AI recommendation")
            self._save_state()
            return

        # For short grids, buy_levels are above price, sell_levels are below
        buy_levels = sorted([p for p in levels["buy_levels"] if p > btc_price])    # above price - close shorts
        sell_levels = sorted([p for p in levels["sell_levels"] if p < btc_price])  # below price - open shorts
        num_buy = len(buy_levels)
        num_sell = len(sell_levels)

        min_levels = self.cfg["grid"].get("min_levels", 2)
        max_levels = self.cfg["grid"].get("max_levels", 8)
        total = num_buy + num_sell
        if total < min_levels:
            msg = f"Too few short grid levels ({total} < {min_levels} min). Short grid not deployed."
            logging.error(msg)
            await send_alert(msg)
            return
        if total > max_levels:
            logging.warning(f"Short grid levels exceed max ({total} > {max_levels}) - deploying anyway")

        # Compute ATR for volatility-adaptive sizing
        atr_pct = None
        try:
            candles_15m = await fetch_candles("15m", limit=100)
            atr_data = calc_atr(candles_15m, period=14)
            if atr_data.get("atr", 0) > 0 and btc_price > 0:
                atr_pct = atr_data["atr"] / btc_price
        except Exception:
            pass  # Fall back to non-adaptive sizing

        # Load vol config from main config if present
        vol_cfg = self.cfg.get("volatility", {})

        # Auto-compounding multiplier
        compounding_mult = self._compounding_factor()

        # Run capital calculator
        calc = calculate_grid(
            equity, btc_price, num_buy, num_sell,
            self.cfg["capital"]["max_exposure_multiplier"],
            self.cfg["capital"]["margin_reserve_pct"],
            atr_pct=atr_pct,
            vol_cfg=vol_cfg,
            compounding_mult=compounding_mult,
            time_adj=time_adj,
            funding_adj=funding_adj,
        )
        if not calc["safe"]:
            msg = f"🚨 Capital check FAILED: {calc['reason']}. Short grid not deployed."
            logging.error(msg)
            await send_alert(msg)
            return

        size = calc["size_per_level"]
        logging.info(f"Deploying SHORT grid: {num_buy} buys + {num_sell} sells, size={size:.6f} BTC/level")

        # Cancel existing orders first
        cancelled = await self.api.cancel_all_orders()
        if cancelled > 0:
            logging.info(f"Cancelled {cancelled} existing orders")

        # Place SELL orders first (opening shorts)
        placed_orders = []
        for price in sell_levels:
            try:
                order = await self.api.place_limit_order("sell", price, size)
                order["status"] = "open"
                order["layer"] = "grid"
                placed_orders.append(order)
                logging.info(f"Placed SHORT SELL @ ${price:,.0f}")
                await asyncio.sleep(0.3)  # pace order placement
            except Exception as e:
                logging.error(f"Failed to place SHORT SELL @ ${price}: {e}")

        # Then place BUY orders (closing shorts)
        for price in buy_levels:
            try:
                order = await self.api.place_limit_order("buy", price, size)
                order["status"] = "open"
                order["layer"] = "grid"
                placed_orders.append(order)
                logging.info(f"Placed SHORT BUY @ ${price:,.0f}")
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to place SHORT BUY @ ${price}: {e}")

        # Update state
        self.state.update({
            "active": True,
            "paused": False,
            "pause_reason": "",
            "levels": {"buy": buy_levels, "sell": sell_levels},
            "range_low": min(sell_levels) if sell_levels else btc_price - 1000,
            "range_high": max(buy_levels) if buy_levels else btc_price + 1000,
            "size_per_level": size,
            "orders": placed_orders,
            "last_reset": datetime.now(timezone.utc).isoformat(),
            "equity_at_reset": equity,
            "peak_equity": equity,  # starts at reset equity, only increases
            "daily_pnl": 0.0,
            "fill_count": 0,
            "grid_direction": "short",
        })
        if roll_info:
            self.state.update(roll_info)
        self._save_state()

        # Log deployment snapshot
        try:
            await self._update_previous_deployment_results()
            self._save_deployment_log(btc_price, equity, "short", signal_data, levels, size)
        except Exception as e:
            logging.error(f"Deployment logging failed (non-fatal): {e}")

        vol_info = ""
        if calc.get("vol_adj") is not None and calc["vol_adj"] != 1.0:
            atr_pct_val = calc.get("atr_pct", 0)
            vol_info = f"\nVol adj: {calc['vol_adj']:.2f}x (ATR {atr_pct_val:.2%})"

        alert = (
            f"📊 Grid deployed (SHORT) · BTC @ ${btc_price:,.0f}\n"
            f"Range: ${min(sell_levels):,.0f} - ${max(buy_levels):,.0f}\n"
            f"{num_sell} sells (open) + {num_buy} buys (close) · {size:.5f} BTC/level{vol_info}"
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

        # ── Volume spike cooldown check ─────────────────────────────
        # If volume spike was detected in the last hour, skip trend-based
        # pause check and roll trigger - give the market time to settle.
        from datetime import datetime
        now = datetime.now(timezone.utc)
        cooldown_until = self.state.get("volume_spike_cooldown_until")
        in_spike_cooldown = False
        if cooldown_until:
            try:
                cooldown_dt = datetime.fromisoformat(cooldown_until)
                if now < cooldown_dt:
                    in_spike_cooldown = True
                    logging.info(f"Volume spike cooldown active until {cooldown_dt} - skipping trend checks")
                else:
                    # Cooldown expired, clear it
                    self.state["volume_spike_cooldown_until"] = None
                    self._save_state()
            except Exception:
                pass  # If parsing fails, proceed normally

        # Detect volume spike on 15m candles
        try:
            candles_15m = await fetch_candles("15m", limit=100)
            from indicators import detect_volume_spike
            spike_info = detect_volume_spike(candles_15m)
            if spike_info["is_spike"] and spike_info["mean_reversion_likely"]:
                # Set cooldown to 1 hour from now
                self.state["volume_spike_cooldown_until"] = (now + timedelta(hours=1)).isoformat()
                self._save_state()
                logging.info(
                    f"Volume spike detected: {spike_info['volume_ratio']:.2f}x avg, "
                    f"direction={spike_info['direction']}, cooldown set for 1h"
                )
        except Exception as e:
            logging.warning(f"Volume spike detection failed: {e}")

        # Check if price is near band edge → trigger roll instead of pause
        range_low = self.state["range_low"]
        range_high = self.state["range_high"]

        # Use 3% buffer inside the range - roll before price fully exits
        buffer = (range_high - range_low) * 0.03  # 3% of range - only roll when price is truly at the edge

        # Enforce minimum 5-minute cooldown between rolls
        last_roll = self.state.get("last_roll")
        if last_roll:
            try:
                from datetime import datetime
                last_roll_time = datetime.fromisoformat(last_roll)
                if datetime.now(timezone.utc) - last_roll_time < timedelta(minutes=5):
                    logging.info("Roll cooldown active - skipping roll check (less than 5 min since last roll)")
                    return  # Don't roll yet
            except Exception:
                pass  # If parsing fails, allow roll

        near_top = btc_price >= range_high - buffer
        near_bottom = btc_price <= range_low + buffer

        # ── Roll cap: max rolls per session ───────────────────────────
        roll_count = self.state.get("roll_count", 0)
        max_rolls = self.cfg.get("grid", {}).get("max_rolls_per_session", 2)
        roll_capped = roll_count >= max_rolls

        if not in_spike_cooldown and (near_top or near_bottom):
            direction = "up" if near_top else "down"
            if roll_capped:
                logging.info(f"Max rolls ({max_rolls}) reached this session — skipping roll, will wait for daily reset")
                buy_levels = self.state["levels"].get("buy", [])
                sell_levels = self.state["levels"].get("sell", [])
                size = self.state["size_per_level"]
            else:
                logging.info(f"Price ${btc_price:,.0f} near {direction} band edge — triggering grid roll")
                rolled = await self.roll_grid(btc_price, direction)
                buy_levels = self.state["levels"].get("buy", [])
                sell_levels = self.state["levels"].get("sell", [])
                size = self.state["size_per_level"]
        elif in_spike_cooldown and (near_top or near_bottom):
            logging.info(f"Price ${btc_price:,.0f} near band edge but volume spike cooldown active — skipping roll")
            buy_levels = self.state["levels"].get("buy", [])
            sell_levels = self.state["levels"].get("sell", [])
            size = self.state["size_per_level"]
        else:
            buy_levels = self.state["levels"].get("buy", [])
            sell_levels = self.state["levels"].get("sell", [])
            size = self.state["size_per_level"]

        # ── Trend-based pause check (sustained downtrend) ────────────
        # Skip during volume spike cooldown
        if not in_spike_cooldown:
            try:
                candles_4h = await fetch_candles("4H", limit=100)
                ema_50 = calc_ema_single(candles_4h, 50)
                trend_cfg = self.cfg.get("trend", {})
                if ema_50 is not None:
                    pct_below = (ema_50 - btc_price) / ema_50
                    pause_threshold = trend_cfg.get("pause_threshold_pct", 0.03)
                    if pct_below > pause_threshold:
                        count = self.state.get("trend_warning_count", 0) + 1
                        self.state["trend_warning_count"] = count
                        self._save_state()
                        if count >= 4:  # 4 cycles = 2 minutes sustained
                            await self._pause(f"Strong downtrend: price {pct_below:.1%} below 4h EMA(50)")
                            await send_alert("📉 Strong Downtrend. Grid paused.")
                            return
                    else:
                        self.state["trend_warning_count"] = 0
                        self._save_state()
            except Exception:
                pass  # Don't break the loop if trend check fails
        else:
            logging.info("Volume spike cooldown active — skipping trend-based pause check")

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

        # BUG 1 FIX: Orphan sell detection (long grid only)
        grid_direction = self.state.get("grid_direction", "long")
        position_size_btc = self.state.get("position_size_btc", 0.0)
        size_per_level = self.state.get("size_per_level", 0)

        # BUG 2 FIX: USDC balance tracker for accurate PnL
        usdc_spent = self.state.get("usdc_spent", 0.0)
        usdc_received = self.state.get("usdc_received", 0.0)

        # Track buy fills and detect orphan sells for long grid
        for order in fills_processed:
            if grid_direction == "long":
                if order["side"] == "buy":
                    self.state.setdefault("pending_buys", [])
                    self.state["pending_buys"].append({
                        "price": order["price"],
                        "size": size_per_level,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    # BUG 2: Track USDC spent on buys
                    usdc_spent += order["price"] * size_per_level
                    self.state["usdc_spent"] = round(usdc_spent, 2)
                    self._save_state()
                elif order["side"] == "sell":
                    # BUG 1: Check if we actually have BTC to sell
                    if position_size_btc >= size_per_level:
                        self.state.setdefault("pending_sells", [])
                        self.state["pending_sells"].append({
                            "price": order["price"],
                            "size": size_per_level,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        })
                        # BUG 2: Track USDC received on sells
                        usdc_received += order["price"] * size_per_level
                        self.state["usdc_received"] = round(usdc_received, 2)
                        self._save_state()
                    else:
                        logging.warning(
                            f"Sell filled without BTC — orphan fill at ${order['price']:,.0f} · size {size_per_level}"
                        )
                        await send_alert(
                            f"⚠️ Orphan sell fill at ${order['price']:,.0f} — no BTC to sell"
                        )
                        # Record orphan but do NOT track for PnL or replacement
                        self.state.setdefault("orphan_sells", []).append({
                            "price": order["price"],
                            "size": size_per_level,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        })
                        self._save_state()
            else:  # short grid
                if order["side"] == "sell":
                    self.state.setdefault("pending_sells", [])
                    self.state["pending_sells"].append({
                        "price": order["price"],
                        "size": size_per_level,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    self._save_state()

        # Match fills for realized PnL (direction-dependent) - for trade display only
        # BUG 2: real PnL is now usdc_received - usdc_spent (USDC tracker)
        for order in fills_processed:
            if grid_direction == "long":
                if order["side"] == "sell":
                    # Only match if this was a real sell (not orphan)
                    pending = self.state.get("pending_buys", [])
                    if pending:
                        buy = pending.pop(0)
                        buy_size = buy.get("size", self.state.get("size_per_level", 0))
                        pnl = (order["price"] - buy["price"]) * buy_size
                        trade = {
                            "buy_price": buy["price"],
                            "sell_price": order["price"],
                            "size": buy_size,
                            "pnl": round(pnl, 2),
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                        self.state.setdefault("trades", []).append(trade)
                        self.state["pending_buys"] = pending
                        # BUG 2: Update realized_pnl from USDC tracker (source of truth)
                        self.state["realized_pnl"] = round(usdc_received - usdc_spent, 2)
                        self._save_state()
            else:  # short grid
                if order["side"] == "buy":
                    pending = self.state.get("pending_sells", [])
                    if pending:
                        sell = pending.pop(0)
                        sell_size = sell.get("size", self.state.get("size_per_level", 0))
                        pnl = (sell["price"] - order["price"]) * sell_size  # PnL = (sell_entry - buy_exit) * size
                        trade = {
                            "sell_price": sell["price"],  # short entry
                            "buy_price": order["price"],   # short exit
                            "size": sell_size,
                            "pnl": round(pnl, 2),
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                        self.state.setdefault("trades", []).append(trade)
                        self.state["pending_sells"] = pending
                        # Short grid: realized_pnl stays from individual matching
                        self.state["realized_pnl"] = round(self.state.get("realized_pnl", 0) + pnl, 2)
                        self._save_state()

        # Place replacements for each fill
        for order in fills_processed:
            # Skip replacement for recovery exit orders
            if order.get("exit_order"):
                logging.info(f"Exit order filled - no replacement (position being closed)")
                continue
            # BUG 1 FIX: Skip replacement for orphan sells (no matching pending_buys)
            if grid_direction == "long" and order["side"] == "sell":
                if position_size_btc < size_per_level:
                    logging.info(f"Skipping replacement for orphan sell @ ${order['price']:,.0f}")
                    continue
            await self._place_replacement(order, buy_levels, sell_levels, size, open_prices)

        # Update PnL metrics
        try:
            equity = await self.api.get_equity()
            equity_at_reset = self.state.get("equity_at_reset", equity)
            self.state["equity_pnl"] = round(equity - equity_at_reset, 2)
            self.state["daily_pnl"] = round(self.state.get("realized_pnl", 0.0), 2)
        except Exception:
            pass  # Skip PnL update if equity fetch fails

        # Increment fill count
        self.state["fill_count"] = self.state.get("fill_count", 0) + len(fills_processed)

        # Remove filled orders from state to prevent re-detection
        self.state["orders"] = [o for o in self.state["orders"] if o.get("status") != "filled"]
        self._save_state()

        # ── Check if position recovery is complete ──────────────────
        if self.state.get("recovering_position") and self.state.get("position_size_btc", 0) > 0:
            open_recovery_orders = [
                o for o in self.state["orders"]
                if o.get("layer") == "recovery" and o.get("status") == "open"
            ]
            if not open_recovery_orders:
                # All exit sells filled - position is closed
                self.state["recovering_position"] = False
                self.state["position_size_btc"] = 0
                self.state["position_value_usd"] = 0
                self._save_state()
                logging.info("Position recovery complete - all exit sells filled")

    async def _compute_candidate_levels(self, current_price: float) -> tuple[dict | None, float, float, dict]:
        """Fetch candles and indicators, generate candidate grid levels.

        Returns None on any error (do not proceed with roll).
        Returns dict: {"buy_levels": [...], "sell_levels": [...], "range_low": X, "range_high": Y}
        """
        try:
            from analysis.analyst import fetch_candles
            from market.intel import gather_all_intel
            from indicators import gather_indicators

            candles_15m = await fetch_candles("15m", limit=200)
            candles_30m = await fetch_candles("30m", limit=200)
            candles_4h = await fetch_candles("4H", limit=48)
            try:
                candles_1d = await fetch_candles("1D", limit=90)
            except Exception:
                candles_1d = []
            market_intel = await gather_all_intel(self.cfg)

            indicators = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel, candles_1d)
            bands = indicators["bollinger"]
            atr = indicators["atr"]
            skew = indicators["skew"]
            time_adj = indicators.get("time_awareness", {}).get("adj_multiplier", 1.0)
            funding_adj = indicators.get("funding_adj", {}).get("adj_multiplier", 1.0)
            oi_div = indicators.get("oi_divergence", {"state": "neutral", "grid_implication": "none"})

            vp = indicators.get("volume_profile", {})
            return self.generate_levels_from_volume_profile(vp, bands, atr, skew, current_price), time_adj, funding_adj, oi_div
        except Exception as e:
            logging.error(f"Failed to compute candidate levels for roll: {e}")
            return None, 1.0, 1.0, {"state": "neutral", "grid_implication": "none"}

    @staticmethod
    def _levels_overlap(old_buy: list, old_sell: list, new_buy: list, new_sell: list) -> float:
        """Compute Jaccard-style overlap between old and new grid levels.

        Two levels 'match' if they are within 0.15% of each other.
        Returns overlap ratio: 0.0 = no overlap, 1.0 = identical.
        """
        def match_price(p):
            def matches(q):
                return abs(p - q) / max(p, q) < 0.0015  # 0.15% tolerance
            return matches

        old_all = old_buy + old_sell
        new_all = new_buy + new_sell

        if not old_all or not new_all:
            return 0.0

        # Count how many new levels match an old level
        matched_new = 0
        for np in new_all:
            if any(match_price(np)(op) for op in old_all):
                matched_new += 1

        return matched_new / len(new_all)

    # ── One-sided rolling ────────────────────────────────────────

    def _count_side_levels(self, side: str) -> int:
        """Count active open orders on one side (buy or sell)."""
        orders = self.state.get("orders", [])
        return sum(1 for o in orders if o.get("status") == "open" and o.get("side") == side)

    async def roll_one_sided(self, current_price: float, direction: str, new_levels: dict) -> bool:
        """Roll only one side of the grid, keeping the other side intact.

        Args:
            current_price: Current BTC price
            direction: "up" (price hit top - new sells needed) or "down" (price hit bottom - new buys needed)
            new_levels: Dict with keys "buy_levels" and "sell_levels" from volume profile or BB fallback

        Returns:
            True if roll was performed, False if error
        """
        try:
            logging.info(f"One-sided roll ({direction}) at ${current_price:,.0f}")

            size_per_level = self.state.get("size_per_level", 0)
            if size_per_level <= 0:
                logging.error("size_per_level is zero or negative - cannot roll")
                return False

            if direction == "up":
                # Price hit top - cancel SELL orders, place new SELL orders above current price
                # Keep BUY orders intact
                logging.info("Cancelling SELL orders, keeping BUY orders intact")

                # Cancel only sell orders
                cancelled_count = 0
                for o in self.state.get("orders", []):
                    if o.get("status") == "open" and o.get("side") == "sell":
                        try:
                            await self.api.cancel_order(o.get("order_id"))
                            o["status"] = "cancelled"
                            cancelled_count += 1
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            logging.error(f"Failed to cancel sell @ ${o['price']}: {e}")

                # Place new sell orders from the relevant side of new_levels
                new_sell_prices = new_levels.get("sell_levels", [])
                if not new_sell_prices:
                    logging.warning("No sell levels in new_levels - cannot complete one-sided roll")
                    return False

                placed = []
                for price in new_sell_prices:
                    if price <= current_price:
                        continue  # Skip levels not above current price
                    try:
                        order = await self.api.place_limit_order("sell", price, size_per_level)
                        order["status"] = "open"
                        order["layer"] = "grid"
                        placed.append(order)
                        logging.info(f"Placed SELL @ ${price:,.0f}")
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logging.error(f"Failed to place SELL @ ${price}: {e}")

                # Update state: replace sell side, keep buy side
                self.state["levels"]["sell"] = new_sell_prices
                # Update range_high based on new sell levels
                self.state["range_high"] = max(new_sell_prices) if new_sell_prices else self.state["range_high"]

                # Clean up cancelled orders, append new ones
                self.state["orders"] = [o for o in self.state["orders"] if o.get("status") != "cancelled"]
                self.state["orders"].extend(placed)

                logging.info(f"One-sided roll (up): cancelled {cancelled_count} sells, placed {len(placed)} new sells")

            elif direction == "down":
                # Price hit bottom - cancel BUY orders, place new BUY orders below current price
                # Keep SELL orders intact
                logging.info("Cancelling BUY orders, keeping SELL orders intact")

                # Cancel only buy orders
                cancelled_count = 0
                for o in self.state.get("orders", []):
                    if o.get("status") == "open" and o.get("side") == "buy":
                        try:
                            await self.api.cancel_order(o.get("order_id"))
                            o["status"] = "cancelled"
                            cancelled_count += 1
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            logging.error(f"Failed to cancel buy @ ${o['price']}: {e}")

                # Place new buy orders from the relevant side of new_levels
                new_buy_prices = new_levels.get("buy_levels", [])
                if not new_buy_prices:
                    logging.warning("No buy levels in new_levels - cannot complete one-sided roll")
                    return False

                placed = []
                for price in new_buy_prices:
                    if price >= current_price:
                        continue  # Skip levels not below current price
                    try:
                        order = await self.api.place_limit_order("buy", price, size_per_level)
                        order["status"] = "open"
                        order["layer"] = "grid"
                        placed.append(order)
                        logging.info(f"Placed BUY @ ${price:,.0f}")
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logging.error(f"Failed to place BUY @ ${price}: {e}")

                # Update state: replace buy side, keep sell side
                self.state["levels"]["buy"] = new_buy_prices
                # Update range_low based on new buy levels
                self.state["range_low"] = min(new_buy_prices) if new_buy_prices else self.state["range_low"]

                # Clean up cancelled orders, append new ones
                self.state["orders"] = [o for o in self.state["orders"] if o.get("status") != "cancelled"]
                self.state["orders"].extend(placed)

                logging.info(f"One-sided roll (down): cancelled {cancelled_count} buys, placed {len(placed)} new buys")
            else:
                logging.error(f"Invalid direction for one-sided roll: {direction}")
                return False

            # Update roll metadata
            self.state["roll_count"] = self.state.get("roll_count", 0) + 1
            self.state["last_roll"] = datetime.now(timezone.utc).isoformat()
            self._save_state()

            return True

        except Exception as e:
            logging.error(f"One-sided roll failed: {e}")
            return False

    # ── Rolling Grid ─────────────────────────────────────────────

    @staticmethod
    def generate_levels_from_volume_profile(vp: dict, bands: dict, atr: dict, skew: dict, current_price: float) -> dict:
        """Generate grid levels from Volume Profile with Bollinger Bands fallback.

        Uses volume profile nodes (HVN) to place buy levels below price
        and sell levels above price. POC (Point of Control) influences density.

        Falls back to Bollinger Bands logic if volume profile returns empty nodes.

        Returns same format as analyst output:
        {"buy_levels": [...], "sell_levels": [...], "range_low": X, "range_high": Y}
        """
        hvn = vp.get("hvn", [])
        lvn = vp.get("lvn", [])
        poc = vp.get("poc", 0)

        # Fallback: if volume profile has no useful data, use BB bands
        if not hvn or poc == 0:
            return GridManager._generate_levels_from_bands(bands, atr, skew, current_price)

        spacing = atr["suggested_spacing"]
        if spacing <= 0:
            spacing = current_price * 0.002

        buy_pct = skew.get("buy_pct", 50) / 100.0
        sell_pct = skew.get("sell_pct", 50) / 100.0

        # Buy levels: HVNs below current price, sorted ascending
        buy_levels_raw = sorted([p for p in hvn if p < current_price])
        # Sell levels: HVNs above current price, sorted ascending
        sell_levels_raw = sorted([p for p in hvn if p > current_price])

        # Round to nearest $50
        buy_levels = sorted(list(set(round(p / 50) * 50 for p in buy_levels_raw)))
        sell_levels = sorted(list(set(round(p / 50) * 50 for p in sell_levels_raw)))

        # Filter to ensure buy < current_price and sell > current_price after rounding
        buy_levels = [p for p in buy_levels if p < current_price]
        sell_levels = [p for p in sell_levels if p > current_price]

        # Ensure minimum 2 levels on each side
        if len(buy_levels) < 2 or len(sell_levels) < 2:
            return GridManager._generate_levels_from_bands(bands, atr, skew, current_price)

        # Clip to a reasonable number based on skew (4-8 per side)
        max_buys = max(4, int(8 * buy_pct))
        max_sells = max(4, int(8 * sell_pct))
        buy_levels = buy_levels[-max_buys:]  # closest to price
        sell_levels = sell_levels[:max_sells]  # closest to price

        # If POC is close to current price, add extra levels near POC for density
        if len(buy_levels) < max_buys and poc < current_price:
            poc_rounded = round(poc / 50) * 50
            if poc_rounded not in buy_levels:
                buy_levels.append(poc_rounded)
                buy_levels.sort()
                buy_levels = buy_levels[-max_buys:]

        if len(sell_levels) < max_sells and poc > current_price:
            poc_rounded = round(poc / 50) * 50
            if poc_rounded not in sell_levels:
                sell_levels.append(poc_rounded)
                sell_levels.sort()
                sell_levels = sell_levels[:max_sells]

        range_low = min(buy_levels) if buy_levels else current_price - spacing * 2
        range_high = max(sell_levels) if sell_levels else current_price + spacing * 2

        return {
            "buy_levels": buy_levels,
            "sell_levels": sell_levels,
            "range_low": range_low,
            "range_high": range_high,
        }

    @staticmethod
    def _generate_levels_from_bands(bands: dict, atr: dict, skew: dict, current_price: float) -> dict:
        """Generate grid levels from Bollinger Bands + ATR + Trend Skew (fallback).

        Levels are anchored to band edges (lower for buys, upper for sells),
        not current_price - ensuring deterministic output for the same bands.

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

        total_range = upper - lower
        total_levels = max(4, min(12, int(total_range / spacing)))  # 4-12 levels max

        num_buys = max(2, int(total_levels * buy_pct))
        num_sells = max(2, int(total_levels * sell_pct))

        # Generate buy levels starting from LOWER band going upward toward price
        buy_levels = []
        price = lower + spacing
        while len(buy_levels) < num_buys and price < current_price:
            rounded = round(price / 50) * 50  # round to $50
            # Avoid exact duplicates
            if rounded not in buy_levels:
                buy_levels.append(rounded)
            price += spacing
        buy_levels.sort()

        # Generate sell levels starting from UPPER band going downward toward price
        sell_levels = []
        price = upper - spacing
        while len(sell_levels) < num_sells and price > current_price:
            rounded = round(price / 50) * 50  # round to $50
            # Avoid exact duplicates
            if rounded not in sell_levels:
                sell_levels.append(rounded)
            price -= spacing
        sell_levels.sort()

        # Fallback: if we couldn't fit levels, place some at band edges
        if len(buy_levels) < 2 and current_price > lower:
            for i in range(1, 3):
                p = round((lower + spacing * i) / 50) * 50
                if p < current_price and p not in buy_levels:
                    buy_levels.append(p)
            buy_levels.sort()
        if len(sell_levels) < 2 and current_price < upper:
            for i in range(1, 3):
                p = round((upper - spacing * i) / 50) * 50
                if p > current_price and p not in sell_levels:
                    sell_levels.append(p)
            sell_levels.sort()

        return {
            "buy_levels": buy_levels,
            "sell_levels": sell_levels,
            "range_low": lower,
            "range_high": upper,
        }

    async def roll_grid(self, current_price: float, direction: str) -> bool:
        """Roll the grid to follow price when it hits band edges.

        Strategy: try one-sided roll first (keep one side's orders intact),
        fall back to full roll if one-sided would leave too few levels.

        Returns True if a roll was performed, False if skipped.
        """
        logging.info(f"Rolling grid at ${current_price:,.0f} (direction: {direction})")

        # Fetch candidate levels first (before canceling anything)
        new_levels, time_adj, funding_adj, oi_div = await self._compute_candidate_levels(current_price)
        if new_levels is None:
            logging.error("Failed to compute candidate levels for roll - skipping")
            return False

        new_buy = new_levels.get("buy_levels", [])
        new_sell = new_levels.get("sell_levels", [])
        if not new_buy or not new_sell:
            logging.warning("Roll generated empty levels - skipping")
            return False

        # Apply OI divergence hints to adjust grid sizing
        oi_state = oi_div.get("state", "neutral")
        oi_implication = oi_div.get("grid_implication", "none")

        min_levels = self.cfg["grid"].get("min_levels", 2)
        max_levels = self.cfg["grid"].get("max_levels", 8)

        # ── Try one-sided roll first ─────────────────────────────────
        # Determine the affected side and the preserved side
        if direction == "up":
            # Price hit top - new sells needed
            preserved_side_count = self._count_side_levels("buy")   # buy side stays
            replaced_side_count = len(new_sell)                       # new sell count
            if oi_state == "new_shorts":
                # Bearish trend - reduce sell count
                new_sell = new_sell[:max(max_levels - 1, min_levels)]
                replaced_side_count = len(new_sell)
                logging.info(f"OI divergence: new_shorts - reduced sell count to {replaced_side_count}")
            elif oi_state == "long_squeeze":
                # Bullish continuation - widen sell spacing
                new_sell = new_sell[::2] if len(new_sell) > 4 else new_sell
                replaced_side_count = len(new_sell)
                logging.info(f"OI divergence: long_squeeze - widened sell levels to {replaced_side_count}")
        elif direction == "down":
            # Price hit bottom - new buys needed
            preserved_side_count = self._count_side_levels("sell")   # sell side stays
            replaced_side_count = len(new_buy)                        # new buy count
            if oi_state == "capitulation":
                # Longs liquidating - be more aggressive with buy levels
                new_buy = new_buy[-min(max_levels, len(new_buy) + 1):]
                replaced_side_count = len(new_buy)
                logging.info(f"OI divergence: capitulation - aggressive buy count {replaced_side_count}")
            elif oi_state == "new_shorts":
                # Bearish trend - reduce buy count by 1
                new_buy = new_buy[-max(min_levels, max_levels - 1):]
                replaced_side_count = len(new_buy)
                logging.info(f"OI divergence: new_shorts - reduced buy count to {replaced_side_count}")
        else:
            logging.error(f"Invalid direction: {direction}")
            return False

        # Check if one-sided roll would leave enough levels on both sides
        can_one_sided = (
            preserved_side_count >= min_levels
            and replaced_side_count >= min_levels
        )

        if can_one_sided:
            # Check per-side overlap for the side being replaced
            # If the side being replaced has >80% overlap with new levels, skip
            old_buy = self.state["levels"].get("buy", [])
            old_sell = self.state["levels"].get("sell", [])

            if direction == "up":
                # Replacing sell side
                if old_sell:
                    matched = 0
                    for np in new_sell:
                        if any(abs(np - op) / max(np, op) < 0.0015 for op in old_sell):
                            matched += 1
                    overlap_sell = matched / len(new_sell) if new_sell else 0
                    if overlap_sell > 0.80:
                        logging.info(f"Sell side overlap {overlap_sell:.0%} - one-sided roll would be redundant")
                    else:
                        # Per-side overlap is acceptable, try one-sided roll
                        rolled = await self.roll_one_sided(current_price, direction, new_levels)
                        if rolled:
                            await send_alert(
                                (
                                    f"🔄 Grid rolled {direction} (one-sided) · BTC @ ${current_price:,.0f}\n"
                                    f"New range: ${self.state['range_low']:,.0f} - ${self.state['range_high']:,.0f}\n"
                                    f"Roll #{self.state['roll_count']}"
                                )
                            )
                            return True
                else:
                    # No old sell levels, proceed with one-sided
                    rolled = await self.roll_one_sided(current_price, direction, new_levels)
                    if rolled:
                        await send_alert(
                            (
                                f"🔄 Grid rolled {direction} (one-sided) · BTC @ ${current_price:,.0f}\n"
                                f"New range: ${self.state['range_low']:,.0f} - ${self.state['range_high']:,.0f}\n"
                                f"Roll #{self.state['roll_count']}"
                            )
                        )
                        return True
            elif direction == "down":
                # Replacing buy side
                if old_buy:
                    matched = 0
                    for np in new_buy:
                        if any(abs(np - op) / max(np, op) < 0.0015 for op in old_buy):
                            matched += 1
                    overlap_buy = matched / len(new_buy) if new_buy else 0
                    if overlap_buy > 0.80:
                        logging.info(f"Buy side overlap {overlap_buy:.0%} - one-sided roll would be redundant")
                    else:
                        # Per-side overlap is acceptable, try one-sided roll
                        rolled = await self.roll_one_sided(current_price, direction, new_levels)
                        if rolled:
                            await send_alert(
                                (
                                    f"🔄 Grid rolled {direction} (one-sided) · BTC @ ${current_price:,.0f}\n"
                                    f"New range: ${self.state['range_low']:,.0f} - ${self.state['range_high']:,.0f}\n"
                                    f"Roll #{self.state['roll_count']}"
                                )
                            )
                            return True
                else:
                    # No old buy levels, proceed with one-sided
                    rolled = await self.roll_one_sided(current_price, direction, new_levels)
                    if rolled:
                        await send_alert(
                            (
                                f"🔄 Grid rolled {direction} (one-sided) · BTC @ ${current_price:,.0f}\n"
                                f"New range: ${self.state['range_low']:,.0f} - ${self.state['range_high']:,.0f}\n"
                                f"Roll #{self.state['roll_count']}"
                            )
                        )
                        return True

            logging.info("One-sided roll skipped or failed - falling back to full roll")
        else:
            logging.info(
                f"One-sided roll not viable (preserved={preserved_side_count}, new={replaced_side_count}, min={min_levels}) - falling back to full roll"
            )

        # ── Fallback: full roll (existing logic) ─────────────────────
        logging.info("Attempting full grid roll (fallback)")

        # Backup current state
        backup_state = copy.deepcopy(self.state)

        # Full overlap check (both sides combined)
        old_buy = self.state["levels"].get("buy", [])
        old_sell = self.state["levels"].get("sell", [])
        overlap = self._levels_overlap(old_buy, old_sell, new_buy, new_sell)
        if overlap > 0.80:
            logging.info(f"Full grid overlap {overlap:.0%} - skipping roll, levels too similar")
            return False

        # Cancel existing orders
        cancelled = await self.api.cancel_all_orders()
        logging.info(f"Cancelled {cancelled} old orders for full roll")

        # Get equity for sizing
        try:
            equity = await self.api.get_equity()
        except Exception as e:
            logging.error(f"Failed to get equity for roll: {e}")
            await send_alert(f"⚠️ Grid roll failed to fetch equity: {e}. Reverting.")
            await self._redeploy_backup(backup_state)
            return False

        # Deploy new grid using pre-computed levels
        roll_info = {
            "roll_count": self.state.get("roll_count", 0) + 1,
            "last_roll": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self.deploy(new_levels, equity, current_price, roll_info=roll_info, time_adj=time_adj, funding_adj=funding_adj)
        except Exception as e:
            logging.error(f"Failed to deploy new grid: {e}")
            await send_alert(f"⚠️ Grid roll failed to deploy: {e}. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return False

        await send_alert(
            (
                f"🔄 Grid rolled {direction} (full)· BTC @ ${current_price:,.0f}\n"
                f"New range: ${min(new_buy):,.0f} - ${max(new_sell):,.0f}\n"
                f"Overlap was {overlap:.0%} - levels changed meaningfully\n"
                f"Roll #{self.state['roll_count']}"
            )
        )

        return True



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

    # ── Status ───────────────────────────────────────────────────

    def status_summary(self) -> str:
        """Return a human-readable status string."""
        s = self.state
        if not s["active"] and not s["paused"]:
            return "Grid: not deployed"
        open_orders = [o for o in s["orders"] if o["status"] == "open"]
        status = "PAUSED" if s["paused"] else "ACTIVE"
        grid_direction = s.get("grid_direction", "long").upper()
        rolls = s.get("roll_count", 0)
        realized_pnl = s.get("realized_pnl", 0.0)
        
        # Direction-specific pending count
        if grid_direction == "SHORT":
            pending = len(s.get("pending_sells", []))
        else:
            pending = len(s.get("pending_buys", []))
        
        trades = len(s.get("trades", []))
        return (
            f"Grid: {status} ({grid_direction})\n"
            f"Range: ${s['range_low']:,.0f}-${s['range_high']:,.0f}\n"
            f"Open orders: {len(open_orders)}\n"
            f"Fills today: {s.get('fill_count', 0)}\n"
            f"Realized PnL: ${realized_pnl:.2f}\n"
            f"Completed trades: {trades} · Pending: {pending}\n"
            f"Rolls: {rolls}\n"
            f"Last reset: {s['last_reset']}"
        )
