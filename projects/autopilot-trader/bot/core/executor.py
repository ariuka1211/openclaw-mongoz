"""
AI decision execution functions extracted from SignalProcessor.

Execute AI-recommended open, close, and close_all actions with verification.
"""

import asyncio
import logging
import time

from core.shared_utils import (
    should_pace_orders,
    should_skip_open_for_quota,
    mark_order_submitted,
    resolve_market_id,
    get_fill_price,
    log_outcome,
)
from core.verifier import verify_position_opened, verify_position_closed
from core.result_writer import write_ai_result


async def execute_ai_open(bot, cfg, api, tracker, alerter, decision: dict) -> bool:
    """Execute an AI-recommended open. Returns True on success."""
    if bot._kill_switch_active:
        logging.warning(f"🚫 Kill switch active — AI open blocked for {decision.get('symbol', '?')}")
        return False

    symbol = decision.get("symbol")
    direction = decision.get("direction")
    # IPC-03: use requested_size_usd (new) with fallback to size_usd (legacy)
    size_usd = decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)

    if not symbol or not direction or size_usd <= 0:
        logging.warning(f"AI open: invalid decision fields")
        return False


    # Resolve market ID
    market_id = resolve_market_id(bot, tracker, symbol)
    if market_id is None:
        logging.warning(f"AI open: unknown symbol {symbol}")
        return False

    # Check AI close cooldown — prevent reopening recently closed symbols
    cooldown_until = bot._ai_close_cooldown.get(symbol)
    if cooldown_until and time.monotonic() < cooldown_until:
        remaining = int(cooldown_until - time.monotonic())
        logging.info(f"🧊 AI open: {symbol} in close cooldown ({remaining}s remaining) - SKIPPING")
        return False

    if market_id in tracker.positions:
        logging.info(f"AI open: already in {symbol}, skipping")
        return False

    # Cap at 8 concurrent positions
    if len(tracker.positions) >= 8:
        logging.info(f"AI open: max positions reached, skipping {symbol}")
        return False

    # Check pacing
    if should_pace_orders(bot):
        logging.info(f"⏱️ AI open: {symbol} pacing orders (low quota) — skipping")
        return False

    # Quota prioritization: skip new opens when quota < 35 to preserve for SL orders
    if should_skip_open_for_quota(bot, api):
        quota = api.volume_quota_remaining if api else None
        logging.warning(f"🚫 {symbol}: new opens paused (quota={quota} < 35, SL protection prioritized)")
        return False

    is_long = direction == "long"
    current_price = await api.get_price(market_id)
    if not current_price:
        logging.warning(f"AI open: no price for {symbol}")
        return False

    # Apply margin cap using REAL exchange leverage
    balance = await bot._get_balance()
    actual_leverage = await api.get_market_leverage(market_id)
    max_notional = balance * cfg.max_margin_pct * actual_leverage
    risk_size = size_usd
    if size_usd > max_notional:
        logging.info(f"📐 {symbol}: margin capped ${risk_size:.2f} → ${max_notional:.2f} (lev={actual_leverage:.0f}x)")
        size_usd = max_notional

    success = await api.open_position(market_id, size_usd, is_long, current_price)
    if success:
        mark_order_submitted(bot)

        # Fix #13: Add to pending_sync and bot_managed BEFORE verification
        # to prevent race conditions if _tick() sync cycle runs mid-verification
        bot._pending_sync.add(market_id)
        bot.bot_managed_market_ids.add(market_id)

        # Verify position exists on exchange (BUG-03 fix)
        expected_size = size_usd / current_price
        verified_pos = await verify_position_opened(api, market_id, expected_size, symbol)
        if verified_pos is None:
            # CRITICAL-2: Don't discard — add as unverified so we can re-verify on next ticks
            # Position may be on exchange but API is slow to reflect it
            logging.error(f"❌ AI open: {symbol} verification failed — tracking as unverified (will re-verify)")
            ai_sl_pct = decision.get("stop_loss_pct")
            tracker.add_position(market_id, symbol, direction, current_price, expected_size, leverage=cfg.dsl_leverage, sl_pct=ai_sl_pct)
            pos = tracker.positions.get(market_id)
            if pos:
                pos.unverified_at = time.time()
                pos.unverified_ticks = 1
            bot._save_state()
            await alerter.send(
                f"⚠️ *POSITION UNVERIFIED*\n"
                f"{direction.upper()} {symbol}\n"
                f"Order submitted but verification failed.\n"
                f"Will re-verify on next ticks."
            )
            return True  # Order was submitted, we're tracking it as unverified
        # Use actual filled size from exchange (handles partial fills)
        actual_size = verified_pos["size"]
        ai_sl_pct = decision.get("stop_loss_pct")
        tracker.add_position(market_id, symbol, direction, current_price, actual_size, leverage=cfg.dsl_leverage, sl_pct=ai_sl_pct)

        # Persist state immediately after opening to prevent crash data loss
        bot._save_state()

        # BUG-06: Verify we can actually fetch price for this position after open
        # If price is unavailable, the position becomes "orphaned" — DSL can't compute ROE
        price_ok = False
        for attempt in range(1, 4):
            verify_price = await api.get_price_with_mark_fallback(market_id)
            if verify_price:
                price_ok = True
                if attempt > 1:
                    logging.info(f"✅ {symbol}: price verified on retry {attempt}/3 = ${verify_price:,.2f}")
                break
            if attempt < 3:
                await asyncio.sleep(1)
        if not price_ok:
            logging.error(f"❌ AI open: {symbol} — no price after 3 attempts, removing orphaned position")
            tracker.remove_position(market_id)
            bot._pending_sync.discard(market_id)
            bot._opened_signals.discard(market_id)
            await alerter.send(
                f"❌ *AI OPEN FAILED*\n"
                f"{direction.upper()} {symbol}\n"
                f"Order filled but price unavailable — position removed.\n"
                f"Order may need manual cleanup on exchange."
            )
            return False

        logging.info(f"📊 Quota remaining: {api.volume_quota_remaining}")
        # Reset close attempt tracking for this symbol
        bot._close_attempts.pop(symbol, None)
        bot._close_attempt_cooldown.pop(symbol, None)
        await alerter.send(
            f"🤖 *AI → OPENED*\n"
            f"{direction.upper()} {symbol}\n"
            f"Size: ${size_usd:.2f}\n"
            f"Reason: {decision.get('reasoning', '?')[:200]}"
        )
        logging.info(f"AI opened: {direction} {symbol} ${size_usd:.2f}")
    return success


