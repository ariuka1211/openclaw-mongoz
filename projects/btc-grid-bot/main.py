#!/usr/bin/env python3
"""
BTC Smart Grid Bot — Main Entry Point

Flow:
  1. Load config + env
  2. Run analyst → get grid levels
  3. Deploy grid via GridManager
  4. Monitor loop: check fills every 30s
  5. Telegram alerts for key events
  6. Graceful shutdown on Ctrl:C / SIGTERM
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import yaml
from dotenv import load_dotenv

from calculator import calculate_grid
from grid import GridManager
from lighter_api import LighterAPI
from tg_alerts import send_alert
from analyst import run_analyst, fetch_candles
from indicators import calc_ema_single, time_awareness_adjustment, funding_rate_adjustment, direction_score
from market_intel import gather_all_intel

load_dotenv()

log = logging.getLogger("btc-grid")

# Telegram bot command interface
COMMAND_FILE = Path("state/bot_command.json")


def check_telegram_commands(gm):
    """Check for commands written by telegram_bot.py. Returns command name or None."""
    if not COMMAND_FILE.exists():
        return None
    try:
        with open(COMMAND_FILE) as f:
            data = json.load(f)
        cmd = data.get("command")
        if cmd == "pause":
            reason = data.get("reason", "User requested pause")
            log.info(f"Telegram pause command: {reason}")
            gm.state["paused"] = True
            gm.state["pause_reason"] = reason
            gm._save_state()
            COMMAND_FILE.unlink()
            return "pause"
        elif cmd == "cancel_all":
            log.info("Telegram emergency cancel command")
            COMMAND_FILE.unlink()
            return "cancel_all"
        elif cmd == "resume":
            log.info("Telegram resume command received — will redeploy fresh grid")
            COMMAND_FILE.unlink()
            return "resume"
    except Exception as e:
        log.error(f"Error reading telegram command: {e}")
    return None


def load_config() -> dict:
    base_dir = Path(__file__).parent
    load_dotenv(Path("/root/.openclaw/workspace/.env"))
    with open(base_dir / "config.yml") as f:
        return yaml.safe_load(f)


def check_loss_lockout() -> str | None:
    """Check if we're in a loss lockout period. Returns reason or None."""
    lockout_file = Path("state/loss_lockout.json")
    if not lockout_file.exists():
        return None
    with open(lockout_file) as f:
        data = json.load(f)
    unlock_at = datetime.fromisoformat(data["unlock_at"])
    if datetime.now(timezone.utc) >= unlock_at:
        lockout_file.unlink()  # lockout expired, clean up
        return None
    return data["reason"]


def set_loss_lockout(reason: str):
    """Set a 24-hour loss lockout."""
    lockout_file = Path("state/loss_lockout.json")
    lockout_file.parent.mkdir(exist_ok=True)
    unlock_at = datetime.now(timezone.utc) + timedelta(hours=24)
    with open(lockout_file, "w") as f:
        json.dump({"unlock_at": unlock_at.isoformat(), "reason": reason}, f, indent=2)
    logging.warning(f"Loss lockout set until {unlock_at.isoformat()}")


