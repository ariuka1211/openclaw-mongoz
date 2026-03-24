"""
FastAPI dashboard for AI Trader.
Vanilla HTML/JS frontend, no frameworks.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from db import DecisionDB

log = logging.getLogger("ai-trader.dashboard")

app = FastAPI(title="AI Trader Dashboard")

# Will be initialized on startup
_db: DecisionDB | None = None
_start_time: float = 0
_last_cycle_time: float = 0
_model_name: str = ""
_equity: float = 0  # HIGH-12: Set by bot via set_equity() — no more hardcoded 1000
_llm_stats_ref = None  # Reference to LLMStats instance for token/cost display


def init_dashboard(db: DecisionDB, model_name: str = "", llm_stats=None):
    global _db, _start_time, _last_cycle_time, _model_name, _llm_stats_ref
    _db = db
    _start_time = datetime.now(timezone.utc).timestamp()
    _model_name = model_name
    _llm_stats_ref = llm_stats


def set_equity(equity: float):
    """Called by the bot to update actual equity for dashboard metrics."""
    global _equity
    _equity = equity


def set_cycle_time(ts: float | None = None):
    """Called by the AI trader each cycle to update last_cycle timestamp."""
    global _last_cycle_time
    _last_cycle_time = ts or datetime.now(timezone.utc).timestamp()


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>AI Trader Dashboard</h1><p>index.html not found</p>")


@app.get("/api/status")
async def api_status():
    stats = _db.get_performance_stats() if _db else {}
    # HIGH-12: Read equity from shared state file if not set via setter
    equity = _equity
    if equity <= 0:
        try:
            equity_file = Path(__file__).parent / "state" / "equity.json"
            if equity_file.exists():
                data = json.loads(equity_file.read_text())
                equity = data.get("equity", 0)
        except Exception:
            pass

    # LLM token/cost stats
    llm_stats = {}
    if _llm_stats_ref:
        llm_stats = _llm_stats_ref.to_dict()

    return {
        "alive": True,
        "uptime_seconds": int(datetime.now(timezone.utc).timestamp() - _start_time),
        "last_cycle": _last_cycle_time,
        "model": _model_name,
        "equity": equity,
        "llm": llm_stats,
        **stats,
    }


@app.get("/api/decisions")
async def api_decisions(n: int = 50):
    if not _db:
        return []
    return _db.get_recent_decisions(n)


@app.get("/api/performance")
async def api_performance():
    if not _db:
        return {}
    return _db.get_performance_stats()


@app.get("/api/alerts")
async def api_alerts(limit: int = 20):
    if not _db:
        return []
    return _db.get_recent_alerts(limit)
