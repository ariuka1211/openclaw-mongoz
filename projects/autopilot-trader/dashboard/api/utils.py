"""Shared utilities for dashboard API modules."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path("/root/.openclaw/workspace/projects/autopilot-trader")
TRADER_DB_PATH = PROJECT_ROOT / "ai-decisions" / "state" / "trader.db"

# Add ai-decisions to path for DecisionDB import
sys.path.insert(0, str(PROJECT_ROOT / "ai-decisions"))
try:
    from db import DecisionDB
    _db = DecisionDB(str(TRADER_DB_PATH))
except Exception:
    _db = None


def read_json(path: Path) -> dict | list | None:
    """Read and parse a JSON file. Returns None on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def time_ago(iso_str: str | None) -> str | None:
    """Convert ISO timestamp to human-readable 'X ago' string."""
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago" if seconds >= 10 else "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        return f"{hours}h {minutes % 60}m ago"
    except Exception:
        return iso_str
