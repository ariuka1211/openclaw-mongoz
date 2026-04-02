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
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import yaml
from dotenv import load_dotenv

from calculator import calculate_grid
from grid import GridManager
from lighter_api import LighterAPI
from telegram import send_alert
from analyst import run_analyst

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


async def startup(cfg: dict) -> tuple[LighterAPI, GridManager, dict]:
    api = LighterAPI(cfg)
    equity = await api.get_equity()
    price = await api.get_btc_price()
    log.info(f"Account equity: ${equity:.2f} | BTC: ${price:,.0f}")

    # Check for active loss lockout (added for Fix #11)
    lockout_reason = check_loss_lockout()
    if lockout_reason:
        msg = f"🔒 Bot locked: {lockout_reason}"
        log.error(msg)
        await send_alert(msg)
        sys.exit(1)

    await send_alert("🔍 Running market analysis...")
    levels = await run_analyst(cfg)

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

    # Safety-check against the REAL level count
    calc = calculate_grid(
        equity, price, num_buy, num_sell,
        cfg["capital"]["max_exposure_multiplier"],
        cfg["capital"]["margin_reserve_pct"],
    )
    if not calc["safe"]:
        msg = f"❌ Safety check failed: {calc['reason']}"
        log.error(msg)
        await send_alert(msg)
        sys.exit(1)

    # Deploy
    gm = GridManager(cfg, api)
    await gm.deploy(levels, equity, price)

    open_count = sum(1 for o in gm.state["orders"] if o.get("status") == "open")
    await send_alert(
        f"✅ Grid deployed · BTC @ ${price:,.0f}\n"
        f"Buy: {levels['buy_levels']}\n"
        f"Sell: {levels['sell_levels']}\n"
        f"Orders placed: {open_count} · Equity: ${equity:.2f}"
    )
    return api, gm, levels


async def run_loop(api: LighterAPI, gm: GridManager, levels: dict, cfg: dict):
    poll_interval = cfg["grid"]["poll_interval_seconds"]
    pnl_reported_today = False

    while True:
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
            log.info("Restarting bot for fresh grid deployment...")
            await send_alert("🟢 Restarting with fresh grid analysis...")
            await gm.cancel_all()
            await api.close()
            api, gm, levels = await startup(cfg)
            # Continue with new grid in the loop below

        try:
            price = await api.get_btc_price()
            await gm.check_fills(price)

            # Check if it's time for daily PnL report (23:30 UTC)
            now = datetime.now(timezone.utc)
            if now.hour == 23 and 30 <= now.minute <= 31 and not pnl_reported_today:
                try:
                    equity = await api.get_equity()
                    equity_at_reset = gm.state.get("equity_at_reset", equity)
                    pnl = equity - equity_at_reset
                    await send_alert(
                        (
                            f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                            f"Starting Equity: ${equity_at_reset:.2f}\n"
                            f"Current Equity: ${equity:.2f}\n"
                            f"Daily PnL: ${pnl:.2f} ({pnl/equity_at_reset*100:.1f}%)\n"
                            f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                        )
                    )
                    pnl_reported_today = True
                except Exception as e:
                    logging.error(f"Failed to send daily PnL report: {e}")

            # Reset daily PnL flag at midnight UTC
            if now.hour == 0:
                pnl_reported_today = False

            # Check daily loss limit (8% drop from equity at reset)
            if gm.state["active"] and not gm.state["paused"]:
                equity = await api.get_equity()
                equity_at_reset = gm.state.get("equity_at_reset", equity)
                loss_pct = (equity_at_reset - equity) / equity_at_reset
                if loss_pct > cfg["risk"]["daily_loss_limit_pct"]:
                    loss_reason = f"Daily loss limit hit: {loss_pct:.1%} drop from reset equity"
                    set_loss_lockout(loss_reason)
                    await gm._pause(loss_reason)
                    await send_alert(
                        f"🚨 Daily loss limit reached!\n"
                        f"Equity dropped {loss_pct:.1%} from reset.\n"
                        f"Starting: ${equity_at_reset:.2f} → Now: ${equity:.2f}\n"
                        f"🔒 Locked for 24h — delete state/loss_lockout.json to override"
                    )
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