async def handle_resume(gm, api, cfg):
    """Resume a paused grid without running fresh AI analysis.

    If valid levels exist in state, clear pause and redeploy.
    If state is empty (first run / crash), fall back to full startup.
    """
    try:
        equity = await api.get_equity()
        price = await api.get_btc_price()
    except Exception as e:
        await send_alert(f"⚠️ Resume failed — cannot fetch equity/price: {e}")
        return

    state = gm.state
    old_buy = state.get("levels", {}).get("buy", [])
    old_sell = state.get("levels", {}).get("sell", [])

    if not old_buy or not old_sell:
        log.info("No existing levels in state — running full AI analysis")
        await send_alert("🔄 No existing grid — running fresh AI analysis...")
        new_api, new_gm, new_levels = await startup(cfg)
        gm.state.update(new_gm.state)
        gm._save_state()
        return

    levels = {
        "buy_levels": old_buy,
        "sell_levels": old_sell,
        "range_low": state.get("range_low", min(old_buy)),
        "range_high": state.get("range_high", max(old_sell)),
    }

    num_buy = len(old_buy)
    num_sell = len(old_sell)

    # Compute ATR for volatility-adaptive sizing
    atr_pct = None
    try:
        candles_15m = await fetch_candles("15m", limit=100)
        from indicators import calc_atr
        atr_data = calc_atr(candles_15m, period=14)
        if atr_data.get("atr", 0) > 0 and price > 0:
            atr_pct = atr_data["atr"] / price
    except Exception:
        pass

    vol_cfg = cfg.get("volatility", {})

    # Auto-compounding multiplier
    compounding_mult = gm._compounding_factor()

    # Time-of-day adjustment
    time_adj = time_awareness_adjustment()["adj_multiplier"]

    # Funding rate adjustment
    funding_adj = 1.0
    try:
        market_intel = await gather_all_intel(cfg)
        funding_info = funding_rate_adjustment(market_intel, price)
        funding_adj = funding_info["adj_multiplier"]
    except Exception:
        pass  # Default to 1.0 if funding fetch fails

    # Read direction from state, default to long for backward compat
    direction = state.get("grid_direction", "long")

    calc = calculate_grid(
        equity, price, num_buy, num_sell,
        cfg["capital"]["max_exposure_multiplier"],
        cfg["capital"]["margin_reserve_pct"],
        atr_pct=atr_pct,
        vol_cfg=vol_cfg,
        compounding_mult=compounding_mult,
        time_adj=time_adj,
        funding_adj=funding_adj,
        direction=direction,
    )
    if not calc["safe"]:
        msg = f"❌ Safety check failed — cannot resume: {calc['reason']}"
        await send_alert(msg)
        return

    # Cancel old orders and redeploy same levels
    await gm.cancel_all()
    gm.state["paused"] = False
    gm.state["pause_reason"] = ""
    await gm.deploy(levels, equity, price, time_adj=time_adj, funding_adj=funding_adj, direction=direction)
    direction_label = direction.upper()
    await send_alert(f"🟢 Grid resumed ({direction_label} · same levels) · BTC @ ${price:,.0f}")


async def check_direction(cfg: dict, price: float) -> dict:
    """Multi-signal direction check using all available indicators.
    
    Returns a dict with:
      - "direction": "long" | "short" | "neutral"
      - "score": int (-100 to +100)
      - "confidence": "high" | "medium" | "low"
      - "recommendation": "deploy_long" | "deploy_short" | "pause" | "neutral_prefer_long"
      - "flags": list of safety override strings
    """
    # Fetch candles
    candles_15m = await fetch_candles("15m", limit=200)
    candles_30m = await fetch_candles("30m", limit=200)
    candles_4h = await fetch_candles("4H", limit=48)
    try:
        candles_1d = await fetch_candles("1D", limit=90)
    except Exception:
        candles_1d = []
    
    # Get market intel (funding, OI)
    from market_intel import gather_all_intel
    from indicators import gather_indicators
    
    market_intel = await gather_all_intel(cfg)
    indicators = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel, candles_1d)
    
    # Extract individual indicator components for direction_score
    score_result = direction_score(
        trend_skew=indicators["skew"],
        oi_div=indicators["oi_divergence"],
        adx_data=indicators["adx"],
        funding_rate=market_intel.get("current", {}).get("funding_rate", 0),
        volume_spike=indicators["volume_spike"],
        regime=indicators["regime"],
        ema_50_4h=indicators["ema_50_4h"],
        ema_50_1d=indicators["ema_50_1d"],
        ema_20_1d=indicators["ema_20_1d"],
        current_price=price,
    )
    
    # Log the result
    log.info(f"Direction check: score={score_result['score']}, "
             f"direction={score_result['direction']}, "
             f"recommendation={score_result['recommendation']}, "
             f"confidence={score_result['confidence']}")
    
    return score_result


