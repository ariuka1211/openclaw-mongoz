"""
Signal processing and AI decision execution.

Handles signals.json, ai-decision.json processing, position opening/closing,
verification, and outcome logging.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import BotConfig
from core.models import TrackedPosition


class SignalProcessor:
    def __init__(self, cfg: BotConfig, api, tracker, alerter, bot):
        """Initialize signal processor.

        Args:
            cfg: Bot configuration
            api: LighterAPI instance
            tracker: PositionTracker instance
            alerter: TelegramAlerter instance
            bot: LighterCopilot reference (for state, _get_balance, _auth_manager, _save_state)
        """
        self.cfg = cfg
        self.api = api
        self.tracker = tracker
        self.alerter = alerter
        self.bot = bot

        # Paths for AI/signal files
        self.ai_decision_file = Path(cfg.ai_decision_file)
        self.ai_result_file = Path(cfg.ai_result_file)
        self.ai_trader_dir = Path(cfg.ai_trader_dir)
        self.signals_file = Path(cfg.signals_file)

    async def _process_signals(self):
        """Read signals.json and open positions for new, unopened signals."""
        if self._kill_switch_active:
            logging.warning("🚫 Kill switch active — _process_signals() skipping new opens")
            return

        signals_path = Path(self._signals_file)
        if not signals_path.exists():
            return

        data = safe_read_json(signals_path)
        if data is None:
            logging.warning(f"Failed to read signals file: {signals_path}")
            return

        # MED-6: Content-based dedup — hash opportunities (timestamp+symbol+score) so
        # same-second signals with different content are not silently dropped
        opp_hash = hashlib.sha256(json.dumps(
            data.get("opportunities", []),
            sort_keys=True, default=str
        ).encode()).hexdigest()[:16]
        if opp_hash == self.bot._last_signal_hash:
            return
        self.bot._last_signal_hash = opp_hash
        self.bot._last_signal_timestamp = data.get("timestamp")
        self._signal_processed_this_tick = True

        # Auto-detect balance and scale positions proportionally
        balance = await self.bot._get_balance()
        scanner_equity = data.get("config", {}).get("accountEquity", balance)
        if balance <= 0:
            logging.warning("⚠️ Zero or negative balance, cannot open positions")
            return
        scale = balance / scanner_equity
        if abs(scale - 1.0) > 0.01:
            logging.info(f"📐 Scaling positions: balance=${balance:.2f} / scanner_equity=${scanner_equity:.2f} = {scale:.4f}×")

        # HIGH-12: Write equity to shared state file for dashboard
        self._write_equity_file(balance)

        for opp in data.get("opportunities", []):
            mid = opp["marketId"]
            symbol = opp["symbol"]
            direction = opp.get("direction", "long")

            # Filter by minimum score
            score = opp.get("compositeScore", 0)
            if score < self._min_score:
                continue

            # Only open if safety checks passed
            if not opp.get("safetyPass", False):
                logging.debug(f"⚠️ {symbol}: safety check failed — {opp.get('safetyReason', 'unknown')}")
                continue

            # Cap concurrent positions from signals
            signal_positions = sum(1 for m in self.tracker.positions.keys() if m in self.bot._opened_signals)
            if signal_positions >= 3:
                logging.info(f"🛑 Max concurrent signal positions (3) reached, stopping")
                break

            # Skip if already have position in this market
            if mid in self.tracker.positions:
                logging.debug(f"⏭️ {symbol}: already have position, skipping")
                continue

            # Skip if AI recently closed this symbol (cooldown)
            cooldown_until = self._ai_close_cooldown.get(symbol)
            if cooldown_until and time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                logging.info(f"🧊 {symbol}: AI close cooldown ({remaining}s remaining) - SKIPPING")
                continue

            # Skip if already acted on this signal this round
            if mid in self.bot._opened_signals:
                continue

            is_long = direction == "long"

            # Check pacing BEFORE fetching price (saves API calls)
            if self._should_pace_orders():
                logging.debug(f"⏳ {symbol}: pacing orders (low quota), skipping open")
                continue

            # Quota prioritization: skip new opens when quota < 35 to preserve for SL orders
            if self._should_skip_open_for_quota():
                logging.warning(f"🚫 {symbol}: new opens paused (quota={self.api.volume_quota_remaining} < 35, SL protection prioritized)")
                continue

            # Always fetch live price — signal data can be stale (up to 5 min old)
            current_price = None
            if self.api:
                current_price = await self.api.get_price(mid)
                await asyncio.sleep(self.cfg.price_call_delay)
            if not current_price:
                logging.warning(f"⚠️ {symbol}: no live price available, skipping")
                continue

            # Scale position size to actual balance
            size_usd = opp.get("positionSizeUsd", 0) * scale
            if size_usd <= 0:
                logging.warning(f"⚠️ {symbol}: invalid position size, skipping")
                continue

            # Open the position
            logging.info(f"📡 Signal: {direction.upper()} {symbol} score={opp['compositeScore']} size=${size_usd:.2f}")
            if self.api:
                success = await self.api.open_position(mid, size_usd, is_long, current_price)
                if success:
                    self._mark_order_submitted()

                    # Fix #13: Add to pending_sync and bot_managed BEFORE verification
                    # to prevent race conditions if _tick() sync cycle runs mid-verification
                    self._pending_sync.add(mid)
                    self.bot.bot_managed_market_ids.add(mid)

                    # Verify position exists on exchange (BUG-03 fix)
                    expected_size = size_usd / current_price
                    verified_pos = await self._verify_position_opened(mid, expected_size, symbol)
                    if verified_pos is None:
                        # CRITICAL-2: Don't discard — add as unverified so we can re-verify on next ticks
                        logging.error(f"❌ Signal open: {symbol} verification failed — tracking as unverified (will re-verify)")
                        self.tracker.add_position(mid, symbol, direction, current_price, expected_size, leverage=min(self.cfg.default_leverage, 10))
                        pos = self.tracker.positions.get(mid)
                        if pos:
                            pos.unverified_at = time.time()
                            pos.unverified_ticks = 1
                        self.bot._save_state()
                        self.bot._opened_signals.add(mid)
                        await self.alerts.send(
                            f"⚠️ *POSITION UNVERIFIED*\n"
                            f"{direction.upper()} {symbol}\n"
                            f"Order submitted but verification failed.\n"
                            f"Will re-verify on next ticks."
                        )
                        continue  # Skip to next signal

                    self.bot._opened_signals.add(mid)
                    # Use actual filled size from exchange (handles partial fills)
                    actual_size = verified_pos["size"]
                    self.tracker.add_position(mid, symbol, direction, current_price, actual_size, leverage=min(self.cfg.default_leverage, 10))

                    # NOTE: DSL uses config leverage from add_position, NOT exchange-reported leverage.
                    # Exchange leverage can vary for cross margin and would break DSL tier calibration.

                    # Persist state immediately after opening to prevent crash data loss
                    self.bot._save_state()

                    # BUG-06: Verify we can actually fetch price for this position after open
                    price_ok = False
                    for attempt in range(1, 4):
                        verify_price = await self.api.get_price_with_mark_fallback(mid)
                        if verify_price:
                            price_ok = True
                            if attempt > 1:
                                logging.info(f"✅ {symbol}: price verified on retry {attempt}/3 = ${verify_price:,.2f}")
                            break
                        if attempt < 3:
                            await asyncio.sleep(1)
                    if not price_ok:
                        logging.error(f"❌ Signal open: {symbol} — no price after 3 attempts, removing orphaned position")
                        self.tracker.remove_position(mid)
                        await self.alerts.send(
                            f"❌ *SIGNAL OPEN FAILED*\n"
                            f"{direction.upper()} {symbol}\n"
                            f"Order filled but price unavailable — position removed.\n"
                            f"Order may need manual cleanup on exchange."
                        )
                        continue

                    logging.info(f"📊 Quota remaining: {self.api.volume_quota_remaining}")
                    await self.alerts.send(
                        f"📡 *SIGNAL → OPENED*\n"
                        f"{direction.upper()} {symbol}\n"
                        f"Score: {opp['compositeScore']}\n"
                        f"Price: ${current_price:,.2f}\n"
                        f"Size: ${size_usd:.2f} (scaled {scale:.2f}×)\n"
                        f"SL dist: {opp.get('stopLossDistancePct', 0):.2f}%"
                    )



    def _validate_ai_decision(self, decision: dict) -> str | None:
        """Validate AI decision fields. Returns error string if invalid, None if OK."""
        action = decision.get("action")
        if action not in ("open", "close", "close_all", "hold"):
            return f"Invalid action: {action!r}"

        symbol = decision.get("symbol")
        if action in ("open", "close"):
            if not symbol or not isinstance(symbol, str) or not symbol.strip():
                return f"Missing or invalid symbol: {symbol!r}"

        if action == "open":
            # IPC-03: use requested_size_usd (new) with fallback to size_usd (legacy)
            size_usd = decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)
            if not isinstance(size_usd, (int, float)) or size_usd <= 0:
                return f"Invalid size_usd: {size_usd!r}"
            direction = decision.get("direction")
            if direction not in ("long", "short"):
                return f"Invalid direction: {direction!r}"
            confidence = decision.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                    return f"Invalid confidence: {confidence!r} (expected 0.0-1.0)"

        if action == "close":
            confidence = decision.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                    return f"Invalid confidence: {confidence!r} (expected 0.0-1.0)"

        return None



    async def _process_ai_decision(self):
        """Read AI decision file and execute if valid."""
        path = Path(self._ai_decision_file)
        if not path.exists():
            return

        decision = safe_read_json(path)
        if decision is None:
            # MED-5: File exists but read returned None (atomic write in progress).
            # Retry once after 0.5s to avoid dropping decisions on first tick post-restart.
            if path.exists():
                await asyncio.sleep(0.5)
                decision = safe_read_json(path)
            if decision is None:
                return

        # Only process new decisions
        ts = decision.get("timestamp", "")
        if ts == self.bot._last_ai_decision_ts:
            return
        self.bot._last_ai_decision_ts = ts
        self._signal_processed_this_tick = True

        # HIGH-7: Reject stale AI decisions (>10 minutes old)
        try:
            decision_time = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        except (ValueError, AttributeError):
            decision_time = None
        if decision_time:
            age_seconds = (datetime.now(timezone.utc) - decision_time).total_seconds()
            if age_seconds > 600:
                logging.warning(f"⚠️ AI decision rejected: stale (age={age_seconds:.0f}s, max=600s)")
                self._write_ai_result(decision, success=False)
                return

        # Validate decision
        validation_error = self._validate_ai_decision(decision)
        if validation_error:
            logging.warning(f"⚠️ AI decision rejected: {validation_error}")
            self._write_ai_result(decision, success=False)
            return

        action = decision.get("action")
        if action not in ("open", "close", "close_all"):
            return  # hold or unknown — do nothing

        # BUG 2: Check prev_decision_id for gap detection
        prev_id = decision.get("prev_decision_id")
        if prev_id:
            ack_path = str(path) + ".ack"
            try:
                acked_id = Path(ack_path).read_text().strip() if Path(ack_path).exists() else ""
            except Exception:
                acked_id = ""
            if acked_id and prev_id != acked_id:
                logging.warning(
                    f"⚠️ AI decision prev_decision_id={prev_id} doesn't match last ACKed={acked_id} "
                    f"— potential decision gap, processing anyway"
                )

        # BUG 3: Check if we already processed this decision (duplicate execution guard)
        decision_id = decision.get("decision_id", "")
        result_path = Path(self._ai_result_file)
        if result_path.exists() and decision_id:
            existing_result = safe_read_json(result_path)
            if existing_result and existing_result.get("processed_decision_id") == decision_id:
                logging.info(f"⏩ Decision {decision_id} already processed (result file exists), skipping execution")
                return

        # Execute inside try/except — ACK + result are written AFTER execution
        # completes (success or failure), NOT if an uncaught exception occurs.
        try:
            if action == "close_all":
                success = await self._execute_ai_close_all(decision)
            elif action == "open":
                success = await self._execute_ai_open(decision)
            elif action == "close":
                success = await self._execute_ai_close(decision)
            else:
                success = True

            # HIGH-3: Write ACK BEFORE result — ACK = "I consumed this decision."
            # If bot crashes between ACK and result write, AI trader won't re-send.
            # Result is supplementary (positions context). ACK is essential.
            ack_path = str(path) + ".ack"
            with open(ack_path, "w") as f:
                f.write(decision.get("decision_id", ""))
            self._write_ai_result(decision, success=success)
            # BUG 3: Save state immediately after ACK so _last_ai_decision_ts persists before ACK
            self.bot._save_state()
        except Exception as e:
            logging.error(f"❌ AI decision execution crashed — NOT writing ACK: {e}", exc_info=True)
            # Do NOT write result or ACK — the AI trader will re-deliver the decision



    async def _execute_ai_open(self, decision: dict) -> bool:
        """Execute an AI-recommended open. Returns True on success."""
        if self._kill_switch_active:
            logging.warning(f"🚫 Kill switch active — AI open blocked for {decision.get('symbol', '?')}")
            return False

        symbol = decision.get("symbol")
        direction = decision.get("direction")
        # IPC-03: use requested_size_usd (new) with fallback to size_usd (legacy)
        size_usd = decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)

        if not symbol or not direction or size_usd <= 0:
            logging.warning(f"AI open: invalid decision fields")
            return False

        # Cap size_usd at 10x equity
        balance = await self.bot._get_balance()
        if balance > 0:
            max_size = balance * 10
            if size_usd > max_size:
                logging.warning(f"⚠️ AI open: size_usd=${size_usd:.2f} capped to ${max_size:.2f} (10x equity)")
                size_usd = max_size

        # Resolve market ID
        market_id = self._resolve_market_id(symbol)
        if market_id is None:
            logging.warning(f"AI open: unknown symbol {symbol}")
            return False

        # Check AI close cooldown — prevent reopening recently closed symbols
        cooldown_until = self._ai_close_cooldown.get(symbol)
        if cooldown_until and time.monotonic() < cooldown_until:
            remaining = int(cooldown_until - time.monotonic())
            logging.info(f"🧊 AI open: {symbol} in close cooldown ({remaining}s remaining) - SKIPPING")
            return False

        if market_id in self.tracker.positions:
            logging.info(f"AI open: already in {symbol}, skipping")
            return False

        # Cap at 8 concurrent positions
        if len(self.tracker.positions) >= 8:
            logging.info(f"AI open: max positions reached, skipping {symbol}")
            return False

        # Check pacing
        if self._should_pace_orders():
            logging.info(f"⏱️ AI open: {symbol} pacing orders (low quota) — skipping")
            return False

        # Quota prioritization: skip new opens when quota < 35 to preserve for SL orders
        if self._should_skip_open_for_quota():
            quota = self.api.volume_quota_remaining if self.api else None
            logging.warning(f"🚫 {symbol}: new opens paused (quota={quota} < 35, SL protection prioritized)")
            return False

        is_long = direction == "long"
        current_price = await self.api.get_price(market_id)
        if not current_price:
            logging.warning(f"AI open: no price for {symbol}")
            return False

        success = await self.api.open_position(market_id, size_usd, is_long, current_price)
        if success:
            self._mark_order_submitted()

            # Fix #13: Add to pending_sync and bot_managed BEFORE verification
            # to prevent race conditions if _tick() sync cycle runs mid-verification
            self._pending_sync.add(market_id)
            self.bot.bot_managed_market_ids.add(market_id)

            # Verify position exists on exchange (BUG-03 fix)
            expected_size = size_usd / current_price
            verified_pos = await self._verify_position_opened(market_id, expected_size, symbol)
            if verified_pos is None:
                # CRITICAL-2: Don't discard — add as unverified so we can re-verify on next ticks
                # Position may be on exchange but API is slow to reflect it
                logging.error(f"❌ AI open: {symbol} verification failed — tracking as unverified (will re-verify)")
                ai_sl_pct = decision.get("stop_loss_pct")
                ai_leverage = min(float(decision.get("leverage", self.cfg.default_leverage)), 10)
                self.tracker.add_position(market_id, symbol, direction, current_price, expected_size, leverage=ai_leverage, sl_pct=ai_sl_pct)
                pos = self.tracker.positions.get(market_id)
                if pos:
                    pos.unverified_at = time.time()
                    pos.unverified_ticks = 1
                self.bot._save_state()
                await self.alerts.send(
                    f"⚠️ *POSITION UNVERIFIED*\n"
                    f"{direction.upper()} {symbol}\n"
                    f"Order submitted but verification failed.\n"
                    f"Will re-verify on next ticks."
                )
                return True  # Order was submitted, we're tracking it as unverified
            # Use actual filled size from exchange (handles partial fills)
            actual_size = verified_pos["size"]
            ai_sl_pct = decision.get("stop_loss_pct")
            ai_leverage = min(float(decision.get("leverage", self.cfg.default_leverage)), 10)
            self.tracker.add_position(market_id, symbol, direction, current_price, actual_size, leverage=ai_leverage, sl_pct=ai_sl_pct)

            # NOTE: DSL uses config leverage from add_position, NOT exchange-reported leverage.
            # Exchange leverage can vary for cross margin and would break DSL tier calibration.

            # Persist state immediately after opening to prevent crash data loss
            self.bot._save_state()

            # BUG-06: Verify we can actually fetch price for this position after open
            # If price is unavailable, the position becomes "orphaned" — DSL can't compute ROE
            price_ok = False
            for attempt in range(1, 4):
                verify_price = await self.api.get_price_with_mark_fallback(market_id)
                if verify_price:
                    price_ok = True
                    if attempt > 1:
                        logging.info(f"✅ {symbol}: price verified on retry {attempt}/3 = ${verify_price:,.2f}")
                    break
                if attempt < 3:
                    await asyncio.sleep(1)
            if not price_ok:
                logging.error(f"❌ AI open: {symbol} — no price after 3 attempts, removing orphaned position")
                self.tracker.remove_position(market_id)
                self._pending_sync.discard(market_id)
                await self.alerts.send(
                    f"❌ *AI OPEN FAILED*\n"
                    f"{direction.upper()} {symbol}\n"
                    f"Order filled but price unavailable — position removed.\n"
                    f"Order may need manual cleanup on exchange."
                )
                return False

            logging.info(f"📊 Quota remaining: {self.api.volume_quota_remaining}")
            # Reset close attempt tracking for this symbol
            self._close_attempts.pop(symbol, None)
            self._close_attempt_cooldown.pop(symbol, None)
            await self.alerts.send(
                f"🤖 *AI → OPENED*\n"
                f"{direction.upper()} {symbol}\n"
                f"Size: ${size_usd:.2f}\n"
                f"Reason: {decision.get('reasoning', '?')[:200]}"
            )
            logging.info(f"AI opened: {direction} {symbol} ${size_usd:.2f}")
        return success



    async def _check_active_orders(self, market_id: int) -> list[dict]:
        """Check if there are any active (unfilled) orders for this market on our account.

        Used by MED-18 cancel logic to find order_index for cancellation.
        """
        try:
            await self.api._ensure_client()
            # Generate auth token for the request
            auth = None
            try:
                await self.api._ensure_signer()
                if self.api._signer is not None:
                    if not hasattr(self.bot, '_auth_manager'):
                        self.bot._auth_manager = LighterAuthManager(
                            signer=self.api._signer,
                            account_index=self.cfg.account_index
                        )
                    auth = self.bot._auth_manager.get_auth_token()
            except Exception as auth_err:
                logging.debug(f"Auth generation skipped: {auth_err}")
            orders = await self.api._order_api.account_active_orders(
                account_index=self.cfg.account_index,
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



    async def _verify_position_closed(self, market_id: int, symbol: str) -> bool:
        """Poll the Lighter API to verify a position is actually closed after a close order.
        Uses progressively longer delays to account for exchange processing time.
        MED-25: Adds market_id to _verifying_close to skip DSL/SL evaluation during verification.
        """
        self._verifying_close.add(market_id)
        try:
            delays = [3, 5, 7, 10]  # MED-25: reduced from [5,10,15,20] — 25s total instead of 50s
            for attempt, delay in enumerate(delays):
                await asyncio.sleep(delay)
                try:
                    live_positions = await self.api.get_positions()
                    still_open = any(p["market_id"] == market_id and abs(p.get("size", 0)) > 0.001 for p in live_positions)
                    if not still_open:
                        logging.info(f"✅ {symbol}: position closure verified (attempt {attempt + 1}, after {delay}s)")
                        return True
                    # Also check if there are any active orders (the close order might still be pending)
                    active_orders = await self._check_active_orders(market_id)
                    sl_orders = [o for o in active_orders]  # all active orders are close-related (both long close and short close)
                    logging.info(
                        f"⏳ {symbol}: position still open (attempt {attempt + 1}/{len(delays)}), "
                        f"active_orders={len(active_orders)}, sl_orders={len(sl_orders)}"
                    )
                except Exception as e:
                    logging.warning(f"⚠️ {symbol}: error verifying closure (attempt {attempt + 1}): {e}")
            return False
        finally:
            self._verifying_close.discard(market_id)



    async def _verify_position_opened(self, market_id: int, expected_size: float, symbol: str) -> dict | None:
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
                live_positions = await self.api.get_positions()
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



    async def _get_fill_price(self, market_id: int, client_order_index: str | None) -> float | None:
        """Query Lighter API for actual fill price of a closed order."""
        if not client_order_index:
            return None
        try:
            if not hasattr(self.bot, '_auth_manager'):
                self.bot._auth_manager = LighterAuthManager(
                    signer=self.api._signer,
                    account_index=self.cfg.account_index
                )
            auth = self.bot._auth_manager.get_auth_token()
            base = self.cfg.lighter_url.rstrip('/')
            url = f'{base}/api/v1/accountInactiveOrders?account_index={self.cfg.account_index}&limit=100&auth={auth}'
            # Reuse or create a session for fill price queries
            if not hasattr(self, '_http_session') or self._http_session.closed:
                self._http_session = aiohttp.ClientSession()
            async with self._http_session.get(url) as resp:
                data = await resp.json()
                if data.get("code") == 200 and "orders" in data:
                    for o in data["orders"]:
                        coi = str(o.get("client_order_index", ""))
                        if coi == str(client_order_index):
                            filled_base = float(o.get("filled_base_amount", 0))
                            filled_quote = float(o.get("filled_quote_amount", 0))
                            if filled_base > 0:
                                return filled_quote / filled_base
            return None
        except Exception as e:
            logging.debug(f"Could not fetch fill price: {e}")
            return None



    async def _execute_ai_close(self, decision: dict) -> bool:
        """Execute an AI-recommended close. Returns True on success (position actually closed)."""
        symbol = decision.get("symbol")
        if not symbol:
            return False

        # Check close attempt cooldown — if we've failed too many times, skip
        cooldown_until = self._close_attempt_cooldown.get(symbol)
        if cooldown_until and time.monotonic() < cooldown_until:
            remaining = int(cooldown_until - time.monotonic())
            logging.info(f"🧊 AI close: {symbol} in close attempt cooldown ({remaining}s remaining) — skipping. Position may need manual intervention.")
            return False

        # Find position by symbol
        mid_to_close = None
        for mid, pos in self.tracker.positions.items():
            if pos.symbol == symbol:
                mid_to_close = mid
                break

        if mid_to_close is None:
            logging.info(f"AI close: no position in {symbol}")
            # Reset attempt counter if position is gone
            self._close_attempts.pop(symbol, None)
            return False

        pos = self.tracker.positions[mid_to_close]
        is_long = pos.side == "long"
        current_price = await self.api.get_price(mid_to_close)
        if not current_price:
            return False

        # MED-18: Cancel stale SL order before placing new one
        if pos.active_sl_order_id:
            logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before AI close")
            await self.api._cancel_order(mid_to_close, int(pos.active_sl_order_id))
            pos.active_sl_order_id = None
        sl_success, sl_coi = await self.api.execute_sl(mid_to_close, pos.size, current_price, is_long)
        if sl_success and sl_coi:
            pos.active_sl_order_id = sl_coi
        if not sl_success:
            # Track attempts with graduated delay
            attempts = self._close_attempts.get(symbol, 0) + 1
            self._close_attempts[symbol] = attempts
            delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
            retry_delay = self._sl_retry_delays[delay_idx]
            self._close_attempt_cooldown[symbol] = time.monotonic() + retry_delay
            logging.warning(f"⚠️ Failed to submit close order for {pos.side} {symbol} (attempt {attempts}, retry in {retry_delay}s)")
            return False

        # CRITICAL-4: Don't log outcome yet — log ONCE after verification
        # Now verify it actually filled by polling the API
        position_closed = await self._verify_position_closed(mid_to_close, symbol)

        if not position_closed:
            # Increment attempt counter
            attempts = self._close_attempts.get(symbol, 0) + 1
            self._close_attempts[symbol] = attempts
            logging.warning(f"⚠️ {symbol}: close order submitted but position still open (attempt {attempts}/{self._max_close_attempts})")

            if attempts >= self._max_close_attempts:
                # Escalate: set cooldown and alert
                self._close_attempt_cooldown[symbol] = time.monotonic() + self._close_cooldown_seconds
                # CRITICAL-4: Log with estimated price as fallback after all retries exhausted
                self._log_outcome(pos, current_price, "ai_close", estimated=True)
                roe = ((current_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                    else ((pos.entry_price - current_price) / pos.entry_price * 100)
                await self.alerts.send(
                    f"🚨 *CLOSE FAILED ×{attempts}*\n"
                    f"{pos.side.upper()} {symbol}\n"
                    f"ROE: {roe:+.1f}%\n"
                    f"Order submitted but NOT filled after {attempts} attempts.\n"
                    f"Cooldown: {self._close_cooldown_seconds // 60}min — may need manual intervention."
                )
                logging.error(f"🚨 {symbol}: max close attempts ({self._max_close_attempts}) reached. Setting {self._close_cooldown_seconds}s cooldown.")
            return False

        # Position successfully closed — reset attempt counter
        self._close_attempts.pop(symbol, None)
        self._close_attempt_cooldown.pop(symbol, None)

        fill_price = await self._get_fill_price(mid_to_close, sl_coi)
        exit_price = fill_price if fill_price else current_price
        # CRITICAL-4: Log outcome ONCE with actual fill price after verification
        self._log_outcome(pos, exit_price, "ai_close")
        self._recently_closed[mid_to_close] = time.monotonic() + 300  # 5 min phantom guard
        pos.active_sl_order_id = None  # MED-18
        self.bot.bot_managed_market_ids.discard(mid_to_close)
        self.tracker.remove_position(mid_to_close)

        roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
            else ((pos.entry_price - exit_price) / pos.entry_price * 100)

        await self.alerts.send(
            f"🤖 *AI → CLOSED*\n"
            f"{pos.side.upper()} {symbol}\n"
            f"ROE: {roe:+.1f}%\n"
            f"Reason: {decision.get('reasoning', '?')[:200]}"
        )
        logging.info(f"AI closed: {pos.side} {symbol} ROE={roe:+.1f}%")

        # Set cooldown — prevent re-opening this symbol for N minutes
        self._ai_close_cooldown[symbol] = time.monotonic() + self._ai_cooldown_seconds
        logging.info(f"🧊 {symbol}: AI close cooldown set ({self._ai_cooldown_seconds}s)")
        return True



    async def _execute_ai_close_all(self, decision: dict) -> bool:
        """Emergency close all positions — with verification."""
        reasoning = decision.get("reasoning", "Emergency halt")
        logging.warning(f"🚨 AI close_all triggered: {reasoning}")
        await self.alerts.send(
            f"🚨 *AI → CLOSE ALL*\n"
            f"Reason: {reasoning[:200]}"
        )

        failed_positions = []

        for i, (mid, pos) in enumerate(list(self.tracker.positions.items())):
            is_long = pos.side == "long"
            current_price = await self.api.get_price(mid) if self.api else None
            if i < len(self.tracker.positions) - 1:
                await asyncio.sleep(self.cfg.price_call_delay)

            if not current_price:
                logging.warning(f"⚠️ No price for {pos.symbol} — skipping close, keeping in tracker")
                failed_positions.append(pos.symbol)
                continue

            # MED-18: Cancel stale SL order before placing new one
            if pos.active_sl_order_id:
                logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before close_all")
                await self.api._cancel_order(mid, int(pos.active_sl_order_id))
                pos.active_sl_order_id = None
            sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, current_price, is_long)
            if sl_success and sl_coi:
                pos.active_sl_order_id = sl_coi

            if not sl_success:
                logging.warning(f"⚠️ Failed to submit close order for {pos.side} {pos.symbol}")
                failed_positions.append(pos.symbol)
                continue

            # Order submitted — verify it actually filled
            position_closed = await self._verify_position_closed(mid, pos.symbol)

            if position_closed:
                # Get actual fill price for accurate outcome logging
                fill_price = await self._get_fill_price(mid, sl_coi)
                exit_price = fill_price if fill_price else current_price
                # HIGH-6: Log outcome to DB for close_all positions
                self._log_outcome(pos, exit_price, "ai_close_all")
                roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                    else ((pos.entry_price - exit_price) / pos.entry_price * 100)
                logging.info(f"Emergency closed: {pos.side} {pos.symbol} ROE={roe:+.1f}%")
                self._recently_closed[mid] = time.monotonic() + 300
                pos.active_sl_order_id = None  # MED-18
                self.bot.bot_managed_market_ids.discard(mid)
                self.tracker.remove_position(mid)
                await self.alerts.send(
                    f"✅ *CLOSE ALL → {pos.side.upper()} {pos.symbol}* closed"
                )
            else:
                logging.warning(f"⚠️ {pos.symbol}: close order submitted but position still open after verification")
                failed_positions.append(pos.symbol)
                await self.alerts.send(
                    f"⚠️ *CLOSE ALL → {pos.symbol}* verification failed — position may still be open"
                )

        return len(failed_positions) == 0



    def _resolve_market_id(self, symbol: str) -> int | None:
        """Resolve symbol to market_id. Tries scanner signals first, then cached positions."""
        # Try from signals file
        signals_path = Path(self._signals_file)
        if signals_path.exists():
            # MED-23: Check staleness before using signals for market ID resolution.
            # Stale signals may have wrong market IDs (scanner could have reassigned IDs).
            try:
                signals_age = time.time() - signals_path.stat().st_mtime
                if signals_age > 600:  # 10 minutes
                    logging.warning(f"⚠️ _resolve_market_id: signals.json stale (age={signals_age:.0f}s), skipping signal-based resolution for {symbol}")
                    signals_path = None  # Skip to position tracker fallback
            except OSError:
                pass

            if signals_path and signals_path.exists():
                data = safe_read_json(signals_path)
                if data:
                    for opp in data.get("opportunities", []):
                        if opp.get("symbol") == symbol:
                            return opp.get("marketId")

        # Try from position tracker (already-open positions)
        for mid, pos in self.tracker.positions.items():
            if pos.symbol == symbol:
                return mid

        return None



    def _log_outcome(self, pos: TrackedPosition, exit_price: float, exit_reason: str,
                     estimated: bool = False):
        """Log a closed trade outcome to the AI trader journal DB.

        Called ONCE per close — either with actual fill price after verification
        succeeds, or with estimated=True as fallback after max verification retries.
        This ensures only one outcome row per close (no double-writing).

        PnL math (no double-counting of leverage):
          pnl_pct  = raw price movement % (not leveraged)
          size_usd = notional position value at entry (size × entry_price)
          pnl_usd  = actual dollar P&L = notional × pnl_pct / 100
                     (this IS the real dollar gain/loss, leverage doesn't change it —
                      1 BTC moved $100 is $100 whether you used 1x or 10x margin)
          roe_pct  = Return on Equity % = pnl_pct × leverage
                     (what you earned relative to your margin deposit)
        """
        if _db is None:
            return
        try:
            is_long = pos.side == "long"
            # Raw price movement percentage (NOT leveraged)
            pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                else ((pos.entry_price - exit_price) / pos.entry_price * 100)
            # Notional position value at entry
            size_usd = pos.size * pos.entry_price
            # Dollar P&L = notional × raw price change % (leverage doesn't affect dollar P&L)
            pnl_usd = size_usd * pnl_pct / 100
            hold_seconds = int((datetime.now(timezone.utc) - pos.opened_at).total_seconds())
            # ROE = return relative to margin (not notional) = pnl_pct × leverage
            # For cross margin: effective_leverage = notional / equity
            # Fall back to DSL state leverage, then config default
            equity = self.tracker.account_equity
            if equity > 0 and size_usd > 0:
                actual_leverage = size_usd / equity
            elif pos.dsl_state:
                actual_leverage = pos.dsl_state.leverage
            else:
                actual_leverage = self.cfg.default_leverage
            roe_pct = pnl_pct * actual_leverage

            # Mark as estimated if we haven't verified the fill yet
            reason_tag = f"{exit_reason} (estimated)" if estimated else exit_reason
            _db.log_outcome({
                "symbol": pos.symbol,
                "direction": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "size_usd": size_usd,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "roe_pct": roe_pct,
                "hold_time_seconds": hold_seconds,
                "max_drawdown_pct": 0,  # not tracked yet
                "exit_reason": reason_tag,
                "decision_snapshot": {},
            })
            tag = " (est)" if estimated else ""
            logging.info(
                f"📝 Outcome logged{tag}: {pos.side} {pos.symbol} "
                f"PnL=${pnl_usd:+.2f} ({roe_pct:+.1f}% ROE @ {actual_leverage:.1f}x) "
                f"held={hold_seconds}s reason={exit_reason}"
            )
        except Exception as e:
            logging.warning(f"Failed to log outcome: {e}")



    def _write_ai_result(self, decision: dict, success: bool):
        """Write execution result for the AI trader to read."""
        try:
            positions = []
            for mid, pos in self.tracker.positions.items():
                current_price = self.api.get_mark_price(mid) if self.api else None
                positions.append({
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "current_price": current_price if current_price and current_price > 0 else pos.entry_price,
                    "size": pos.size,
                    "leverage": pos.dsl_state.effective_leverage if pos.dsl_state else self.cfg.default_leverage,
                    "position_size_usd": pos.size * pos.entry_price,
                })
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "processed_decision_id": decision.get("decision_id"),
                "processed_timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_action": decision.get("action"),
                "decision_symbol": decision.get("symbol"),
                "success": success,
                "positions": positions,
            }
            # Atomic write (same pattern as ai-trader)
            tmp = str(self._ai_result_file) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(result, f, indent=2)
            os.replace(tmp, str(self._ai_result_file))
            # HIGH-10: Mark result as fresh — refresh should not overwrite until next tick
            self._result_dirty = True
        except Exception as e:
            logging.warning(f"Failed to write AI result: {e}")



    def _refresh_position_context(self):
        """MED-4: Write updated positions to result file between AI decisions.

        Preserves the last processed_decision_id so AI trader can still correlate,
        but updates positions to reflect DSL/SL/TP closes that happened since
        the last AI decision was processed.
        """
        # HIGH-10: Skip if a fresh AI result was just written — don't overwrite
        # the AI trader's result before it has a chance to read it.
        if self._result_dirty:
            logging.debug("Refresh skipped: result is dirty (fresh AI result not yet consumed)")
            return
        try:
            existing = safe_read_json(Path(self._ai_result_file))
            last_decision_id = existing.get("processed_decision_id") if existing else None

            positions = []
            for mid, pos in self.tracker.positions.items():
                current_price = self.api.get_mark_price(mid) if self.api else None
                positions.append({
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "current_price": current_price if current_price and current_price > 0 else pos.entry_price,
                    "size": pos.size,
                    "leverage": pos.dsl_state.effective_leverage if pos.dsl_state else self.cfg.default_leverage,
                    "position_size_usd": pos.size * pos.entry_price,
                })
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "processed_decision_id": last_decision_id,  # Keep last AI decision ID for correlation
                "processed_timestamp": existing.get("processed_timestamp") if existing else datetime.now(timezone.utc).isoformat(),
                "decision_action": existing.get("decision_action") if existing else "refresh",
                "decision_symbol": existing.get("decision_symbol") if existing else None,
                "success": existing.get("success", True) if existing else True,
                "positions": positions,
            }
            tmp = str(self._ai_result_file) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(result, f, indent=2)
            os.replace(tmp, str(self._ai_result_file))
        except Exception as e:
            logging.debug(f"Failed to refresh position context: {e}")



