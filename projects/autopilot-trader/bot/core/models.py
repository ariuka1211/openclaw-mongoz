"""
Shared data models for the bot.

TrackedPosition: per-position state (entry, DSL, trailing).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from dsl import DSLState


@dataclass
class TrackedPosition:
    market_id: int
    symbol: str
    side: str               # "long" or "short"
    entry_price: float
    size: float
    high_water_mark: float
    trailing_sl_activated: bool = False      # Has trailing SL been triggered (price moved past trigger)
    trailing_sl_level: float | None = None  # Trailing SL price level (ratchets)
    dsl_state: DSLState | None = None       # DSL state (when dsl_enabled)
    sl_pct: float | None = None             # per-position stop loss % (from AI), None = use config default
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # CRITICAL-2: Unverified position tracking — set when open_order succeeds but verification fails
    unverified_at: float | None = None      # time.time() when marked unverified
    unverified_ticks: int = 0               # consecutive ticks in unverified state
    active_sl_order_id: str | None = None   # MED-18: cancel API tracking