async def startup(cfg: dict) -> tuple[LighterAPI, GridManager, dict]:
    api = LighterAPI(cfg)
    equity = await api.get_equity()
    price = await api.get_btc_price()
    log.info(f"Account equity: ${equity:.2f} | BTC: ${price:,.0f}")

    # Check for active loss lockout
    lockout_reason = check_loss_lockout()
    if lockout_reason:
        msg = f"🔒 Bot locked: {lockout_reason}"
        log.error(msg)
        await send_alert(msg)
        sys.exit(1)

    gm = GridManager(cfg, api)

    # ── 1. Check for actual BTC position ─────────────────────────
    try:
        btc_balance = await api.get_btc_balance()
    except Exception as e:
        log.error(f"Failed to check BTC balance: {e}")
        btc_balance = 0.0  # assume clean, proceed with caution

    # Any non-zero position (long or short) needs recovery — flatten it
    if btc_balance != 0:
        pos_value = abs(btc_balance) * price
        pos_pct = pos_value / equity if equity > 0 else 0
        side = "SHORT" if btc_balance < 0 else "LONG"

        log.warning(f"⚠️ Found BTC {side}.position: {btc_balance:.6f} (${pos_value:.2f} = {pos_pct:.1%} equity)")

        if pos_pct < 0.10:
            # Small position — incorporate into fresh grid
            await send_alert(
                f"📎 Small {side} position detected: {btc_balance:.6f} BTC (${pos_value:.2f}, {pos_pct:.1%} equity)\n"
                f"Building grid around position..."
            )
            # Compute funding adjustment
            funding_adj = 1.0
            try:
                market_intel = await gather_all_intel(cfg)
                funding_info = funding_rate_adjustment(market_intel, price)
                funding_adj = funding_info["adj_multiplier"]
            except Exception:
                pass
            result = await gm.deploy_with_position(btc_balance, price, equity, cfg, funding_adj=funding_adj)
            levels = {
                "buy_levels": result["buy_levels"],
                "sell_levels": result["sell_levels"],
                "range_low": min(result["buy_levels"]),
                "range_high": max(result["sell_levels"]),
            }
            return api, gm, levels
        else:
            # Medium or large position — close it
            await send_alert(f"⚠️ Found {side} position to close: {btc_balance:.6f} BTC = ${pos_value:.2f} ({pos_pct:.1%} equity)")
            result = await gm.recover_position(btc_balance, price, cfg)
            levels = {
                "buy_levels": result["buy_levels"],
                "sell_levels": result["sell_levels"],
                "range_low": min(result["buy_levels"]) if result["buy_levels"] else price,
                "range_high": max(result["sell_levels"]),
            }
            return api, gm, levels

    # ── 2. Check for existing orders ─────────────────────────────
    if await gm.adopt_existing_orders(price):
        levels = {
            "buy_levels": gm.state["levels"]["buy"],
            "sell_levels": gm.state["levels"]["sell"],
            "range_low": gm.state["range_low"],
            "range_high": gm.state["range_high"],
        }
        num_orders = len(gm.state["orders"])
        await send_alert(
            f"♻️ Resumed existing grid · {num_orders} orders adopted\n"
            f"Buy: {levels['buy_levels']}\n"
            f"Sell: {levels['sell_levels']}\n"
            f"Equity: ${equity:.2f}"
        )
        return api, gm, levels

    # ── 4. Sanity cleanup (always run) ───────────────────────────
    await gm.sanity_cleanup()

    # ── 3. Fresh deploy ──────────────────────────────────────────
    await send_alert("🔍 Running market analysis...")
    levels = await run_analyst(cfg, equity=equity, btc_price=price, grid_manager=gm)

    # Direction score check — after analyst, before capital check
    if not levels.get("pause"):
        direction_result = await check_direction(cfg, price)
        score = direction_result["score"]
        recommendation = direction_result["recommendation"]
        flags = direction_result.get("flags", [])
        
        # Resolve direction from AI analyst + direction score
        ai_direction = levels.get("direction", "long")
        
        # Safety overrides from flags
        pause_flags = [f for f in flags if "no_" in f or "pa" in f.lower()]
        
        if ai_direction == "pause" or recommendation == "pause":
            direction = "pause"
        elif ai_direction == "short" and "no_shorts_during_capitulation" not in flags and "no_shorts_during_squeeze" not in flags:
            # AI wants short + no flag preventing it
            direction = "short"
        elif ai_direction == "long":
            # AI wants long, but direction score might suggest otherwise
            direction = "long"
        else:
            # AI says nothing strong — defer to direction score
            if recommendation == "deploy_short":
                direction = "short"
            elif recommendation == "deploy_long":
                direction = "long"
            else:
                direction = "long"  # default
        
        # Log decision for transparency
        log.info(f"Direction resolution: AI={ai_direction}, Score={score}, Result={direction}")

        if direction == "pause":
            msg = f"⚠️ Direction override: {direction} — pausing deployment"
            log.error(msg)
            await send_alert(msg)
            sys.exit(1)
    else:
        direction = "long"  # won't be used anyway since paused
        score = 0
        direction_result = {"score": 0, "confidence": "low", "recommendation": "neutral_prefer_long", "flags": []}

    # Validate analyst output against config min/max
    num_buy = len(levels["buy_levels"])
    num_sell = len(levels["sell_levels"])
    min_levels = cfg["grid"].get("min_levels", 2)
    max_levels = cfg["grid"].get("max_levels", 8)
    if not levels.get("pause"):
        if num_buy < min_levels:
            levels["pause"] = True
            levels["pause_reason"] = f"Too few buy levels ({num_buy} < {min_levels} min)"
        elif num_sell < min_levels:
            levels["pause"] = True
            levels["pause_reason"] = f"Too few sell levels ({num_sell} < {min_levels} min)"
        elif num_buy + num_sell > max_levels:
            logging.warning(f"Grid levels exceed max ({num_buy + num_sell} > {max_levels}) — deploying anyway")

    if levels["pause"]:
        msg = f"⏸ Analyst paused: {levels['pause_reason']}"
        log.warning(msg)
        await send_alert(msg)
        sys.exit(1)

    # Compute ATR for volatility-adaptive sizing
    atr_pct = None
    try:
        candles_15m = await fetch_candles("15m", limit=100)
        from indicators import calc_atr
        atr_data = calc_atr(candles_15m, period=14)
        if atr_data.get("atr", 0) > 0 and price > 0:
            atr_pct = atr_data["atr"] / price
    except Exception:
        pass

    vol_cfg = cfg.get("volatility", {})

    # Auto-compounding multiplier
    compounding_mult = gm._compounding_factor()

    # Time-of-day adjustment
    time_adj = time_awareness_adjustment()["adj_multiplier"]

    # Funding rate adjustment
    funding_adj = 1.0
    try:
        market_intel = await gather_all_intel(cfg)
        funding_info = funding_rate_adjustment(market_intel, price)
        funding_adj = funding_info["adj_multiplier"]
    except Exception:
        pass  # Default to 1.0 if fetch fails

    # Safety-check against the REAL level count
    calc = calculate_grid(
        equity, price, num_buy, num_sell,
        cfg["capital"]["max_exposure_multiplier"],
        cfg["capital"]["margin_reserve_pct"],
        atr_pct=atr_pct,
        vol_cfg=vol_cfg,
        compounding_mult=compounding_mult,
        time_adj=time_adj,
        funding_adj=funding_adj,
        direction=direction,
    )
    if not calc["safe"]:
        msg = f"❌ Safety check failed: {calc['reason']}"
        log.error(msg)
        await send_alert(msg)
        sys.exit(1)

    # Deploy with direction
    await gm.deploy(levels, equity, price, time_adj=time_adj, funding_adj=funding_adj, direction=direction)

    open_count = sum(1 for o in gm.state["orders"] if o.get("status") == "open")
    direction_label = direction.upper()
    await send_alert(
        f"✅ Grid deployed ({direction_label}) · BTC @ ${price:,.0f}\n"
        f"Direction score: {score:+d} ({direction_result['confidence']})\n"
        f"Buy: {levels['buy_levels']}\n"
        f"Sell: {levels['sell_levels']}\n"
        f"Orders placed: {open_count} · Equity: ${equity:.2f}"
    )
    return api, gm, levels


