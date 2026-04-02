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


def load_config() -> dict:
    base_dir = Path(__file__).parent
    load_dotenv(Path("/root/.openclaw/workspace/.env"))
    with open(base_dir / "config.yml") as f:
        return yaml.safe_load(f)


async def startup(cfg: dict) -> tuple[LighterAPI, GridManager, dict]:
    api = LighterAPI(cfg)
    equity = await api.get_equity()
    price = await api.get_btc_price()
    log.info(f"Account equity: ${equity:.2f} | BTC: ${price:,.0f}")

    # Safety check before deploying anything
    calc = calculate_grid(
        equity, price,
        cfg["grid"].get("num_buy_levels", 4),
        cfg["grid"].get("num_sell_levels", 4),
        cfg["capital"]["max_exposure_multiplier"],
        cfg["capital"]["margin_reserve_pct"],
    )
    if not calc["safe"]:
        msg = f"❌ Safety check failed: {calc['reason']}"
        log.error(msg)
        await send_alert(msg)
        sys.exit(1)

    await send_alert("🔍 Running market analysis...")
    levels = await run_analyst(cfg)

    if levels["pause"]:
        msg = f"⏸ Analyst paused: {levels['pause_reason']}"
        log.warning(msg)
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

    while True:
        await asyncio.sleep(poll_interval)

        try:
            price = await api.get_btc_price()
            await gm.check_fills(price)

            # Check if it's time for daily PnL report (23:30 UTC)
            now = datetime.now(timezone.utc)
            if now.hour == 23 and now.minute == 30:
                try:
                    equity = await api.get_equity()
                    pnl = equity - cfg["capital"]["starting_equity"]
                    await send_alert(
                        (
                            f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                            f"Starting Equity: ${cfg['capital']['starting_equity']:.2f}\n"
                            f"Current Equity: ${equity:.2f}\n"
                            f"Daily PnL: ${pnl:.2f} ({pnl/cfg['capital']['starting_equity']*100:.1f}%)\n"
                            f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                        )
                    )
                    # Wait until the next day to avoid duplicate reports
                    tomorrow = now.replace(hour=23, minute=30, second=0, microsecond=0) + timedelta(days=1)
                    await asyncio.sleep((tomorrow - now).total_seconds())
                except Exception as e:
                    logging.error(f"Failed to send daily PnL report: {e}")

            # Check daily loss limit (8% drop from equity at reset)
            if gm.state["active"] and not gm.state["paused"]:
                equity = await api.get_equity()
                equity_at_reset = gm.state.get("equity_at_reset", equity)
                loss_pct = (equity_at_reset - equity) / equity_at_reset
                if loss_pct > cfg["risk"]["daily_loss_limit_pct"]:
                    await gm._pause(f"Daily loss limit hit: {loss_pct:.1%} drop from reset equity")
                    await send_alert(
                        f"🚨 Daily loss limit reached!\n"
                        f"Equity dropped {loss_pct:.1%} from reset.\n"
                        f"Starting: ${equity_at_reset:.2f} → Now: ${equity:.2f}"
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