async def execute_ai_close(bot, cfg, api, tracker, alerter, decision: dict) -> bool:
    """Execute an AI-recommended close. Returns True on success (position actually closed)."""
    symbol = decision.get("symbol")
    if not symbol:
        return False

    # Check close attempt cooldown — if we've failed too many times, skip
    cooldown_until = bot._close_attempt_cooldown.get(symbol)
    if cooldown_until and time.monotonic() < cooldown_until:
        remaining = int(cooldown_until - time.monotonic())
        logging.info(f"🧊 AI close: {symbol} in close attempt cooldown ({remaining}s remaining) — skipping. Position may need manual intervention.")
        return False

    # Find position by symbol
    mid_to_close = None
    for mid, pos in tracker.positions.items():
        if pos.symbol == symbol:
            mid_to_close = mid
            break

    if mid_to_close is None:
        logging.info(f"AI close: no position in {symbol}")
        # Reset attempt counter if position is gone
        bot._close_attempts.pop(symbol, None)
        return False

    pos = tracker.positions[mid_to_close]
    is_long = pos.side == "long"
    current_price = await api.get_price(mid_to_close)
    if not current_price:
        return False

    # MED-18: Cancel stale SL order before placing new one
    if pos.active_sl_order_id:
        logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before AI close")
        await api._cancel_order(mid_to_close, int(pos.active_sl_order_id))
        pos.active_sl_order_id = None
    sl_success, sl_coi = await api.execute_sl(mid_to_close, pos.size, current_price, is_long)
    if sl_success and sl_coi:
        pos.active_sl_order_id = sl_coi
    if not sl_success:
        # Track attempts with graduated delay
        attempts = bot._close_attempts.get(symbol, 0) + 1
        bot._close_attempts[symbol] = attempts
        delay_idx = min(attempts - 1, len(bot._sl_retry_delays) - 1)
        retry_delay = bot._sl_retry_delays[delay_idx]
        bot._close_attempt_cooldown[symbol] = time.monotonic() + retry_delay
        logging.warning(f"⚠️ Failed to submit close order for {pos.side} {symbol} (attempt {attempts}, retry in {retry_delay}s)")
        return False

    # CRITICAL-4: Don't log outcome yet — log ONCE after verification
    # Now verify it actually filled by polling the API
    position_closed = await verify_position_closed(bot, api, mid_to_close, symbol)

    if not position_closed:
        # Increment attempt counter
        attempts = bot._close_attempts.get(symbol, 0) + 1
        bot._close_attempts[symbol] = attempts
        logging.warning(f"⚠️ {symbol}: close order submitted but position still open (attempt {attempts}/{bot._max_close_attempts})")

        if attempts >= bot._max_close_attempts:
            # Escalate: set cooldown and alert
            bot._close_attempt_cooldown[symbol] = time.monotonic() + bot._close_cooldown_seconds
            # CRITICAL-4: Log with estimated price as fallback after all retries exhausted
            log_outcome(pos, current_price, "ai_close", cfg, tracker, estimated=True)
            roe = ((current_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                else ((pos.entry_price - current_price) / pos.entry_price * 100)
            await alerter.send(
                f"🚨 *CLOSE FAILED ×{attempts}*\n"
                f"{pos.side.upper()} {symbol}\n"
                f"ROE: {roe:+.1f}%\n"
                f"Order submitted but NOT filled after {attempts} attempts.\n"
                f"Cooldown: {bot._close_cooldown_seconds // 60}min — may need manual intervention."
            )
            logging.error(f"🚨 {symbol}: max close attempts ({bot._max_close_attempts}) reached. Setting {bot._close_cooldown_seconds}s cooldown.")
        return False

    # Position successfully closed — reset attempt counter
    bot._close_attempts.pop(symbol, None)
    bot._close_attempt_cooldown.pop(symbol, None)

    fill_price = await get_fill_price(bot, cfg, mid_to_close, sl_coi)
    exit_price = fill_price if fill_price else current_price
    # CRITICAL-4: Log outcome ONCE with actual fill price after verification
    log_outcome(pos, exit_price, "ai_close", cfg, tracker)
    bot._recently_closed[mid_to_close] = time.monotonic() + 300  # 5 min phantom guard
    pos.active_sl_order_id = None  # MED-18
    bot.bot_managed_market_ids.discard(mid_to_close)
    tracker.remove_position(mid_to_close)
    bot._opened_signals.discard(mid_to_close)

    roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
        else ((pos.entry_price - exit_price) / pos.entry_price * 100)

    await alerter.send(
        f"🤖 *AI → CLOSED*\n"
        f"{pos.side.upper()} {symbol}\n"
        f"ROE: {roe:+.1f}%\n"
        f"Reason: {decision.get('reasoning', '?')[:200]}"
    )
    logging.info(f"AI closed: {pos.side} {symbol} ROE={roe:+.1f}%")

    # Set cooldown — prevent re-opening this symbol for N minutes
    bot._ai_close_cooldown[symbol] = time.monotonic() + bot._ai_cooldown_seconds
    logging.info(f"🧊 {symbol}: AI close cooldown set ({bot._ai_cooldown_seconds}s)")
    return True


async def execute_ai_close_all(bot, cfg, api, tracker, alerter, decision: dict) -> bool:
    """Emergency close all positions — with verification."""
    reasoning = decision.get("reasoning", "Emergency halt")
    logging.warning(f"🚨 AI close_all triggered: {reasoning}")
    await alerter.send(
        f"🚨 *AI → CLOSE ALL*\n"
        f"Reason: {reasoning[:200]}"
    )

    failed_positions = []

    for i, (mid, pos) in enumerate(list(tracker.positions.items())):
        is_long = pos.side == "long"
        current_price = await api.get_price(mid) if api else None
        if i < len(tracker.positions) - 1:
            await asyncio.sleep(cfg.price_call_delay)

        if not current_price:
            logging.warning(f"⚠️ No price for {pos.symbol} — skipping close, keeping in tracker")
            failed_positions.append(pos.symbol)
            continue

        # MED-18: Cancel stale SL order before placing new one
        if pos.active_sl_order_id:
            logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before close_all")
            await api._cancel_order(mid, int(pos.active_sl_order_id))
            pos.active_sl_order_id = None
        sl_success, sl_coi = await api.execute_sl(mid, pos.size, current_price, is_long)
        if sl_success and sl_coi:
            pos.active_sl_order_id = sl_coi

        if not sl_success:
            logging.warning(f"⚠️ Failed to submit close order for {pos.side} {pos.symbol}")
            failed_positions.append(pos.symbol)
            continue

        # Order submitted — verify it actually filled
        position_closed = await verify_position_closed(bot, api, mid, pos.symbol)

        if position_closed:
            # Get actual fill price for accurate outcome logging
            fill_price = await get_fill_price(bot, cfg, mid, sl_coi)
            exit_price = fill_price if fill_price else current_price
            # HIGH-6: Log outcome to DB for close_all positions
            log_outcome(pos, exit_price, "ai_close_all", cfg, tracker)
            roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                else ((pos.entry_price - exit_price) / pos.entry_price * 100)
            logging.info(f"Emergency closed: {pos.side} {pos.symbol} ROE={roe:+.1f}%")
            bot._recently_closed[mid] = time.monotonic() + 300
            pos.active_sl_order_id = None  # MED-18
            bot.bot_managed_market_ids.discard(mid)
            tracker.remove_position(mid)
            bot._opened_signals.discard(mid)
            await alerter.send(
                f"✅ *CLOSE ALL → {pos.side.upper()} {pos.symbol}* closed"
            )
        else:
            logging.warning(f"⚠️ {pos.symbol}: close order submitted but position still open after verification")
            failed_positions.append(pos.symbol)
            await alerter.send(
                f"⚠️ *CLOSE ALL → {pos.symbol}* verification failed — position may still be open"
            )

    return len(failed_positions) == 0
