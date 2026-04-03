#!/usr/bin/env python3
"""
BTC Grid Bot — Interactive Telegram Bot

Provides /start, /status, /pnl, /pause, /resume, /cancel commands
for managing the grid bot from Telegram.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import yaml

# ── Fix: prevent local telegram.py from shadowing python-telegram-bot ──
import sys, importlib
_sys_path = sys.path.copy()
# Remove the script directory so 'import telegram' finds the package, not local file
script_dir = str(Path(__file__).parent)
sys.path = [p for p in sys.path if p != script_dir]
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
sys.path = _sys_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("grid-telegram-bot")

# Path resolution
BASE_DIR = Path(__file__).parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.yml"
STATE_FILE = STATE_DIR / "grid_state.json"
COMMAND_FILE = STATE_DIR / "bot_command.json"
RESUME_FILE = STATE_DIR / "resume_signal.json"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_grid_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"active": False, "paused": False}


def write_command(command: str, reason: str = None):
    STATE_DIR.mkdir(exist_ok=True)
    data = {"command": command}
    if reason:
        data["reason"] = reason
    with open(COMMAND_FILE, "w") as f:
        json.dump(data, f)


def clear_command():
    if COMMAND_FILE.exists():
        COMMAND_FILE.unlink()


def format_status(state: dict) -> str:
    if not state.get("active") and not state.get("paused"):
        return "🔴 Grid Bot — Not deployed\n\nUse `/resume` to start or wait for manual deployment."

    status_emoji = "🟢" if not state.get("paused") else "⏸️"
    status_text = "ACTIVE" if not state.get("paused") else f"PAUSED ({state.get('pause_reason', 'no reason')})"

    range_low = state.get("range_low", 0)
    range_high = state.get("range_high", 0)
    open_orders = len([o for o in state.get("orders", []) if o.get("status") == "open"])
    fills = state.get("fill_count", 0)
    pnl = state.get("daily_pnl", 0.0)
    equity_reset = state.get("equity_at_reset", 0.0)
    rolls = state.get("roll_count", 0)
    last_reset = state.get("last_reset", "never")

    pnl_emoji = "📈" if pnl >= 0 else "📉"
    pnl_pct = f"({pnl/equity_reset*100:.1f}%)" if equity_reset > 0 else ""

    lines = [
        f"{status_emoji} Grid Bot — {status_text}",
        "",
        f"Range: ${range_low:,.0f} – ${range_high:,.0f}",
        f"Open orders: {open_orders}",
        f"Fills: {fills}",
        f"{pnl_emoji} Daily PnL: ${pnl:.2f} {pnl_pct}",
        f"Rolls: {rolls}",
        f"Last reset: {last_reset}",
    ]
    return "\n".join(lines)


def format_pnl(state: dict) -> str:
    equity_reset = state.get("equity_at_reset", 0.0)
    realized_pnl = state.get("realized_pnl", 0.0)
    equity_pnl = state.get("equity_pnl", 0.0)
    trades = state.get("trades", [])
    fills = state.get("fill_count", 0)
    rolls = state.get("roll_count", 0)
    size = state.get("size_per_level", 0)
    pending = len(state.get("pending_buys", []))

    pnl_emoji = "📈" if realized_pnl >= 0 else "📉"

    lines = [
        "📊 PnL Report",
        "",
        f"Equity at reset: ${equity_reset:.2f}",
        f"Realized PnL: {pnl_emoji} ${realized_pnl:.2f}",
        f"Equity delta: {pnl_emoji} ${equity_pnl:.2f}",
        f"Fill count: {fills}",
        f"Completed trades: {len(trades)}",
        f"Pending buy closes: {pending}",
        f"Roll count: {rolls}",
        f"Size per level: {size:.6f} BTC",
    ]

    if trades:
        last_trades = trades[-3:]  # show last 3
        lines.append("")
        lines.append("Last trades:")
        for t in last_trades:
            emoji = "✅" if t.get("pnl", 0) >= 0 else "❌"
            lines.append(f"  {emoji} Buy ${t['buy_price']:,.0f} → Sell ${t['sell_price']:,.0f} | PnL ${t['pnl']:.2f}")

    if rolls > 0:
        last_roll = state.get("last_roll", "never")
        lines.append(f"Last roll: {last_roll}")

    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🦊 <b>BTC Grid Bot — Control Panel</b>\n\n"
        "Available commands:\n"
        "/status — Current bot state\n"
        "/pnl — PnL details\n"
        "/pause [reason] — Pause the grid\n"
        "/resume — Resume paused grid\n"
        "/cancel — Emergency: cancel all orders"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_grid_state()
    msg = format_status(state)
    await update.message.reply_text(msg)


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_grid_state()
    msg = format_pnl(state)
    await update.message.reply_text(msg)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = " ".join(context.args) if context.args else "User requested pause"
    write_command("pause", reason)
    await update.message.reply_text(f"⏸️ Pause signal sent.\nReason: {reason}\n\nThe bot will pause within the next poll cycle (30s).")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_command()
    STATE_DIR.mkdir(exist_ok=True)
    with open(COMMAND_FILE, "w") as f:
        json.dump({"command": "resume"}, f)
    await update.message.reply_text("🟢 Resume signal sent.\nThe bot will resume on next poll cycle (30s).")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    write_command("cancel_all")
    await update.message.reply_text("🚨 EMERGENCY CANCEL\nAll orders will be cancelled within 30s.")


def main():
    cfg = load_config()
    tg_cfg = cfg.get("telegram", {})
    bot_token = tg_cfg.get("bot_token")

    if not bot_token:
        print("ERROR: telegram.bot_token not set in config.yml")
        return

    app = Application.builder().token(bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    print("🦊 Telegram bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
