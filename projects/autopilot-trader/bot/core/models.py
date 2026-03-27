"""
Shared data models for the bot.

TrackedPosition: per-position state (entry, DSL, trailing).
BotState: shared mutable state passed between bot, SignalProcessor, and StateManager.
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
    trailing_active: bool = False            # DEPRECATED: trailing TP concept gone, use trailing_sl_activated
    trailing_sl_activated: bool = False      # Has trailing SL been triggered (price moved past trigger)
    trailing_sl_level: float | None = None  # Trailing SL price level (ratchets)
    dsl_state: DSLState | None = None       # DSL state (when dsl_enabled)
    sl_pct: float | None = None             # per-position stop loss % (from AI), None = use config default
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # CRITICAL-2: Unverified position tracking — set when open_order succeeds but verification fails
    unverified_at: float | None = None      # time.time() when marked unverified
    unverified_ticks: int = 0               # consecutive ticks in unverified state
    active_sl_order_id: str | None = None   # MED-18: cancel API tracking


@dataclass
class BotState:
    # Signal tracking
    opened_signals: set[int] = field(default_factory=set)
    last_signal_timestamp: str | None = None
    last_signal_hash: str | None = None
    signal_processed_this_tick: bool = False

    # AI decision tracking
    last_ai_decision_ts: str | None = None
    result_dirty: bool = False

    # Close tracking
    recently_closed: dict[int, float] = field(default_factory=dict)
    close_attempts: dict[str, int] = field(default_factory=dict)
    close_attempt_cooldown: dict[str, float] = field(default_factory=dict)
    dsl_close_attempts: dict[str, int] = field(default_factory=dict)
    dsl_close_attempt_cooldown: dict[str, float] = field(default_factory=dict)
    ai_close_cooldown: dict[str, float] = field(default_factory=dict)

    # Position management
    bot_managed_market_ids: set[int] = field(default_factory=set)
    pending_sync: set[int] = field(default_factory=set)
    verifying_close: set[int] = field(default_factory=set)

    # Quota/order pacing (only used by bot.py, but included for save/load)
    last_order_time: float = 0

    # Misc
    api_lag_warnings: dict[str, float] = field(default_factory=dict)
    no_price_ticks: dict[int, int] = field(default_factory=dict)
    idle_tick_count: int = 0
    kill_switch_active: bool = False
    saved_positions: dict | None = None
    position_sync_failures: int = 0