async def run_loop(api: LighterAPI, gm: GridManager, levels: dict, cfg: dict):
    pnl_reported_today = False

    config_mtime = os.path.getmtime(Path(__file__).parent / "config.yml")

    while True:
        # ── Config hot-reload ────────────────────────────────────
        config_path = Path(__file__).parent / "config.yml"
        current_mtime = os.path.getmtime(config_path)
        if current_mtime != config_mtime:
            try:
                old_poll = cfg["grid"].get("poll_interval_seconds")
                with open(config_path) as f:
                    cfg = yaml.safe_load(f)
                new_poll = cfg["grid"].get("poll_interval_seconds")
                config_mtime = current_mtime
                log.info(f"Config reloaded · poll: {old_poll}s → {new_poll}s")
            except Exception as e:
                log.warning(f"Config hot-reload failed: {e} — keeping old config")

        poll_interval = cfg["grid"].get("poll_interval_seconds", 30)
        await asyncio.sleep(poll_interval)

        # Check for telegram bot commands (pause, cancel, resume)
        cmd = check_telegram_commands(gm)
        if cmd == "pause":
            log.warning("Bot paused by telegram command")
            await send_alert("⏸️ Grid paused via Telegram.")
            return  # exit loop (finally block cancels orders)
        elif cmd == "cancel_all":
            log.warning("Bot cancelled by telegram command")
            await send_alert("🚨 All orders cancelled via Telegram.")
            await gm.cancel_all()
            gm.state["paused"] = True
            gm.state["pause_reason"] = "Manual cancel"
            gm._save_state()
            return
        elif cmd == "resume":
            await handle_resume(gm, api, cfg)
            log.info("Grid resumed — continuing with existing levels")

        # Skip this cycle if resume redeployed (state changed mid-cycle)
        if cmd in ("resume",):
            continue

        try:
            price = await api.get_btc_price()
            await gm.check_fills(price)

            # Check if it's time for daily PnL report (23:30 UTC)
            now = datetime.now(timezone.utc)
            if now.hour == 23 and 30 <= now.minute <= 31 and not pnl_reported_today:
                try:
                    equity = await api.get_equity()
                    equity_at_reset = gm.state.get("equity_at_reset", equity)
                    realized_pnl = gm.state.get("realized_pnl", 0.0)
                    trades_count = len(gm.state.get("trades", []))
                    pending_buys = len(gm.state.get("pending_buys", []))
                    pnl_pct_str = f"{realized_pnl/equity_at_reset*100:.1f}%" if equity_at_reset > 0 else "0.0%"

                    compounding_mult = gm._compounding_factor()
                    trades = gm.state.get("trades", [])
                    if trades:
                        wins = sum(1 for t in trades if t.get("pnl", 0) >= 0)
                        win_rate = wins / len(trades) * 100
                        avg_pnl = sum(t.get("pnl", 0) for t in trades) / len(trades)
                    else:
                        win_rate = 0
                        avg_pnl = 0

                    win_emoji = "🎯" if win_rate > 0 else ""
                    comp_emoji = "📈" if compounding_mult >= 1.0 else "📉"
                    await send_alert(
                        (
                            f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                            f"Starting Equity: ${equity_at_reset:.2f}\n"
                            f"Current Equity: ${equity:.2f}\n"
                            f"Realized PnL: ${realized_pnl:.2f} ({pnl_pct_str}%)\n"
                            f"Completed trades: {trades_count} | Win rate: {win_emoji} {win_rate:.0f}%\n"
                            f"Avg PnL/trade: {avg_pnl:+.2f} USDC\n"
                            f"{comp_emoji} Compounding: {compounding_mult:.2f}x\n"
                            f"Pending buys: {pending_buys}\n"
                            f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                        )
                    )
                    pnl_reported_today = True
                except Exception as e:
                    logging.error(f"Failed to send daily PnL report: {e}")

            # Reset daily PnL flag at midnight UTC
            if now.hour == 0:
                pnl_reported_today = False

            # Trailing loss limit check (replaces static daily loss limit)
            if gm.state["active"] and not gm.state["paused"]:
                equity = await api.get_equity()
                equity_at_reset = gm.state.get("equity_at_reset", equity)
                peak_equity = max(gm.state.get("peak_equity", equity_at_reset), equity)
                gm.state["peak_equity"] = peak_equity

                trailing_loss_pct = cfg["risk"].get("trailing_loss_pct", 0.04)
                trailing_stop = peak_equity * (1 - trailing_loss_pct)
                static_floor = equity_at_reset * (1 - cfg["risk"]["daily_loss_limit_pct"])
                effective_stop = max(trailing_stop, static_floor)

                if equity < effective_stop:
                    # Trigger lockout
                    if trailing_stop > static_floor:
                        reason = f"Trailing loss hit: equity ${equity:.2f} dropped {((peak_equity - equity)/peak_equity)*100:.1f}% from peak ${peak_equity:.2f}. Stop was ${effective_stop:.2f}."
                    else:
                        loss_pct = (equity_at_reset - equity) / equity_at_reset
                        reason = f"Daily loss limit hit: equity ${equity:.2f} dropped {loss_pct:.1%} from reset"
                    set_loss_lockout(reason)
                    await gm._pause(reason)
                    await send_alert(f"🚨 LOSS LIMIT REACHED!\n{reason}\n🔒 Locked for 24h")
                    return

            # If paused (price left range), wait for manual restart
            if gm.state.get("paused"):
                log.info("Grid is paused. Stopping monitor loop.")
                await send_alert("⏸ Grid paused — restart bot for fresh analysis")
                return

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Monitor loop error: {e}")
            await asyncio.sleep(10)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config()
    api, gm, levels = await startup(cfg)

    try:
        await run_loop(api, gm, levels, cfg)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down — cancelling all orders...")
        try:
            await send_alert("🛑 Grid bot stopping")
        except Exception:
            pass
        try:
            await gm.cancel_all()
        except Exception:
            pass
        try:
            await api.close()
        except Exception:
            pass
        log.info("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass