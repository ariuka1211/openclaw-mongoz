"""Unified Dashboard — FastAPI backend.

Replaces the old dashboard with modular API routers for portfolio,
trader, scanner, and system health endpoints.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from signals.dashboard.api import portfolio, trader, scanner, system

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("dashboard")

app = FastAPI(title="Autopilot Trader — Unified Dashboard", version="2.0.0")

# CORS for dev convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(portfolio.router)
app.include_router(trader.router)
app.include_router(scanner.router)
app.include_router(system.router)

DASHBOARD_DIR = Path(__file__).parent

# Serve static JS files
app.mount("/js", StaticFiles(directory=str(DASHBOARD_DIR / "js")), name="js")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the unified dashboard frontend."""
    html_path = DASHBOARD_DIR / "index.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>Dashboard</h1><p>index.html not found</p>")


if __name__ == "__main__":
    import uvicorn
    log.info("Starting unified dashboard on port 8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
