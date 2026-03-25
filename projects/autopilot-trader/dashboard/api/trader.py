"""AI Trader + Performance endpoints — queries via DecisionDB."""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query

log = logging.getLogger("dashboard.api.trader")

PROJECT_ROOT = Path("/root/.openclaw/workspace/projects/autopilot-trader")
TRADER_DB_PATH = PROJECT_ROOT / "ai-decisions" / "state" / "trader.db"
AI_DECISION_PATH = PROJECT_ROOT / "ipc" / "ai-decision.json"
AI_RESULT_PATH = PROJECT_ROOT / "ipc" / "ai-result.json"
BOT_STATE_PATH = PROJECT_ROOT / "bot" / "state" / "bot_state.json"

# Import DecisionDB from ai-decisions
sys.path.insert(0, str(PROJECT_ROOT / "ai-decisions"))
try:
    from db import DecisionDB
    _db = DecisionDB(str(TRADER_DB_PATH))
except Exception as e:
    log.error(f"Failed to load DecisionDB: {e}")
    _db = None

router = APIRouter()


def _read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _time_ago(iso_str: str | None) -> str | None:
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        return f"{hours}h {minutes % 60}m ago"
    except Exception:
        return iso_str


@router.get("/api/trader/status")
async def get_trader_status():
    decision = _read_json(AI_DECISION_PATH)
    state = _read_json(BOT_STATE_PATH)

    equity = None
    if state:
        signals = _read_json(PROJECT_ROOT / "ipc" / "signals.json")
        if signals and "config" in signals:
            equity = signals["config"].get("accountEquity")

    last_cycle_ts = decision.get("timestamp") if decision else None
    model = None
    # Read model from ai-trader config (not from decision file)
    ai_config = _read_json(PROJECT_ROOT / "ai-decisions" / "config.json")
    if ai_config and "llm" in ai_config:
        model = ai_config["llm"].get("primary_model")

    alive = False
    import subprocess
    try:
        r = subprocess.run(["pgrep", "-f", "ai_trader"], capture_output=True, text=True, timeout=3)
        alive = r.returncode == 0
    except Exception:
        pass

    return {
        "alive": alive,
        "last_cycle": last_cycle_ts,
        "last_cycle_ago": _time_ago(last_cycle_ts),
        "model": model,
        "equity": equity,
    }


@router.get("/api/trader/decisions")
async def get_decisions(n: int = Query(default=50, ge=1, le=200)):
    if not _db:
        return []
    try:
        return _db.get_recent_decisions(limit=n)
    except Exception as e:
        log.error(f"get_decisions error: {e}")
        return []


@router.get("/api/trader/performance")
async def get_performance():
    if not _db:
        return {"error": "db unavailable"}
    try:
        return _db.get_performance_stats()
    except Exception as e:
        log.error(f"get_performance error: {e}")
        return {"error": str(e)}


@router.get("/api/trader/alerts")
async def get_alerts(limit: int = Query(default=20, ge=1, le=100)):
    if not _db:
        return []
    try:
        return _db.get_recent_alerts(limit=limit)
    except Exception as e:
        log.error(f"get_alerts error: {e}")
        return []


@router.get("/api/trader/equity-curve")
async def get_equity_curve():
    """Cumulative PnL from outcomes table, ordered by timestamp."""
    if not _db:
        return []
    try:
        conn = _db.conn
        rows = conn.execute(
            "SELECT timestamp, symbol, pnl_usd FROM outcomes ORDER BY id ASC"
        ).fetchall()
        cumulative = 0.0
        points = []
        for r in rows:
            cumulative += (r[2] or 0)
            points.append({
                "timestamp": r[0],
                "symbol": r[1],
                "trade_pnl": r[2],
                "cumulative_pnl": round(cumulative, 6),
            })
        return points
    except Exception as e:
        log.error(f"get_equity_curve error: {e}")
        return []


@router.get("/api/trader/confidence-stats")
async def get_confidence_stats():
    """Confidence calibration data: per-trade confidence vs outcome."""
    if not _db:
        return {"error": "db unavailable"}
    try:
        conn = _db.conn
        # Join outcomes with decisions by symbol + nearest prior open decision
        # (outcomes.cycle_id is often NULL, so we match by symbol + timestamp)
        trades = conn.execute(
            """SELECT o.symbol, o.pnl_usd, o.exit_reason, o.timestamp,
                      (SELECT d.confidence FROM decisions d
                       WHERE d.symbol = o.symbol
                         AND d.action = 'open'
                         AND d.timestamp <= o.timestamp
                       ORDER BY d.timestamp DESC LIMIT 1) as confidence
               FROM outcomes o
               ORDER BY o.id ASC"""
        ).fetchall()
        scatter = []
        for r in trades:
            conf = r[4]  # confidence from subquery
            if conf is None:
                continue
            pnl = r[1] or 0
            scatter.append({
                "confidence": round(conf, 2),
                "pnl": round(pnl, 6),
                "win": pnl > 0,
                "symbol": r[0],
                "exit_reason": r[2],
                "timestamp": r[3],
            })

        # Bracket stats (keep for summary)
        brackets_raw = _db.get_confidence_bracket_stats()
        for b in brackets_raw.get("confidence_stats", []):
            total = b.get("total", 0)
            wins = b.get("wins", 0)
            b["win_rate"] = round(wins / total * 100, 1) if total > 0 else 0
            b["trades"] = total

        return {
            "scatter": scatter,
            "brackets": brackets_raw.get("confidence_stats", []),
        }
    except Exception as e:
        log.error(f"get_confidence_stats error: {e}")
        return {"error": str(e)}


@router.get("/api/trader/per-symbol")
async def get_per_symbol():
    """PnL grouped by symbol."""
    if not _db:
        return []
    try:
        conn = _db.conn
        rows = conn.execute(
            """SELECT symbol,
                      COUNT(*) as trades,
                      COALESCE(SUM(pnl_usd), 0) as total_pnl,
                      COALESCE(AVG(pnl_usd), 0) as avg_pnl,
                      COALESCE(AVG(hold_time_seconds), 0) as avg_hold
               FROM outcomes
               GROUP BY symbol
               ORDER BY total_pnl DESC"""
        ).fetchall()
        return [
            {
                "symbol": r[0],
                "trades": r[1],
                "total_pnl": round(r[2], 6),
                "avg_pnl": round(r[3], 6),
                "avg_hold_seconds": round(r[4], 1),
            }
            for r in rows
        ]
    except Exception as e:
        log.error(f"get_per_symbol error: {e}")
        return []


@router.get("/api/trader/by-exit-reason")
async def get_by_exit_reason():
    """PnL grouped by exit_reason."""
    if not _db:
        return []
    try:
        conn = _db.conn
        rows = conn.execute(
            """SELECT exit_reason,
                      COUNT(*) as trades,
                      COALESCE(SUM(pnl_usd), 0) as total_pnl,
                      COALESCE(AVG(pnl_usd), 0) as avg_pnl
               FROM outcomes
               GROUP BY exit_reason
               ORDER BY total_pnl DESC"""
        ).fetchall()
        return [
            {
                "exit_reason": r[0],
                "trades": r[1],
                "total_pnl": round(r[2], 6),
                "avg_pnl": round(r[3], 6),
            }
            for r in rows
        ]
    except Exception as e:
        log.error(f"get_by_exit_reason error: {e}")
        return []
