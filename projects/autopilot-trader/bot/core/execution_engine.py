"""
Main execution engine for bot ticks and position processing.

Contains the core event loop (_tick) and position-level processing
(_process_position_tick) with DSL evaluation and order execution.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import BotConfig
from core.models import TrackedPosition
from api.lighter_api import LighterAPI
from core.position_tracker import PositionTracker
from dsl import evaluate_dsl


class ExecutionEngine:
    def __init__(self, cfg: BotConfig, api: LighterAPI, tracker: PositionTracker, alerter, bot):
        """Initialize execution engine.

        Args:
            cfg: Bot configuration
            api: LighterAPI instance
            tracker: PositionTracker instance
            alerter: TelegramAlerter instance
            bot: LighterCopilot reference (for all state and other managers)
        """
        self.cfg = cfg
        self.api = api
        self.tracker = tracker
        self.alerter = alerter
        self.bot = bot

    @staticmethod
    def _pnl_info(pos: TrackedPosition, price: float) -> dict:
        """Calculate PnL USD, ROE%, and leverage for a position at a given price.

        pos.size is in base units (e.g., BTC), notional_usd = size * entry_price.
        Returns dict with: pnl_usd, roe_pct, leverage, notional_usd.
        """
        if pos.entry_price <= 0 or pos.size <= 0:
            return {"pnl_usd": 0.0, "roe_pct": 0.0, "leverage": 10.0, "notional_usd": 0.0}
        notional_usd = pos.size * pos.entry_price
        if pos.side == "long":
            pnl_usd = pos.size * (price - pos.entry_price)
        else:
            pnl_usd = pos.size * (pos.entry_price - price)
        pnl_pct = (pnl_usd / notional_usd * 100) if notional_usd > 0 else 0.0
        leverage = pos.dsl_state.leverage if pos.dsl_state else 10.0
        roe_pct = pnl_pct * leverage
        return {"pnl_usd": pnl_usd, "roe_pct": roe_pct, "leverage": leverage, "notional_usd": notional_usd}

    @staticmethod
    def _fmt_mt(dt: datetime | None) -> str:
        """Format a UTC datetime as Mountain Time (MDT/MST)."""
        if dt is None:
            return "?"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mt = datetime.fromtimestamp(dt.timestamp())  # local = Mountain on this server
        return mt.strftime("%H:%M")

    async def _tick(self):
        """One cycle: sync positions, update prices, check triggers."""
        # HIGH-13: _pending_sync.clear() moved to AFTER position verification section
        # (see section 1.4 below) to prevent race where previous tick's verification
        # is still sleeping when sync re-detects the position as "new".

        # ── Kill switch check ──
        kill_switch_now = self.bot._kill_switch_path.exists()
        if kill_switch_now and not self.bot._kill_switch_active:
            self.bot._kill_switch_active = True
            logging.critical("🚨 KILL SWITCH ACTIVE — no new positions")
            await self.alerter.send("🚨 *KILL SWITCH ACTIVE*\nNo new positions will be opened.\nExisting positions still managed.")
        elif not kill_switch_now and self.bot._kill_switch_active:
            self.bot._kill_switch_active = False
            logging.info("✅ Kill switch deactivated")
            await self.alerter.send("✅ *Kill switch deactivated*\nBot resumed normal operation.")

        self.bot.order_manager._prune_caches()
        if not self.api:
            return

        # 1. Sync positions from Lighter
        live_positions = await self.api.get_positions()

        # EDGE-03: Handle API/network failure — don't clear positions on error
        if live_positions is None:
            self.bot._position_sync_failures += 1
            if self.bot._position_sync_failures == 1:
                logging.warning(f"⚠️ Position sync failed (attempt {self.bot._position_sync_failures}) — keeping existing tracker state")
            else:
                logging.error(f"❌ Position sync failed ({self.bot._position_sync_failures} consecutive) — keeping existing tracker state")
            if self.bot._position_sync_failures >= self.bot._position_sync_failure_threshold:
                await self.alerter.send(
                    f"🔴 *Position Sync Failed ×{self.bot._position_sync_failures}*\n"
                    f"Cannot reach Lighter API for position sync.\n"
                    f"Tracker state preserved (positions may be stale).\n"
                    f"Check network/proxy status."
                )
            # Skip position sync entirely — DSL evaluation still runs on existing positions
            live_mids = set()
        else:
            # Reset failure counter on successful sync
            if self.bot._position_sync_failures > 0:
                logging.info(f"✅ Position sync recovered after {self.bot._position_sync_failures} failure(s)")
                self.bot._position_sync_failures = 0

            live_mids = {p["market_id"] for p in live_positions}

            # Cache mark prices from unrealized_pnl (authoritative exchange price for PnL)
            self.api.update_mark_prices_from_positions(live_positions)

            # Refresh account equity for cross margin ROE calculations
            try:
                new_balance = await self.bot._get_balance()
                if new_balance > 0:
                    self.tracker.account_equity = new_balance
                    # Leverage is set on exchange via set_leverage() — no recalculation needed
            except Exception as e:
                logging.debug(f"Equity refresh failed (using cached): {e}")

        # Detect new positions & closed positions — only when API succeeded
        # When live_positions is None, skip entirely to preserve tracker state (EDGE-03)
        if live_positions is not None:
            # Detect new positions (skip markets opened this tick to avoid race)
            for pos in live_positions:
                mid = pos["market_id"]
                if mid in self.bot._pending_sync:
                    continue
                # CRITICAL-2: Adopt unverified positions that now appear in live_positions
                existing = self.tracker.positions.get(mid)
                if existing and existing.unverified_at is not None:
                    logging.info(f"✅ {pos['symbol']}: unverified position confirmed on exchange — adopting")
                    # Update with actual exchange data
                    existing.unverified_at = None
                    existing.unverified_ticks = 0
                    existing.entry_price = pos["entry_price"]
                    existing.size = pos["size"]
                    if existing.dsl_state:
                        if pos.get("leverage"):
                            existing.dsl_state.leverage = pos["leverage"]
                    continue
                if mid in self.tracker.positions:
                    continue  # already tracked
                if pos["entry_price"] <= 0:
                    continue

                symbol = pos["symbol"]

                # BUG-07: Skip positions the bot didn't open
                if not self.cfg.track_manual_positions and mid not in self.bot.bot_managed_market_ids:
                    logging.info(f"↩️ Unmanaged position detected, skipping: {pos['side'].upper()} {symbol} (market_id={mid})")
                    continue

                # Skip if bot recently closed this position (stale API data)
                if mid in self.bot._recently_closed:
                    logging.debug(f"⏭️ {symbol}: recently closed by bot, ignoring stale API data")
                    continue

                # Check AI close cooldown before re-tracking
                cooldown_until = self.bot._ai_close_cooldown.get(symbol)
                if cooldown_until and time.monotonic() < cooldown_until:
                    remaining = int(cooldown_until - time.monotonic())
                    # Rate limit API lag warnings (once per minute per symbol)
                    now_mono = time.monotonic()
                    last_warned = self.bot._api_lag_warnings.get(symbol, 0)
                    if now_mono - last_warned > 60:
                        self.bot._api_lag_warnings[symbol] = now_mono
                        logging.warning(f"🧊 DETECTED {symbol} from Lighter API but AI close cooldown active ({remaining}s) - API lag? IGNORING")
                    continue

                # Confirm on first detection — phantom guard via _recently_closed + _ai_close_cooldown already handles false positives
                logging.info(f"📌 API POSITION DETECTED: {pos['side'].upper()} {symbol}")
                self.tracker.add_position(
                    mid, pos["symbol"], pos["side"], pos["entry_price"], pos["size"],
                    leverage=pos.get("leverage")
                )
                logging.info(f"📊 Quota remaining: {self.api.volume_quota_remaining}")
                await self.alerter.send(
                    f"📌 *New position detected*\n"
                    f"{pos['side'].upper()} {pos['symbol']} @ ${pos['entry_price']:,.2f}\n"
                    f"Size: {pos['size']}"
                )

            # Detect closed positions (skip unverified — handled separately below)
            for mid in list(self.tracker.positions.keys()):
                if mid not in live_mids:
                    pos = self.tracker.positions[mid]
                    # CRITICAL-2: Don't remove unverified positions on first absence —
                    # they may just not be visible to the API yet. Give them 3 ticks.
                    if pos.unverified_at is not None:
                        continue
                    # Try to get fill price for accurate outcome logging
                    exit_price = self.api.get_mark_price(mid) if self.api else pos.entry_price
                    if not exit_price or exit_price <= 0:
                        exit_price = pos.entry_price
                    self.bot.signal_processor._log_outcome(pos, exit_price, "exchange_close")
                    logging.info(f"Position closed by exchange: {pos.symbol}")
                    self.bot._recently_closed[mid] = time.monotonic() + 300
                    pos.active_sl_order_id = None  # MED-18
                    self.bot.bot_managed_market_ids.discard(mid)
                    self.tracker.remove_position(mid)
                    self.bot._opened_signals.discard(mid)

            # CRITICAL-2: Handle unverified positions not in live_mids
            # Increment tick count; alert and remove after 3 consecutive absent ticks
            for mid in list(self.tracker.positions.keys()):
                pos = self.tracker.positions[mid]
                if pos.unverified_at is None:
                    continue
                if mid in live_mids:
                    # Position appeared — will be adopted in detection loop above on next tick
                    continue
                pos.unverified_ticks += 1
                logging.warning(f"⚠️ {pos.symbol}: unverified position not in live positions (tick {pos.unverified_ticks}/3)")
                if pos.unverified_ticks >= 3:
                    logging.error(f"❌ {pos.symbol}: unverified position absent for 3 ticks — removing (order likely rejected)")
                    await self.alerter.send(
                        f"❌ *UNVERIFIED POSITION REMOVED*\n"
                        f"{pos.side.upper()} {pos.symbol}\n"
                        f"Absent from exchange for 3 consecutive ticks.\n"
                        f"Order was likely rejected. Position removed from tracker."
                    )
                    pos.active_sl_order_id = None  # MED-18
                    self.bot.bot_managed_market_ids.discard(mid)
                    self.tracker.remove_position(mid)
                    self.bot._opened_signals.discard(mid)

        # Fix #17: Quota staleness warning — alert if no quota update for 10+ minutes
        if self.api and self.api._last_known_quota is not None and self.api._last_quota_time > 0:
            quota_age = time.time() - self.api._last_quota_time
            if quota_age > 600:  # 10 minutes
                age_min = int(quota_age / 60)
                logging.warning(
                    f"⚠️ Quota tracking stale — no update for {age_min}m "
                    f"(last known: {self.api._last_known_quota} TX). "
                    f"Guards still active with last known value."
                )
                # Reset timer to avoid spamming every tick (warn once per 10 min)
                self.api._last_quota_time = time.time()

        # Periodic quota status alert (every 20 minutes) — after position sync for accurate counts
        now = time.time()
        if now - self.bot._last_quota_alert_time > self.bot._quota_alert_interval:
            self.bot._last_quota_alert_time = now
            api_quota = self.api.volume_quota_remaining if self.api else None
            in_cooldown = False
            if api_quota is not None:
                status = f"{api_quota} TX"
            elif self.api and self.api._last_known_quota is not None:
                age = int((now - self.api._last_quota_time) / 60)
                status = f"~{self.api._last_known_quota} TX (updated {age}m ago)"
            elif in_cooldown:
                status = "0 TX (exhausted)"
            else:
                status = "unknown"
            positions_count = len(self.tracker.positions)
            emoji = "🔴" if (api_quota is not None and api_quota < 35) or (api_quota is None and in_cooldown) else "🟡" if (api_quota is not None and api_quota < 200) else "🟢"
            await self.alerter.send(
                f"{emoji} *Quota Status*\n"
                f"Remaining: {status}\n"
                f"Positions: {positions_count}\n"
                f"Cooldown: {'active' if in_cooldown else 'none'}"
            )

        # 1.4. HIGH-13: Clear pending sync AFTER position verification section completes.
        # This prevents the race where previous tick's verification is still sleeping
        # and sync re-detects the position as "new", giving it a fresh DSLState instead
        # of the AI-configured one.
        self.bot._pending_sync.clear()

        # Reconcile saved DSL state with detected positions (first tick after restart)
        await self.bot.state_manager._reconcile_positions(live_positions)

        # MED-4: Refresh position context in result file so AI trader sees current positions
        # even between its own decisions (DSL/SL/TP closes update tracker but not result file)
        # HIGH-10: Clear dirty flag — AI trader has had a full tick to read the result
        if self.bot._ai_mode:
            self.bot._result_dirty = False
        if self.bot._ai_mode and self.tracker.positions:
            self.bot.signal_processor._refresh_position_context()

        # 1.5. Process signals — AI mode or rule-based
        # NOTE: Moved AFTER position sync/confirmation so tracker is populated
        # before close_all or other AI decisions execute.
        if self.bot._ai_mode:
            await self.bot.signal_processor._process_ai_decision()
        else:
            await self.bot.signal_processor._process_signals()

        # 2. Update tracked markets
        self.api.set_tracked_markets(list(self.tracker.positions.keys()))
        self.api._save_tracked_markets()

        # 3. Get prices and check triggers — each position wrapped independently
        tracked_items = list(self.tracker.positions.items())
        for i, (mid, pos) in enumerate(tracked_items):
            try:
                if i < len(tracked_items) - 1:
                    await asyncio.sleep(self.cfg.price_call_delay)
                await self._process_position_tick(mid, pos)
            except Exception as e:
                logging.error(f"Error processing {pos.symbol} (market {mid}): {e}", exc_info=True)
                continue  # one bad position doesn't kill the rest

        # Persist state for crash/restart recovery
        self.bot.state_manager._save_state()

        # ── Idle tick tracking — extend sleep when flat with no activity ──
        if len(self.tracker.positions) == 0 and not self.bot._signal_processed_this_tick:
            self.bot._idle_tick_count += 1
        else:
            # Reset on any activity (positions or signals)
            self.bot._idle_tick_count = 0
        self.bot._signal_processed_this_tick = False  # reset per-tick flag



    async def _process_position_tick(self, mid: int, pos: TrackedPosition):
        """Process a single position's tick — fetch price, evaluate triggers, execute if needed."""
        # CRITICAL-2: Skip DSL/SL evaluation for unverified positions
        if pos.unverified_at is not None:
            logging.debug(f"⏭️ {pos.symbol}: skipping tick (unverified, tick {pos.unverified_ticks})")
            return
        # MED-25: Skip DSL/SL evaluation for positions being verified as closed
        if mid in self.bot._verifying_close:
            logging.debug(f"⏭️ {pos.symbol}: skipping tick (verification in progress)")
            return
        price = await self.api.get_price_with_mark_fallback(mid)
        if not price:
            # BUG-06: Orphaned position detection — don't skip silently
            consecutive = self.bot._no_price_ticks.get(mid, 0) + 1
            self.bot._no_price_ticks[mid] = consecutive
            logging.warning(
                f"⚠️ {pos.symbol}: no price data (mark + trade both failed) — "
                f"consecutive no-price ticks: {consecutive}/{self.bot._no_price_alert_threshold}"
            )
            if consecutive >= self.bot._no_price_alert_threshold:
                await self.alerter.send(
                    f"🚨 *ORPHANED POSITION*\n"
                    f"{pos.symbol} ({pos.side.upper()})\n"
                    f"No price data for {consecutive} consecutive ticks.\n"
                    f"Entry: ${pos.entry_price:,.2f}\n"
                    f"DSL/SL evaluation suspended — MANUAL CHECK REQUIRED."
                )
                # Reset counter to avoid spamming every tick (alert once per threshold)
                self.bot._no_price_ticks[mid] = 0
            return
        # Reset no-price counter on successful price fetch
        if mid in self.bot._no_price_ticks:
            del self.bot._no_price_ticks[mid]

        # NOTE: DSL uses leverage from exchange IMF (set via set_leverage() before opens).
        # Both current_roe() and hard_sl_roe in dsl.py use state.leverage.

        action = self.tracker.update_price(mid, price)
        is_long = pos.side == "long"

        if not action:
            # Periodic stagnation status alert (every 15 minutes)
            if (pos.dsl_state and pos.dsl_state.stagnation_active
                    and pos.dsl_state.high_water_time):
                elapsed_min = (datetime.now(timezone.utc) - pos.dsl_state.high_water_time).total_seconds() / 60
                remaining_min = self.cfg.stagnation_minutes - elapsed_min
                if remaining_min > 0:
                    last_status = self.bot._stagnation_last_status.get(mid, 0)
                    if time.monotonic() - last_status >= 900:  # 15 minutes
                        self.bot._stagnation_last_status[mid] = time.monotonic()
                        pnl = self._pnl_info(pos, price)
                        hw = pos.dsl_state.high_water_roe
                        logging.info(
                            f"⏳ {pos.symbol}: stagnation {elapsed_min:.0f}min / "
                            f"{self.cfg.stagnation_minutes}min ({remaining_min:.0f}min remaining), "
                            f"PnL=${pnl['pnl_usd']:+.2f}, ROE={pnl['roe_pct']:+.1f}%, HW={hw:+.1f}%"
                        )
                        await self.alerter.send(
                            f"⏳ *STAGNATION STATUS*\n"
                            f"Symbol: {pos.symbol}\n"
                            f"Side: {pos.side}\n"
                            f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                            f"HW Peak: {hw:+.1f}% ROE\n"
                            f"Timer: {elapsed_min:.0f}min / {self.cfg.stagnation_minutes}min "
                            f"({remaining_min:.0f}min remaining)"
                        )
            return

        # Unpack tuple actions (action_name, details_dict)
        if isinstance(action, tuple):
            action, details = action
        else:
            details = {}

        # Informational alerts (no trade execution)
        if action == "dsl_tier_lock":
            pnl = self._pnl_info(pos, price)
            msg = (
                f"🔒 *DSL TIER LOCKED*\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                f"Lock floor: {details['floor_roe']:+.1f}% ROE (~${details['floor_price']:,.2f})\n"
                f"Tier: +{details['tier']}% ({details['breaches']}x)"
            )
            logging.info(msg)
            await self.alerter.send(msg)
            return

        if action == "dsl_stagnation_timer":
            pnl = self._pnl_info(pos, price)
            since = details.get("since")
            since_str = self._fmt_mt(since)
            exit_at = since + timedelta(minutes=self.cfg.stagnation_minutes) if since else None
            exit_str = self._fmt_mt(exit_at)
            msg = (
                f"⏳ *DSL STAGNATION TIMER STARTED*\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                f"Timer: {self.cfg.stagnation_minutes}min (started {since_str} MT → exits {exit_str} MT if no new high)"
            )
            logging.info(msg)
            await self.alerter.send(msg)
            self.bot._stagnation_last_status[mid] = time.monotonic()
            return

        # Exit actions (DSL)
        if action in ("tier_lock", "stagnation", "hard_sl"):
            pnl = self._pnl_info(pos, price)
            labels = {
                "tier_lock": "🔒 DSL TIER LOCK BREACH",
                "stagnation": "⏸️ DSL STAGNATION EXIT",
                "hard_sl": "🛑 HARD STOP LOSS",
            }
            hw_pnl = self._pnl_info(pos, pos.dsl_state.high_water_price) if pos.dsl_state and pos.dsl_state.high_water_price else None
            hw_str = f"HW Peak: ${hw_pnl['pnl_usd']:+.2f} ({pos.dsl_state.high_water_roe:+.1f}% ROE)" if hw_pnl else ""
            msg = (
                f"{labels.get(action, action)}\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"Trigger: ${price:,.2f}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                f"{hw_str}"
            )
            logging.info(msg)
            await self.alerter.send(msg)
            # Check DSL close attempt cooldown
            cooldown_until = self.bot._dsl_close_attempt_cooldown.get(pos.symbol)
            if cooldown_until and time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                logging.info(f"🧊 DSL close: {pos.symbol} in DSL close cooldown ({remaining}s remaining) — skipping. Position may need manual intervention.")
                return  # Don't remove from tracker, but stop retrying

            # MED-18: Cancel stale SL order before placing new one
            if pos.active_sl_order_id:
                logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before DSL close")
                await self.api._cancel_order(mid, int(pos.active_sl_order_id))
                pos.active_sl_order_id = None
            sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
            if sl_success and sl_coi:
                pos.active_sl_order_id = sl_coi
            if sl_success:
                # CRITICAL-4: Don't log outcome yet — log ONCE after verification
                position_closed = await self.bot.signal_processor._verify_position_closed(mid, pos.symbol)
                if not position_closed:
                    # Increment DSL close attempt counter
                    attempts = self.bot._dsl_close_attempts.get(pos.symbol, 0) + 1
                    self.bot._dsl_close_attempts[pos.symbol] = attempts
                    logging.warning(f"⚠️ {pos.symbol}: DSL SL submitted but position still open (attempt {attempts}/{self.bot._max_close_attempts})")

                    if attempts >= self.bot._max_close_attempts:
                        # Escalate: set cooldown and alert
                        self.bot._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + self.bot._close_cooldown_seconds
                        # CRITICAL-4: Log with estimated price as fallback after all retries exhausted
                        self.bot.signal_processor._log_outcome(pos, price, f"dsl_{action}", estimated=True)
                        await self.alerter.send(
                            f"🚨 *DSL CLOSE FAILED ×{attempts}*\n"
                            f"{pos.side.upper()} {pos.symbol}\n"
                            f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                            f"Action: {labels.get(action, action)}\n"
                            f"Order submitted but NOT filled after {attempts} attempts.\n"
                            f"Cooldown: {self.bot._close_cooldown_seconds // 60}min — MANUAL INTERVENTION REQUIRED."
                        )
                        logging.error(f"🚨 {pos.symbol}: max DSL close attempts ({self.bot._max_close_attempts}) reached. Setting {self.bot._close_cooldown_seconds}s cooldown.")
                    return  # Don't remove from tracker, will retry next tick (unless cooldown active)
                # Position successfully closed — reset DSL attempt counter
                self.bot._dsl_close_attempts.pop(pos.symbol, None)
                self.bot._dsl_close_attempt_cooldown.pop(pos.symbol, None)
            else:
                # SL order failed (rate-limited or rejected) — track attempts with graduated delay
                attempts = self.bot._dsl_close_attempts.get(pos.symbol, 0) + 1
                self.bot._dsl_close_attempts[pos.symbol] = attempts
                delay_idx = min(attempts - 1, len(self.bot._sl_retry_delays) - 1)
                retry_delay = self.bot._sl_retry_delays[delay_idx]
                self.bot._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay

                logging.warning(f"⚠️ {pos.symbol}: DSL SL order rejected (attempt {attempts}, retry in {retry_delay}s)")

                if attempts >= 4:
                    # After 3 graduated retries, alert for manual intervention
                    await self.alerter.send(
                        f"🚨 *DSL SL FAILED ×{attempts}*\n"
                        f"{pos.side.upper()} {pos.symbol}\n"
                        f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                        f"Action: {labels.get(action, action)}\n"
                        f"Retry delays exhausted. Next retry in 15min.\n"
                        f"MANUAL INTERVENTION may be needed."
                    )
                return  # Don't remove from tracker, will retry after cooldown
            fill_price = await self.bot.signal_processor._get_fill_price(mid, sl_coi)
            exit_price = fill_price if fill_price else price
            # CRITICAL-4: Log outcome ONCE with actual fill price after verification
            self.bot.signal_processor._log_outcome(pos, exit_price, f"dsl_{action}")
            self.bot._recently_closed[mid] = time.monotonic() + 300  # 5 min phantom guard
            pos.active_sl_order_id = None  # MED-18
            self.bot.bot_managed_market_ids.discard(mid)
            self.tracker.remove_position(mid)
            self.bot._opened_signals.discard(mid)
            # Post-close completion alert
            if is_long:
                pnl_usd = pos.size * (exit_price - pos.entry_price)
            else:
                pnl_usd = pos.size * (pos.entry_price - exit_price)
            notional = pos.size * pos.entry_price
            pnl_pct_val = (pnl_usd / notional * 100) if notional > 0 else 0.0
            leverage = pos.dsl_state.leverage if pos.dsl_state else 10.0
            roe_pct = pnl_pct_val * leverage
            await self.alerter.send(
                f"✅ *DSL → CLOSED*\n"
                f"{pos.side.upper()} {pos.symbol}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"Exit: ${exit_price:,.2f}\n"
                f"PnL: ${pnl_usd:+.2f} ({roe_pct:+.1f}% ROE @ {leverage:.0f}x)\n"
                f"Reason: {labels.get(action, action)}"
            )
            return

        # Trailing SL exit (new — downside protection)
        if action == "trailing_sl":
            pnl = self._pnl_info(pos, price)
            msg = (
                f"🔻 *TRAILING SL EXIT*\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"Trigger: ${price:,.2f}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)"
            )
            logging.info(msg)
            await self.alerter.send(msg)
            # Check close attempt cooldown
            cooldown_until = self.bot._dsl_close_attempt_cooldown.get(pos.symbol)
            if cooldown_until and time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                logging.info(f"🧊 Trailing SL close: {pos.symbol} in cooldown ({remaining}s) — skipping")
                return
            # Cancel stale SL order
            if pos.active_sl_order_id:
                await self.api._cancel_order(mid, int(pos.active_sl_order_id))
                pos.active_sl_order_id = None
            sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
            if sl_success and sl_coi:
                pos.active_sl_order_id = sl_coi
            if sl_success:
                position_closed = await self.bot.signal_processor._verify_position_closed(mid, pos.symbol)
                if not position_closed:
                    attempts = self.bot._dsl_close_attempts.get(pos.symbol, 0) + 1
                    self.bot._dsl_close_attempts[pos.symbol] = attempts
                    if attempts >= self.bot._max_close_attempts:
                        self.bot._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + self.bot._close_cooldown_seconds
                        self.bot.signal_processor._log_outcome(pos, price, "trailing_sl", estimated=True)
                        await self.alerter.send(
                            f"🚨 *TRAILING SL CLOSE FAILED ×{attempts}*\n"
                            f"{pos.side.upper()} {pos.symbol}\n"
                            f"MANUAL INTERVENTION REQUIRED."
                        )
                    return
                self.bot._dsl_close_attempts.pop(pos.symbol, None)
                self.bot._dsl_close_attempt_cooldown.pop(pos.symbol, None)
            else:
                attempts = self.bot._dsl_close_attempts.get(pos.symbol, 0) + 1
                self.bot._dsl_close_attempts[pos.symbol] = attempts
                delay_idx = min(attempts - 1, len(self.bot._sl_retry_delays) - 1)
                retry_delay = self.bot._sl_retry_delays[delay_idx]
                self.bot._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay
                logging.warning(f"⚠️ {pos.symbol}: trailing SL order rejected (attempt {attempts}, retry in {retry_delay}s)")
                return
            fill_price = await self.bot.signal_processor._get_fill_price(mid, sl_coi)
            exit_price = fill_price if fill_price else price
            self.bot.signal_processor._log_outcome(pos, exit_price, "trailing_sl")
            self.bot._recently_closed[mid] = time.monotonic() + 300
            pos.active_sl_order_id = None
            self.bot.bot_managed_market_ids.discard(mid)
            self.tracker.remove_position(mid)
            self.bot._opened_signals.discard(mid)
            # Post-close alert
            if is_long:
                pnl_usd = pos.size * (exit_price - pos.entry_price)
            else:
                pnl_usd = pos.size * (pos.entry_price - exit_price)
            await self.alerter.send(
                f"✅ *TRAILING SL → CLOSED*\n"
                f"{pos.side.upper()} {pos.symbol}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"Exit: ${exit_price:,.2f}\n"
                f"PnL: ${pnl_usd:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)"
            )
            return

        # Legacy actions (stop_loss from non-DSL mode)
        sl_price = self.tracker._compute_hard_floor_price(pos)
        pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100)

        msg = (
            f"⚠️ *{action.upper().replace('_', ' ')}* triggered!\n"
            f"Symbol: {pos.symbol}\n"
            f"Side: {pos.side}\n"
            f"Trigger: ${price:,.2f}\n"
            f"Entry: ${pos.entry_price:,.2f}\n"
            f"P&L: {pnl_pct:+.2f}%"
        )

        logging.info(msg)
        await self.alerter.send(msg)

        # Execute the SL order
        if pos.active_sl_order_id:
            logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before legacy SL")
            await self.api._cancel_order(mid, int(pos.active_sl_order_id))
            pos.active_sl_order_id = None
        sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
        if sl_success and sl_coi:
            pos.active_sl_order_id = sl_coi
        if sl_success:
            position_closed = await self.bot.signal_processor._verify_position_closed(mid, pos.symbol)
            if not position_closed:
                logging.warning(f"⚠️ {pos.symbol}: SL submitted but position still open — keeping in tracker")
                return
            fill_price = await self.bot.signal_processor._get_fill_price(mid, sl_coi)
            price = fill_price if fill_price else price
            self.bot.signal_processor._log_outcome(pos, price, action)
        else:
            attempts = self.bot._close_attempts.get(pos.symbol, 0) + 1
            self.bot._close_attempts[pos.symbol] = attempts
            delay_idx = min(attempts - 1, len(self.bot._sl_retry_delays) - 1)
            retry_delay = self.bot._sl_retry_delays[delay_idx]
            self.bot._close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay
            logging.warning(f"⚠️ {pos.symbol}: SL order rejected (attempt {attempts}, retry in {retry_delay}s)")
            if attempts >= 4:
                pnl = self._pnl_info(pos, price)
                await self.alerter.send(
                    f"🚨 *SL FAILED ×{attempts}*\n"
                    f"{pos.side.upper()} {pos.symbol}\n"
                    f"PnL: ${pnl['pnl_usd']:+.2f} ({pnl['roe_pct']:+.1f}% ROE @ {pnl['leverage']:.0f}x)\n"
                    f"Action: {action.replace('_', ' ').upper()}\n"
                    f"Retry delays exhausted. Next retry in 15min.\n"
                    f"MANUAL INTERVENTION may be needed."
                )
            return
        self.bot._recently_closed[mid] = time.monotonic() + 300
        pos.active_sl_order_id = None
        self.bot.bot_managed_market_ids.discard(mid)
        self.tracker.remove_position(mid)
        self.bot._opened_signals.discard(mid)

    # ── Delegation methods for signal_processor ───────────────────
    def _save_state(self):
        """Delegate to StateManager (called by signal_processor via self.bot)."""
        self.bot.state_manager._save_state()



