"""
Standalone utility functions extracted from SignalProcessor.

Pacing/quota helpers, market ID resolution, equity file writing,
fill price queries, and outcome logging.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from core.models import TrackedPosition

# AI Trader DB (for outcome logging)
_ai_trader_dir = os.environ.get("AI_TRADER_DIR", str(Path(__file__).resolve().parent.parent.parent / "ai-decisions"))
if _ai_trader_dir not in sys.path:
    sys.path.insert(0, _ai_trader_dir)
try:
    from db import DecisionDB
    _db = DecisionDB(f"{_ai_trader_dir}/state/trader.db")
except Exception:
    _db = None


def should_pace_orders(bot) -> bool:
    """Pace orders to leverage 15-second free tx window when quota is low."""
    if bot.api and bot.api.volume_quota_remaining is not None and bot.api.volume_quota_remaining < 35:
        time_since_last = time.time() - bot._last_order_time
        if time_since_last < 16:  # 16s to be safe
            logging.debug(f"⏱️ Pacing orders (quota={bot.api.volume_quota_remaining}, last_order={time_since_last:.1f}s ago)")
            return True
    return False


def should_skip_open_for_quota(bot, api) -> bool:
    """Skip new opens when quota is low to preserve it for SL orders."""
    quota = api.volume_quota_remaining if api else None
    if quota is not None and quota < 35:
        logging.debug(f"🚫 Skipping new opens (quota={quota} < 35, preserving for SL)")
        return True
    return False


def mark_order_submitted(bot):
    """Mark timestamp when order was submitted."""
    bot._last_order_time = time.time()


def resolve_market_id(bot, tracker, symbol: str) -> int | None:
    """Resolve symbol to market_id. Tries scanner signals first, then cached positions."""
    # Try from signals file
    signals_path = Path(bot._signals_file)
    if signals_path.exists():
        # MED-23: Check staleness before using signals for market ID resolution.
        # Stale signals may have wrong market IDs (scanner could have reassigned IDs).
        try:
            signals_age = time.time() - signals_path.stat().st_mtime
            if signals_age > 600:  # 10 minutes
                logging.warning(f"⚠️ resolve_market_id: signals.json stale (age={signals_age:.0f}s), skipping signal-based resolution for {symbol}")
                signals_path = None  # Skip to position tracker fallback
        except OSError:
            pass

        if signals_path and signals_path.exists():
            from ipc_utils import safe_read_json
            data = safe_read_json(signals_path)
            if data:
                for opp in data.get("opportunities", []):
                    if opp.get("symbol") == symbol:
                        return opp.get("marketId")

    # Try from position tracker (already-open positions)
    for mid, pos in tracker.positions.items():
        if pos.symbol == symbol:
            return mid

    return None


def write_equity_file(bot, balance: float):
    """HIGH-12: Write equity to shared state file for dashboard to read."""
    try:
        ai_trader_dir = os.environ.get("AI_TRADER_DIR", str(Path(__file__).resolve().parent.parent.parent / "ai-decisions"))
        equity_path = Path(ai_trader_dir) / "state" / "equity.json"
        equity_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(equity_path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"equity": balance, "timestamp": datetime.now(timezone.utc).isoformat()}, f)
        os.replace(tmp, str(equity_path))
    except Exception as e:
        logging.debug(f"Failed to write equity file: {e}")


async def get_fill_price(bot, cfg, market_id: int, client_order_index: str | None) -> float | None:
    """Query Lighter API for actual fill price of a closed order."""
    if not client_order_index:
        return None
    try:
        if not hasattr(bot, '_auth_manager'):
            from auth_helper import LighterAuthManager
            bot._auth_manager = LighterAuthManager(
                signer=bot.api._signer,
                account_index=cfg.account_index
            )
        auth = bot._auth_manager.get_auth_token()
        base = cfg.lighter_url.rstrip('/')
        url = f'{base}/api/v1/accountInactiveOrders?account_index={cfg.account_index}&limit=100&auth={auth}'
        # Reuse or create a session for fill price queries
        if not hasattr(bot, '_http_session') or bot._http_session.closed:
            bot._http_session = aiohttp.ClientSession()
        async with bot._http_session.get(url) as resp:
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


def log_outcome(pos: TrackedPosition, exit_price: float, exit_reason: str,
                cfg, tracker, estimated: bool = False):
    """Log a closed trade outcome to the AI trader journal DB.

    Called ONCE per close — either with actual fill price after verification
    succeeds, or with estimated=True as fallback after max verification retries.
    This ensures only one outcome row per close (no double-writing).

    PnL math (no double-counting of leverage):
      pnl_pct       = raw price movement % (not leveraged)
      size_usd      = notional position value at entry (size × entry_price)
      pnl_usd       = actual dollar P&L = notional × pnl_pct / 100
                      (this IS the real dollar gain/loss, leverage doesn't change it —
                       1 BTC moved $100 is $100 whether you used 1x or 10x margin)
      price_move_pct = raw price movement % (alias for pnl_pct, used in new API)
      roe_pct       = Return on Equity % = pnl_pct × leverage (kept for history)
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
        # price_move_pct = raw price movement % (used in new API, no leverage multiply)
        price_move_pct = pnl_pct
        # roe_pct = pnl_pct × leverage (kept for historical data)
        leverage = pos.dsl_state.leverage if pos.dsl_state else cfg.dsl_leverage
        roe_pct = pnl_pct * leverage

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
            "price_move_pct": price_move_pct,
            "hold_time_seconds": hold_seconds,
            "max_drawdown_pct": 0,  # not tracked yet
            "exit_reason": reason_tag,
            "decision_snapshot": {},
        })
        tag = " (est)" if estimated else ""
        logging.info(
            f"📝 Outcome logged{tag}: {pos.side} {pos.symbol} "
            f"PnL=${pnl_usd:+.2f} ({price_move_pct:+.2f}% move) "
            f"held={hold_seconds}s reason={exit_reason}"
        )
    except Exception as e:
        logging.warning(f"Failed to log outcome: {e}")
