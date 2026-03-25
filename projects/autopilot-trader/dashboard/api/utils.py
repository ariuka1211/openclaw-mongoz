"""Shared utilities for dashboard API modules."""

import json
from datetime import datetime, timezone
from pathlib import Path


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
