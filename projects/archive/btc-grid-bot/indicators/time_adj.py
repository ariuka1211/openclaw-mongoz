"""Time-of-day awareness adjustment."""

from datetime import datetime, timezone
from typing import Dict, Optional

# Session schedule for time-of-day awareness
_TIME_SESSIONS = [
    (0, 5, 0.7, "Late Asian (quiet hours, reduce size 30%)"),
    (6, 7, 0.85, "Asian/London transition"),
    (8, 11, 1.0, "London session (normal sizing)"),
    (12, 15, 1.15, "London afternoon (increased activity)"),
    (16, 19, 1.2, "NY/London overlap (high volume, widen grid)"),
    (20, 23, 1.0, "NY close (normal sizing)"),
]


def time_awareness_adjustment(current_time_utc: datetime | None = None) -> dict:
    """Return time-of-day volatility multiplier based on trading session.

    Returns:
        {"adj_multiplier": float, "session_label": str, "description": str}
    """
    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)

    hour = current_time_utc.hour

    # Find matching session
    for start, end, mult, desc in _TIME_SESSIONS:
        if start <= hour <= end:
            return {
                "adj_multiplier": mult,
                "session_label": desc,
                "description": desc,
            }

    # Fallback (shouldn't happen with proper session coverage)
    return {
        "adj_multiplier": 1.0,
        "session_label": "Unknown session",
        "description": "Unknown trading session (default sizing)",
    }
