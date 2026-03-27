"""
Dynamic Stop Loss (DSL) — High Water Mode

Adapted from Senpi's DSL v5 (Apache-2.0).
Tiered trailing stop loss that tightens as profit increases.
No LLM tokens, no exchange dependency — pure Python logic.

Usage:
    from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


# ── Types ─────────────────────────────────────────────────────────────

@dataclass
class DSLTier:
    """Each tier activates when price move % reaches trigger_pct, locking in a floor from the peak."""
    trigger_pct: float          # Price move % to activate this tier
    lock_hw_pct: float          # % of high-water move to lock (40-85), used when trailing_buffer_pct is None
    trailing_buffer_pct: float | None = None  # Fixed move % buffer from HW (floor = HW - buffer). Takes priority over lock_hw_pct.
    consecutive_breaches: int = 3  # breaches needed before ratcheting to this tier's floor


# Default tiers — conservative, then progressively tighter
DEFAULT_TIERS = [
    DSLTier(trigger_pct=0.3,  lock_hw_pct=30, trailing_buffer_pct=0.6, consecutive_breaches=3),
    DSLTier(trigger_pct=0.7,  lock_hw_pct=40, trailing_buffer_pct=0.5, consecutive_breaches=3),
    DSLTier(trigger_pct=1.2, lock_hw_pct=55, trailing_buffer_pct=0.4, consecutive_breaches=2),
    DSLTier(trigger_pct=1.5, lock_hw_pct=75, trailing_buffer_pct=0.3, consecutive_breaches=2),
    DSLTier(trigger_pct=2.0, lock_hw_pct=85, trailing_buffer_pct=0.2, consecutive_breaches=2),
    DSLTier(trigger_pct=3.0, lock_hw_pct=90, trailing_buffer_pct=0.1, consecutive_breaches=2),
]


@dataclass
class DSLConfig:
    tiers: list[DSLTier] = field(default_factory=lambda: list(DEFAULT_TIERS))
    stagnation_move_pct: float = 0.5  # price move % threshold for stagnation check
    stagnation_minutes: int = 90      # exit if no new high water mark for this long
    hard_sl_pct: float = 1.25         # hard stop loss from entry (ignores DSL)


@dataclass
class DSLState:
    """Per-position DSL state."""
    side: str                           # "long" or "short"
    entry_price: float
    leverage: float = 10.0              # Exchange-enforced leverage (display only, not used in SL math)

    high_water_move_pct: float = 0.0    # peak price move % seen
    high_water_roe: float = 0.0         # deprecated — kept for backward compat during migration
    high_water_price: float = 0.0       # price at peak move
    high_water_time: datetime | None = None

    current_tier: DSLTier | None = None
    breach_count: int = 0
    locked_floor_pct: float | None = None  # price move % floor we've committed to
    locked_floor_roe: float | None = None  # deprecated — kept for backward compat during migration

    stagnation_active: bool = False
    stagnation_started: datetime | None = None

    def current_move_pct(self, price: float) -> float:
        """Calculate raw price movement % from entry. Positive = profitable direction."""
        if self.entry_price <= 0:
            return 0.0
        move = (price - self.entry_price) / self.entry_price * 100
        if self.side == "short":
            move = -move
        return move

    def update_high_water(self, move_pct: float, price: float, now: datetime):
        if move_pct > self.high_water_move_pct:
            self.high_water_move_pct = move_pct
            self.high_water_price = price
            self.high_water_time = now


def evaluate_dsl(state: DSLState, price: float, cfg: DSLConfig) -> str | None:
    """
    Evaluate DSL for one tick. Returns action or None.

    Actions:
        "tier_lock"    — DSL tier floor breached, close position
        "stagnation"   — position stalled, exit
        "hard_sl"      — hard stop loss from entry breached
        None           — hold

    Call this INSTEAD of the old flat trailing SL logic.
    """
    now = datetime.now(timezone.utc)
    move_pct = state.current_move_pct(price)
    state.update_high_water(move_pct, price, now)

    # ── Hard SL: absolute floor, always check first ──
    # Small tolerance (0.001%) to handle floating point imprecision
    hard_sl_floor = -abs(cfg.hard_sl_pct)
    if move_pct <= hard_sl_floor + 0.001:
        return "hard_sl"

    # ── Find highest tier we qualify for based on High Water move %, not current ──
    best_tier = None
    for tier in sorted(cfg.tiers, key=lambda t: -t.trigger_pct):
        if state.high_water_move_pct >= tier.trigger_pct:
            best_tier = tier
            break

    # ── Activate stagnation timer: when HW first reaches stagnation_move_pct ──
    # Aligned with exit check — both use the same threshold so there's no dead zone
    # where the timer runs but can never fire. Position must reach "decent profit"
    # (stagnation_move_pct) to start the clock.
    if not state.stagnation_active and state.high_water_move_pct >= cfg.stagnation_move_pct:
        state.stagnation_active = True
        state.stagnation_started = now
        state.high_water_time = now

    # ── Tier transition: reset breach counter ──
    if best_tier and (state.current_tier is None or best_tier.trigger_pct != state.current_tier.trigger_pct):
        state.breach_count = 0
        state.current_tier = best_tier

    # ── Calculate what the floor SHOULD be at current tier + HW ──
    if state.current_tier and state.high_water_move_pct > 0:
        if state.current_tier.trailing_buffer_pct is not None:
            # Fixed buffer: floor = HW - buffer (consistent cushion)
            lock_floor_pct = state.high_water_move_pct - state.current_tier.trailing_buffer_pct
        else:
            # Percentage: floor = HW * lock_pct / 100 (backwards compat)
            lock_floor_pct = state.high_water_move_pct * state.current_tier.lock_hw_pct / 100
    elif state.current_tier:
        # Just activated, hasn't seen HW yet — floor at trigger
        lock_floor_pct = state.current_tier.trigger_pct * state.current_tier.lock_hw_pct / 100
    else:
        lock_floor_pct = None

    # ── Check if price is below the computed floor ──
    if lock_floor_pct is not None and move_pct < lock_floor_pct:
        state.breach_count += 1
        needed = state.current_tier.consecutive_breaches if state.current_tier else 3

        if state.breach_count >= needed:
            # Enough consecutive breaches → ratchet the floor up permanently
            ratchet_first_time = state.locked_floor_pct is None
            if state.locked_floor_pct is None or lock_floor_pct > state.locked_floor_pct:
                state.locked_floor_pct = lock_floor_pct

            # MED-15: Reset HW time on first ratchet so stagnation counts from profit lock
            if ratchet_first_time and lock_floor_pct > 0:
                state.high_water_time = now

            # Check if we've breached even the locked floor
            if state.locked_floor_pct is not None and move_pct < state.locked_floor_pct:
                return "tier_lock"
        # else: not enough breaches yet, keep watching
    else:
        # Back above floor → reset breach counter
        state.breach_count = 0

    # ── Stagnation check: active once HW reaches minimum tier trigger ──
    # Timer starts from first time position reaches a tier trigger level (e.g., 0.3%).
    # Tracks "time since last new high" — only new highs reset the timer.
    # Brief dips below stagnation_move_pct don't reset it.
    # Minimum move % floor avoids exiting losing positions.
    if state.stagnation_active and move_pct >= cfg.stagnation_move_pct and state.high_water_time:
        elapsed = now - state.high_water_time
        if elapsed >= timedelta(minutes=cfg.stagnation_minutes):
            return "stagnation"

    return None


def evaluate_trailing_sl(
    side: str,
    entry_price: float,
    price: float,
    high_water_price: float,
    trailing_sl_level: float | None,
    trailing_sl_activated: bool,
    trigger_pct: float,
    step_pct: float,
    hard_floor_pct: float,
) -> tuple[str | None, float | None, bool]:
    """
    Evaluate trailing stop loss for one tick.

    Returns (action, new_trailing_sl_level, new_trailing_sl_activated).
    action is "trailing_sl" or None.

    The trailing SL ratchets UP (for longs) from the high water mark.
    It only activates after price moves trigger_pct above entry.
    The hard floor is a stop loss at entry * (1 - hard_floor_pct/100).
    """
    activated = trailing_sl_activated

    if side == "long":
        # 1. Hard floor check
        hard_floor = entry_price * (1 - hard_floor_pct / 100)
        if price <= hard_floor:
            return ("trailing_sl", None, False)

        # 2. Activation
        if not activated:
            trigger_level = entry_price * (1 + trigger_pct / 100)
            if high_water_price >= trigger_level:
                activated = True

        # 3. Ratchet
        new_level = trailing_sl_level
        if activated and high_water_price > 0:
            candidate = high_water_price * (1 - step_pct / 100)
            new_level = max(trailing_sl_level or 0, candidate)

        # 4. Trigger
        if activated and new_level is not None and price <= new_level:
            return ("trailing_sl", new_level, True)

        # 5. Otherwise
        return (None, new_level, activated)

    else:  # short
        # 1. Hard floor check
        hard_floor = entry_price * (1 + hard_floor_pct / 100)
        if price >= hard_floor:
            return ("trailing_sl", None, False)

        # 2. Activation
        if not activated:
            trigger_level = entry_price * (1 - trigger_pct / 100)
            if high_water_price <= trigger_level:
                activated = True

        # 3. Ratchet
        new_level = trailing_sl_level
        if activated and high_water_price > 0:
            candidate = high_water_price * (1 + step_pct / 100)
            new_level = min(trailing_sl_level or float("inf"), candidate)

        # 4. Trigger
        if activated and new_level is not None and price >= new_level:
            return ("trailing_sl", new_level, True)

        # 5. Otherwise
        return (None, new_level, activated)
