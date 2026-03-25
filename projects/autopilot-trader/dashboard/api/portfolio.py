"""Portfolio endpoints — read bot_state.json, compute unrealized PnL."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

log = logging.getLogger("dashboard.api.portfolio")

PROJECT_ROOT = Path("/root/.openclaw/workspace/projects/autopilot-trader")
BOT_STATE_PATH = PROJECT_ROOT / "executor" / "state" / "bot_state.json"
SIGNALS_PATH = PROJECT_ROOT / "ipc" / "signals.json"

router = APIRouter()


def _read_json(path: Path) -> dict | list | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning(f"Failed to read {path}: {e}")
        return None


def _get_signals_price_map() -> dict:
    """Build symbol->lastPrice map from signals.json."""
    data = _read_json(SIGNALS_PATH)
    if not data:
        return {}
    return {
        opp["symbol"]: opp["lastPrice"]
        for opp in data.get("opportunities", [])
        if "symbol" in opp and "lastPrice" in opp
    }


def _compute_unrealized_pnl(position: dict, current_price: float) -> float:
    """Compute unrealized PnL in USD."""
    entry = position["entry_price"]
    size = position["size"]
    leverage = position.get("leverage", 1.0)
    size_usd = entry * size * leverage

    if entry == 0:
        return 0.0

    if position["side"] == "long":
        return (current_price - entry) / entry * size_usd
    else:  # short
        return (entry - current_price) / entry * size_usd


@router.get("/api/portfolio")
async def get_portfolio():
    state = _read_json(BOT_STATE_PATH)
    if not state:
        return {
            "positions": [],
            "total_exposure_usd": 0,
            "max_concurrent": 3,
        }

    positions_data = state.get("positions", {})
    price_map = _get_signals_price_map()

    positions = []
    total_exposure = 0.0

    for market_id, pos in positions_data.items():
        current_price = pos.get("high_water_mark") or pos.get("entry_price", 0)
        sig_price = price_map.get(pos.get("symbol"))
        if sig_price:
            current_price = sig_price

        entry = pos["entry_price"]
        size = pos["size"]
        leverage = pos.get("leverage", 1.0)
        size_usd = entry * size * leverage

        unrealized_pnl = _compute_unrealized_pnl(pos, current_price) if current_price else 0.0
        roe_pct = (unrealized_pnl / size_usd * 100) if size_usd > 0 else 0.0

        dsl = pos.get("dsl", {})
        positions.append({
            "market_id": pos.get("market_id"),
            "symbol": pos.get("symbol"),
            "side": pos["side"],
            "entry_price": entry,
            "current_price": current_price,
            "size": size,
            "size_usd": round(size_usd, 4),
            "leverage": leverage,
            "unrealized_pnl": round(unrealized_pnl, 6),
            "roe_pct": round(roe_pct, 4),
            "dsl": {
                "high_water_roe": dsl.get("high_water_roe"),
                "current_tier_trigger": dsl.get("current_tier_trigger"),
                "breach_count": dsl.get("breach_count", 0),
                "stagnation_active": dsl.get("stagnation_active", False),
            },
        })
        total_exposure += size_usd

    return {
        "positions": positions,
        "total_exposure_usd": round(total_exposure, 4),
        "max_concurrent": 3,
    }


@router.get("/api/portfolio/summary")
async def get_portfolio_summary():
    state = _read_json(BOT_STATE_PATH)
    signals = _read_json(SIGNALS_PATH)

    equity = 0.0
    if signals and "config" in signals:
        equity = signals["config"].get("accountEquity", 0)

    position_count = 0
    total_exposure = 0.0

    if state:
        positions_data = state.get("positions", {})
        position_count = len(positions_data)
        for pos in positions_data.values():
            entry = pos.get("entry_price", 0)
            size = pos.get("size", 0)
            leverage = pos.get("leverage", 1.0)
            total_exposure += entry * size * leverage

    max_concurrent = 3
    if signals and "config" in signals:
        max_concurrent = signals["config"].get("maxConcurrentPositions", 3)

    return {
        "equity": equity,
        "total_exposure_usd": round(total_exposure, 4),
        "position_count": position_count,
        "max_concurrent": max_concurrent,
    }
