"""
Scanner signal processing extracted from SignalProcessor.

Reads signals.json and opens positions for new, unopened signals.
"""

import asyncio
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json
from core.shared_utils import (
    should_pace_orders,
    should_skip_open_for_quota,
    mark_order_submitted,
    write_equity_file,
)
from core.verifier import verify_position_opened


async def process_signals(bot, cfg, api, tracker, alerter):
    """Read signals.json and open positions for new, unopened signals."""
    if bot._kill_switch_active:
        logging.warning("🚫 Kill switch active — process_signals() skipping new opens")
        return

    signals_path = Path(bot._signals_file)
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
    if opp_hash == bot._last_signal_hash:
        return
    bot._last_signal_hash = opp_hash
    bot._last_signal_timestamp = data.get("timestamp")
    bot._signal_processed_this_tick = True

    # Auto-detect balance and scale positions proportionally
    balance = await bot._get_balance()
    scanner_equity = data.get("config", {}).get("accountEquity", balance)
    if balance <= 0:
        logging.warning("⚠️ Zero or negative balance, cannot open positions")
        return
    scale = balance / scanner_equity
    if abs(scale - 1.0) > 0.01:
        logging.info(f"📐 Scaling positions: balance=${balance:.2f} / scanner_equity=${scanner_equity:.2f} = {scale:.4f}×")

    # HIGH-12: Write equity to shared state file for dashboard
    write_equity_file(bot, balance)

    for opp in data.get("opportunities", []):
        mid = opp["marketId"]
        symbol = opp["symbol"]
        direction = opp.get("direction", "long")

        # Filter by minimum score
        score = opp.get("compositeScore", 0)
        if score < bot._min_score:
            continue

        # Only open if safety checks passed
        if not opp.get("safetyPass", False):
            logging.debug(f"⚠️ {symbol}: safety check failed — {opp.get('safetyReason', 'unknown')}")
            continue

        # Cap concurrent positions from signals
        signal_positions = sum(1 for m in tracker.positions.keys() if m in bot._opened_signals)
        if signal_positions >= 3:
            logging.info(f"🛑 Max concurrent signal positions (3) reached, stopping")
            break

        # Skip if already have position in this market
        if mid in tracker.positions:
            logging.debug(f"⏭️ {symbol}: already have position, skipping")
            continue

        # Skip if AI recently closed this symbol (cooldown)
        cooldown_until = bot._ai_close_cooldown.get(symbol)
        if cooldown_until and time.monotonic() < cooldown_until:
            remaining = int(cooldown_until - time.monotonic())
            logging.info(f"🧊 {symbol}: AI close cooldown ({remaining}s remaining) - SKIPPING")
            continue

        # Skip if already acted on this signal this round
        if mid in bot._opened_signals:
            continue

        is_long = direction == "long"

        # Check pacing BEFORE fetching price (saves API calls)
        if should_pace_orders(bot):
            logging.debug(f"⏳ {symbol}: pacing orders (low quota), skipping open")
            continue

        # Quota prioritization: skip new opens when quota < 35 to preserve for SL orders
        if should_skip_open_for_quota(bot, api):
            logging.warning(f"🚫 {symbol}: new opens paused (quota={api.volume_quota_remaining} < 35, SL protection prioritized)")
            continue

        # Always fetch live price — signal data can be stale (up to 5 min old)
        current_price = None
        if api:
            current_price = await api.get_price(mid)
            await asyncio.sleep(cfg.price_call_delay)
        if not current_price:
            logging.warning(f"⚠️ {symbol}: no live price available, skipping")
            continue

        # Scale position size to actual balance
        size_usd = opp.get("positionSizeUsd", 0) * scale
        if size_usd > cfg.max_position_usd:
            size_usd = cfg.max_position_usd
            logging.info(f"📐 Capped position to ${cfg.max_position_usd:.2f}")
        if size_usd <= 0:
            logging.warning(f"⚠️ {symbol}: invalid position size, skipping")
            continue

        # Open the position
        logging.info(f"📡 Signal: {direction.upper()} {symbol} score={opp['compositeScore']} size=${size_usd:.2f}")
        if api:
            success = await api.open_position(mid, size_usd, is_long, current_price)
            if success:
                mark_order_submitted(bot)

                # Fix #13: Add to pending_sync and bot_managed BEFORE verification
                # to prevent race conditions if _tick() sync cycle runs mid-verification
                bot._pending_sync.add(mid)
                bot.bot_managed_market_ids.add(mid)

                # Verify position exists on exchange (BUG-03 fix)
                expected_size = size_usd / current_price
                verified_pos = await verify_position_opened(api, mid, expected_size, symbol)
                if verified_pos is None:
                    # CRITICAL-2: Don't discard — add as unverified so we can re-verify on next ticks
                    logging.error(f"❌ Signal open: {symbol} verification failed — tracking as unverified (will re-verify)")
                    tracker.add_position(mid, symbol, direction, current_price, expected_size, leverage=cfg.dsl_leverage)
                    pos = tracker.positions.get(mid)
                    if pos:
                        pos.unverified_at = time.time()
                        pos.unverified_ticks = 1
                    bot._save_state()
                    bot._opened_signals.add(mid)
                    await alerter.send(
                        f"⚠️ *POSITION UNVERIFIED*\n"
                        f"{direction.upper()} {symbol}\n"
                        f"Order submitted but verification failed.\n"
                        f"Will re-verify on next ticks."
                    )
                    continue  # Skip to next signal

                bot._opened_signals.add(mid)
                # Use actual filled size from exchange (handles partial fills)
                actual_size = verified_pos["size"]
                tracker.add_position(mid, symbol, direction, current_price, actual_size, leverage=verified_pos.get("leverage"))

                # NOTE: DSL uses config leverage from add_position, NOT exchange-reported leverage.
                # Exchange leverage can vary for cross margin and would break DSL tier calibration.

                # Persist state immediately after opening to prevent crash data loss
                bot._save_state()

                # BUG-06: Verify we can actually fetch price for this position after open
                price_ok = False
                for attempt in range(1, 4):
                    verify_price = await api.get_price_with_mark_fallback(mid)
                    if verify_price:
                        price_ok = True
                        if attempt > 1:
                            logging.info(f"✅ {symbol}: price verified on retry {attempt}/3 = ${verify_price:,.2f}")
                        break
                    if attempt < 3:
                        await asyncio.sleep(1)
                if not price_ok:
                    logging.error(f"❌ Signal open: {symbol} — no price after 3 attempts, removing orphaned position")
                    tracker.remove_position(mid)
                    await alerter.send(
                        f"❌ *SIGNAL OPEN FAILED*\n"
                        f"{direction.upper()} {symbol}\n"
                        f"Order filled but price unavailable — position removed.\n"
                        f"Order may need manual cleanup on exchange."
                    )
                    continue

                logging.info(f"📊 Quota remaining: {api.volume_quota_remaining}")
                await alerter.send(
                    f"📡 *SIGNAL → OPENED*\n"
                    f"{direction.upper()} {symbol}\n"
                    f"Score: {opp['compositeScore']}\n"
                    f"Price: ${current_price:,.2f}\n"
                    f"Size: ${size_usd:.2f} (scaled {scale:.2f}×)\n"
                    f"SL dist: {opp.get('stopLossDistancePct', 0):.2f}%"
                )
