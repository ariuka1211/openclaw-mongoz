"""System health endpoints — process checks, file freshness, alerts."""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

from dashboard.api.utils import time_ago, _db

log = logging.getLogger("dashboard.api.system")

PROJECT_ROOT = Path("/root/.openclaw/workspace/projects/autopilot-trader")
BOT_STATE_PATH = PROJECT_ROOT / "bot" / "state" / "bot_state.json"
SIGNALS_PATH = PROJECT_ROOT / "ipc" / "signals.json"

router = APIRouter()

_start_time = datetime.now(timezone.utc)


def _pgrep(pattern: str) -> tuple[bool, int | None]:
    """Check if a process is running. Returns (running, pid)."""
    try:
        r = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            pids = r.stdout.strip().split()
            return True, int(pids[0]) if pids else None
        return False, None
    except Exception:
        return False, None


def _file_mtime(path: Path) -> str | None:
    """Get file modification time as ISO string, or None if missing."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


@router.get("/api/system/health")
async def get_health():
    bot_running, bot_pid = _pgrep("bot.py")
    ai_running, ai_pid = _pgrep("ai_trader")
    scanner_running, scanner_pid = _pgrep("opportunity-scanner")

    bot_state_mtime = _file_mtime(BOT_STATE_PATH)
    signals_mtime = _file_mtime(SIGNALS_PATH)

    signals_stale = False
    try:
        with open(SIGNALS_PATH) as f:
            data = json.load(f)
        ts = data.get("timestamp")
        if ts:
            sig_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - sig_ts).total_seconds()
            signals_stale = age > 600
    except Exception:
        pass

    ai_last_cycle = None
    try:
        with open(PROJECT_ROOT / "ipc" / "ai-decision.json") as f:
            decision = json.load(f)
        ai_last_cycle = decision.get("timestamp")
    except Exception:
        pass

    # Read model from config (ai-decision.json doesn't have a model field)
    model = None
    try:
        with open(PROJECT_ROOT / "ai-decisions" / "config.json") as f:
            ai_config = json.load(f)
        model = ai_config.get("llm", {}).get("primary_model")
    except Exception:
        pass

    uptime_seconds = int((datetime.now(timezone.utc) - _start_time).total_seconds())

    return {
        "services": {
            "bot": {
                "running": bot_running,
                "pid": bot_pid,
                "last_state_update": bot_state_mtime,
                "last_state_ago": time_ago(bot_state_mtime),
            },
            "ai_trader": {
                "running": ai_running,
                "pid": ai_pid,
                "last_cycle": ai_last_cycle,
                "last_cycle_ago": time_ago(ai_last_cycle),
                "model": model,
            },
            "scanner": {
                "running": scanner_running,
                "pid": scanner_pid,
                "last_scan": signals_mtime,
                "last_scan_ago": time_ago(signals_mtime),
                "stale": signals_stale,
            },
        },
        "dashboard": {
            "uptime_seconds": uptime_seconds,
        },
        "port": 8080,
    }


@router.get("/api/system/errors")
async def get_errors(limit: int = 20):
    """Recent alerts from the DB (alias for alerts endpoint)."""
    if not _db:
        return []
    try:
        return _db.get_recent_alerts(limit=limit)
    except Exception as e:
        log.error(f"get_errors error: {e}")
        return []
