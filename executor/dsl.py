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
    """Each tier activates when ROE reaches trigger_pct, locking in lock_hw_pct of the peak."""
    trigger_pct: float          # ROE% to activate this tier
    lock_hw_pct: float          # % of high-water ROE to lock (40-85)
    consecutive_breaches: int   # breaches needed before ratcheting to this tier's floor


# Default tiers — conservative, then progressively tighter
DEFAULT_TIERS = [
    DSLTier(trigger_pct=7,  lock_hw_pct=40, consecutive_breaches=3),
    DSLTier(trigger_pct=12, lock_hw_pct=55, consecutive_breaches=2),
    DSLTier(trigger_pct=15, lock_hw_pct=75, consecutive_breaches=2),
    DSLTier(trigger_pct=20, lock_hw_pct=85, consecutive_breaches=1),
]


@dataclass
class DSLConfig:
    tiers: list[DSLTier] = field(default_factory=lambda: list(DEFAULT_TIERS))
    stagnation_roe_pct: float = 8.0   # ROE% threshold for stagnation check
    stagnation_minutes: int = 60      # exit if no new high for this long
    hard_sl_pct: float = 2.0          # hard stop loss from entry (ignores DSL)


@dataclass
class DSLState:
    """Per-position DSL state."""
    side: str                           # "long" or "short"
    entry_price: float
    leverage: float = 10.0

    high_water_roe: float = 0.0         # peak ROE% seen
    high_water_price: float = 0.0       # price at peak ROE
    high_water_time: datetime | None = None

    current_tier: DSLTier | None = None
    breach_count: int = 0
    locked_floor_roe: float | None = None  # ROE% floor we've committed to

    stagnation_active: bool = False
    stagnation_started: datetime | None = None

    def current_roe(self, price: float) -> float:
        """Calculate ROE% from price movement and leverage."""
        if self.entry_price <= 0:
            return 0.0
        move = (price - self.entry_price) / self.entry_price * 100
        if self.side == "short":
            move = -move
        return move * self.leverage

    def update_high_water(self, roe: float, price: float, now: datetime):
        if roe > self.high_water_roe:
            self.high_water_roe = roe
            self.high_water_price = price
            self.high_water_time = now
            self.stagnation_active = False
            self.stagnation_started = None


def evaluate_dsl(state: DSLState, price: float, cfg: DSLConfig) -> str | None:
    """
    Evaluate DSL for one tick. Returns action or None.

    Actions:
        "tier_lock"    — DSL tier floor breached, close position
        "stagnation"   — position stalled, take profit
        "hard_sl"      — hard stop loss from entry breached
        None           — hold

    Call this INSTEAD of the old flat trailing SL logic.
    """
    now = datetime.now(timezone.utc)
    roe = state.current_roe(price)
    state.update_high_water(roe, price, now)

    # ── Hard SL: absolute floor, always check first ──
    hard_sl_roe = -abs(cfg.hard_sl_pct) * state.leverage
    if roe <= hard_sl_roe:
        return "hard_sl"

    # ── Find highest tier we qualify for based on High Water ROE, not current ──
    best_tier = None
    for tier in sorted(cfg.tiers, key=lambda t: -t.trigger_pct):
        if state.high_water_roe >= tier.trigger_pct:
            best_tier = tier
            break

    # ── Tier transition: reset breach counter ──
    if best_tier and (state.current_tier is None or best_tier.trigger_pct != state.current_tier.trigger_pct):
        state.breach_count = 0
        state.current_tier = best_tier

    # ── Calculate what the floor SHOULD be at current tier + HW ──
    if state.current_tier and state.high_water_roe > 0:
        lock_floor_roe = state.high_water_roe * state.current_tier.lock_hw_pct / 100
    elif state.current_tier:
        # Just activated, hasn't seen HW yet — floor at trigger
        lock_floor_roe = state.current_tier.trigger_pct * state.current_tier.lock_hw_pct / 100
    else:
        lock_floor_roe = None

    # ── Check if price is below the computed floor ──
    if lock_floor_roe is not None and roe < lock_floor_roe:
        state.breach_count += 1
        needed = state.current_tier.consecutive_breaches if state.current_tier else 3

        if state.breach_count >= needed:
            # Enough consecutive breaches → ratchet the floor up permanently
            if state.locked_floor_roe is None or lock_floor_roe > state.locked_floor_roe:
                state.locked_floor_roe = lock_floor_roe

            # Check if we've breached even the locked floor
            if state.locked_floor_roe is not None and roe < state.locked_floor_roe:
                return "tier_lock"
        # else: not enough breaches yet, keep watching
    else:
        # Back above floor → reset breach counter
        state.breach_count = 0

    # ── Stagnation check: high ROE but no progress ──
    if roe >= cfg.stagnation_roe_pct:
        if not state.stagnation_active:
            state.stagnation_active = True
            state.stagnation_started = now
        elif state.stagnation_started:
            elapsed = now - state.stagnation_started
            if elapsed >= timedelta(minutes=cfg.stagnation_minutes):
                return "stagnation"
    else:
        state.stagnation_active = False
        state.stagnation_started = None

    return None
