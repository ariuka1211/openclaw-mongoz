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


def init_dashboard(db: DecisionDB, model_name: str = ""):
    global _db, _start_time, _last_cycle_time, _model_name
    _db = db
    _start_time = datetime.now(timezone.utc).timestamp()
    _model_name = model_name


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>AI Trader Dashboard</h1><p>index.html not found</p>")


@app.get("/api/status")
async def api_status():
    stats = _db.get_performance_stats() if _db else {}
    return {
        "alive": True,
        "uptime_seconds": int(datetime.now(timezone.utc).timestamp() - _start_time),
        "last_cycle": _last_cycle_time,
        "model": _model_name,
        "equity": 1000,  # Will be updated from signals
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
