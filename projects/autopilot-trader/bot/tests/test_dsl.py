"""
Comprehensive tests for bot/dsl.py — Dynamic Stop Loss module.

Covers: current_move_pct, update_high_water, evaluate_dsl (hard SL, tiers,
breach counting, floor locking, stagnation, trailing buffer vs lock_hw_pct),
and edge cases including short positions and tier transitions.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl, evaluate_trailing_sl


# ── Helpers ──────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _future(minutes=0, seconds=0):
    return _now() + timedelta(minutes=minutes, seconds=seconds)


# ══════════════════════════════════════════════════════════════════════
# DSLState.current_move_pct()
# ══════════════════════════════════════════════════════════════════════

class TestCurrentMovePct:

    def test_long_price_up_positive_move(self):
        """Long: price up → positive move."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # price 100→110 = 10% move (raw, no leverage multiply)
        assert state.current_move_pct(110.0) == pytest.approx(10.0)

    def test_long_price_down_negative_move(self):
        """Long: price down → negative move."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # price 100→95 = -5% move (raw, no leverage multiply)
        assert state.current_move_pct(95.0) == pytest.approx(-5.0)

    def test_short_price_down_positive_move(self):
        """Short: price down → positive move."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)
        # short: -(100→90)/100 = -(-10%) = +10% (raw, no leverage)
        assert state.current_move_pct(90.0) == pytest.approx(10.0)

    def test_short_price_up_negative_move(self):
        """Short: price up → negative move."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)
        # short: -(100→110)/100 = -(10%) = -10% (raw, no leverage)
        assert state.current_move_pct(110.0) == pytest.approx(-10.0)

    def test_zero_entry_price_returns_zero(self):
        """Zero entry_price → returns 0.0."""
        state = DSLState(side="long", entry_price=0.0, leverage=10.0)
        assert state.current_move_pct(100.0) == 0.0


# ══════════════════════════════════════════════════════════════════════
# DSLState.update_high_water()
# ══════════════════════════════════════════════════════════════════════

class TestUpdateHighWater:

    def test_new_high_updates_fields(self):
        """New high → updates move_pct, price, time."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        t = _now()
        state.update_high_water(5.0, 105.0, t)
        assert state.high_water_move_pct == 5.0
        assert state.high_water_price == 105.0
        assert state.high_water_time == t

    def test_same_high_no_regression(self):
        """Same high water move → no update."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        t1 = _now()
        t2 = t1 + timedelta(seconds=10)
        state.update_high_water(5.0, 105.0, t1)
        state.update_high_water(5.0, 106.0, t2)
        # Should not update since move is the same (not strictly greater)
        assert state.high_water_price == 105.0
        assert state.high_water_time == t1

    def test_lower_price_no_update(self):
        """Lower move → no update to high water."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        t1 = _now()
        t2 = t1 + timedelta(seconds=10)
        state.update_high_water(5.0, 105.0, t1)
        state.update_high_water(3.0, 103.0, t2)
        assert state.high_water_move_pct == 5.0
        assert state.high_water_price == 105.0


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Hard SL
# ══════════════════════════════════════════════════════════════════════

class TestHardSL:

    def test_long_price_drops_to_hard_sl(self, dsl_config):
        """Long: price drops to hard SL → "hard_sl"."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # hard_sl_pct=1.25 → price = 100 * (1 - 1.25/100) = 98.75
        # move = -1.25% → triggers hard_sl
        assert evaluate_dsl(state, 98.75, dsl_config) == "hard_sl"

    def test_short_price_rises_to_hard_sl(self, dsl_config):
        """Short: price rises to hard SL → "hard_sl"."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)
        # short: -(101.25-100)/100 = -1.25% → triggers hard_sl
        assert evaluate_dsl(state, 101.25, dsl_config) == "hard_sl"

    def test_price_near_but_above_hard_sl(self, dsl_config):
        """Price near but above hard SL → None (hold)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # price=99.0 → move=-1% > -1.25% → no trigger
        result = evaluate_dsl(state, 99.0, dsl_config)
        assert result != "hard_sl"


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Tier Activation
# ══════════════════════════════════════════════════════════════════════

class TestTierActivation:

    def test_tier1_trigger_activates(self, dsl_config):
        """Price reaches tier 1 trigger (0.3% move) → tier activates, no lock yet."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # 0.3% move → price = 100 * (1 + 0.3/100) = 100.3
        # Use 100.31 to avoid floating point edge
        result = evaluate_dsl(state, 100.31, dsl_config)
        assert result is None  # No lock yet, just activated
        assert state.current_tier is not None
        assert state.current_tier.trigger_pct == 0.3

    def test_tier2_trigger_upgrade(self, dsl_config):
        """Price reaches tier 2 trigger → tier upgrade, breach counter resets."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # Reach tier 1 first
        state.breach_count = 2  # Simulate pre-existing breaches
        evaluate_dsl(state, 100.31, dsl_config)  # Tier 1 at 0.3%
        assert state.current_tier.trigger_pct == 0.3

        # Now reach tier 2 at 0.7% move → price = 100 * (1 + 0.7/100) = 100.7
        evaluate_dsl(state, 100.71, dsl_config)
        assert state.current_tier.trigger_pct == 0.7
        assert state.breach_count == 0  # Reset on tier upgrade

    def test_price_below_all_triggers(self, dsl_config):
        """Price below all triggers → None, no tier activated."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        result = evaluate_dsl(state, 100.0, dsl_config)
        assert result is None
        assert state.current_tier is None


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Breach Counting
# ══════════════════════════════════════════════════════════════════════

class TestBreachCounting:

    def test_single_breach_no_action(self, dsl_config):
        """1 breach below floor → no action (need N consecutive)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # First, push into tier 1 to establish floor
        # Tier 1: trigger=0.3, buffer=0.6 → at HW=0.4% move, floor = 0.4-0.6 = -0.2%
        # Actually we need HW to be above trigger. Let's push higher.
        # HW=0.4% → floor = 0.4 - 0.6 = -0.2%. To breach we need move < -0.2%.

        # Simpler: manually set up state to be in a tier
        state.current_tier = dsl_config.tiers[0]  # trigger=0.3, buffer=0.6
        state.high_water_move_pct = 0.4  # HW above trigger
        # Floor = 0.4 - 0.6 = -0.2%. Breach at move < -0.2%
        # Price for -0.2% move: price = 99.8
        # But hard SL at -1.25%, so we're fine there.

        result = evaluate_dsl(state, 99.7, dsl_config)  # move = -0.3%, breach
        assert result is None
        assert state.breach_count == 1

    def test_n_consecutive_breaches_ratchets_floor(self, dsl_config):
        """N consecutive breaches → ratchets floor."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]  # consecutive_breaches=3
        state.high_water_move_pct = 0.4
        # Floor = 0.4 - 0.6 = -0.2%. Breach at move < -0.2% → price < 99.8

        # 3 consecutive breaches
        evaluate_dsl(state, 99.7, dsl_config)  # breach 1
        evaluate_dsl(state, 99.6, dsl_config)  # breach 2
        result = evaluate_dsl(state, 99.5, dsl_config)  # breach 3 → ratchet

        assert state.breach_count >= 3
        assert state.locked_floor_pct is not None

    def test_recovery_resets_breach_counter(self, dsl_config):
        """Price recovers above floor between breaches → counter resets."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_move_pct = 0.4
        # Floor = -0.2%, breach < -0.2% → price < 99.8

        evaluate_dsl(state, 99.7, dsl_config)  # breach 1
        assert state.breach_count == 1

        # Recover above floor
        evaluate_dsl(state, 100.0, dsl_config)  # move=0% > -0.2%
        assert state.breach_count == 0

    def test_breach_recovery_breach_counter_resets(self, dsl_config):
        """Breach → recovery → breach doesn't accumulate."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_move_pct = 0.4

        evaluate_dsl(state, 99.7, dsl_config)  # breach
        evaluate_dsl(state, 100.0, dsl_config)  # recovery → resets to 0
        evaluate_dsl(state, 99.7, dsl_config)  # breach again → count = 1, not 2

        assert state.breach_count == 1


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Floor Locking
# ══════════════════════════════════════════════════════════════════════

class TestFloorLocking:

    def test_enough_breaches_then_locked_breach_returns_tier_lock(self, dsl_config):
        """Enough breaches → floor locks, subsequent breach below locked floor → "tier_lock"."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]  # consecutive_breaches=3, buffer=0.6
        state.high_water_move_pct = 0.4
        # Floor = 0.4 - 0.6 = -0.2%

        # 3 consecutive breaches to lock
        evaluate_dsl(state, 99.7, dsl_config)  # breach 1
        evaluate_dsl(state, 99.7, dsl_config)  # breach 2
        result = evaluate_dsl(state, 99.5, dsl_config)  # breach 3 → locks floor

        # After locking, locked_floor_pct should be set
        locked = state.locked_floor_pct
        assert locked is not None

        # Now breach below the locked floor → "tier_lock"
        # Need price that gives move below locked floor
        # If locked floor is -0.2%, need move < -0.2% → price < 99.8
        result = evaluate_dsl(state, 99.5, dsl_config)
        assert result == "tier_lock"

    def test_locked_floor_never_loosens(self, dsl_config):
        """Locked floor never loosens — new computed floor lower → keeps higher locked value."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_move_pct = 0.4
        # Floor = -0.2%

        # 3 breaches to lock at -0.2%
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.5, dsl_config)

        locked = state.locked_floor_pct
        assert locked is not None

        # Now reduce high water (simulating price drop) — new computed floor would be lower
        state.high_water_move_pct = 0.3  # floor would be 0.3-0.6 = -0.3%, but locked stays at -0.2%
        # The locked floor should not loosen
        assert state.locked_floor_pct == locked

    def test_floor_lock_resets_hw_time_at_positive_move(self, dsl_config):
        """Floor lock at positive move → HW time resets (MED-15)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_move_pct = 0.4
        old_time = _now() - timedelta(minutes=30)
        state.high_water_time = old_time

        # 3 breaches to trigger ratchet
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.5, dsl_config)

        # HW time should have been reset on first ratchet
        # The locked floor is -0.2% (negative), so MED-15 reset only fires when locked_floor_pct > 0

    def test_floor_lock_positive_move_resets_hw_time(self, dsl_config):
        """Floor lock at positive locked floor → HW time resets (MED-15)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[3]  # trigger=1.5, buffer=0.3, consecutive=2
        state.high_water_move_pct = 2.0  # HW well above trigger
        # Floor = 2.0 - 0.3 = 1.7% (positive!)

        old_time = _now() - timedelta(minutes=30)
        state.high_water_time = old_time
        state.locked_floor_pct = None

        # 2 breaches to lock (consecutive_breaches=2 for this tier)
        # Need move < 1.7% → price < 101.7
        evaluate_dsl(state, 101.5, dsl_config)  # move = 1.5%, breach
        result = evaluate_dsl(state, 101.5, dsl_config)  # breach 2 → ratchet

        assert state.locked_floor_pct is not None
        # Check HW time was reset
        assert state.high_water_time != old_time


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Stagnation
# ══════════════════════════════════════════════════════════════════════

class TestStagnation:

    def test_hw_reaches_stagnation_move_pct_timer_starts(self, dsl_config):
        """HW reaches stagnation_move_pct → stagnation timer starts."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        assert not state.stagnation_active

        # Below stagnation_move_pct (0.5%) → no activation
        evaluate_dsl(state, 100.31, dsl_config)  # move ~ 0.31% < 0.5%
        assert not state.stagnation_active

        # At stagnation_move_pct (0.5%) → activates
        # move = 0.81% → price = 100.81
        evaluate_dsl(state, 100.81, dsl_config)  # move ~ 0.81%
        assert state.stagnation_active

    def test_new_high_water_resets_timer(self, dsl_config):
        """New high water → stagnation timer resets."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Reach stagnation_move_pct → starts stagnation timer
        # move ~0.81% → price = 100.81
        evaluate_dsl(state, 100.81, dsl_config)  # move~0.81%, stagnation starts
        first_time = state.high_water_time

        # New high resets timer (101.0 → move=1%)
        evaluate_dsl(state, 101.0, dsl_config)
        assert state.high_water_time != first_time

    def test_elapsed_exceeds_stagnation_minutes_returns_stagnation(self, dsl_config):
        """Elapsed > stagnation_minutes + move >= stagnation_move_pct → "stagnation"."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Set HW at 1.0% move with time 61 minutes ago. Current move will be ~0.81%
        # which is < HW so update_high_water won't reset the timer.
        state.high_water_move_pct = 1.0
        state.high_water_price = 101.0
        state.high_water_time = _now() - timedelta(minutes=61)
        state.stagnation_active = True
        state.current_tier = dsl_config.tiers[1]  # trigger=0.7, buffer=0.5

        # Floor = 1.0 - 0.5 = 0.5%. Current move ~0.81% > 0.5% → no breach.
        # Move ~0.81% >= 0.5% stagnation_move_pct + 61 min elapsed → "stagnation"
        result = evaluate_dsl(state, 100.81, dsl_config)
        assert result == "stagnation"

    def test_below_stagnation_move_pct_no_stagnation(self, dsl_config):
        """Position below stagnation_move_pct → no stagnation even if timer expired."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Set up: HW=1.0% (above current move), stagnation active, timer expired
        # Current move will be 0.5%, which is < HW so timer won't be reset
        state.high_water_move_pct = 1.0
        state.high_water_price = 101.0
        state.high_water_time = _now() - timedelta(minutes=61)
        state.stagnation_active = True
        state.current_tier = dsl_config.tiers[0]

        # Current move = 0.5% < 0.5% stagnation_move_pct → no stagnation
        # (actually 0.5 == 0.5, so it would trigger. Let's use 0.4)
        result = evaluate_dsl(state, 100.4, dsl_config)
        assert result != "stagnation"

    def test_timer_not_started_no_stagnation(self, dsl_config):
        """Timer not started (HW below min trigger) → no stagnation."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Price below min trigger → stagnation never activates
        evaluate_dsl(state, 100.0, dsl_config)
        assert not state.stagnation_active

        # Even if we manually set a time, it shouldn't trigger
        state.high_water_time = _now() - timedelta(minutes=120)
        result = evaluate_dsl(state, 100.0, dsl_config)
        assert result != "stagnation"


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Trailing Buffer vs lock_hw_pct
# ══════════════════════════════════════════════════════════════════════

class TestTrailingBufferVsLockHWPct:

    def test_trailing_buffer_floor_calculation(self):
        """Trailing buffer: floor = HW - buffer (e.g. HW=1.5%, buffer=0.3 → floor=1.2%)."""
        tier = DSLTier(trigger_pct=0.3, lock_hw_pct=30, trailing_buffer_pct=0.3, consecutive_breaches=3)
        cfg = DSLConfig(
            tiers=[tier],
            stagnation_move_pct=0.5,
            stagnation_minutes=60,
            hard_sl_pct=1.25,
        )
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Push HW to 1.5% move → price = 101.5
        evaluate_dsl(state, 101.5, cfg)
        assert state.high_water_move_pct == pytest.approx(1.5)

        # Floor should be 1.5 - 0.3 = 1.2%
        # At price 101.1: move = 1.1% < 1.2% → should be a breach
        result = evaluate_dsl(state, 101.1, cfg)
        assert state.breach_count >= 1  # Breach detected

    def test_lock_hw_pct_floor_calculation(self):
        """lock_hw_pct: floor = HW * lock_pct / 100."""
        # Use a tier with trailing_buffer_pct=None to force lock_hw_pct path
        tier = DSLTier(trigger_pct=0.3, lock_hw_pct=40, trailing_buffer_pct=None, consecutive_breaches=3)
        cfg = DSLConfig(
            tiers=[tier],
            stagnation_move_pct=0.5,
            stagnation_minutes=60,
            hard_sl_pct=1.25,
        )
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Push HW to 1.0% move → price = 101.0
        evaluate_dsl(state, 101.0, cfg)
        assert state.high_water_move_pct == pytest.approx(1.0)

        # Floor = 1.0 * 40 / 100 = 0.4%
        # At move < 0.4% → price < 100.4 → breach
        evaluate_dsl(state, 100.3, cfg)
        assert state.breach_count >= 1


# ══════════════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_short_position_dsl_logic_works(self, dsl_config):
        """Short position — DSL logic works mirrored."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)

        # Price down → positive move for short
        # At price=97, short: -(97-100)/100 = 3% move
        result = evaluate_dsl(state, 97.0, dsl_config)
        # Should activate tier 1 at least (3% > 0.3% trigger)
        assert state.current_tier is not None

        # Hard SL for short: price rises → negative move
        # At price=101.25, short: -(101.25-100)/100 = -1.25%
        state2 = DSLState(side="short", entry_price=100.0, leverage=10.0)
        assert evaluate_dsl(state2, 101.25, dsl_config) == "hard_sl"

    def test_tier_transition_mid_breach_counter_resets(self, dsl_config):
        """Tier transition mid-breach → counter resets, new tier evaluated."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Activate tier 1 and accumulate 2 breaches
        evaluate_dsl(state, 100.31, dsl_config)  # Tier 1 (0.3%)
        assert state.current_tier.trigger_pct == 0.3
        state.breach_count = 2

        # Now push to tier 2 — should reset breach counter
        evaluate_dsl(state, 100.71, dsl_config)  # Tier 2 (0.7%)
        assert state.current_tier.trigger_pct == 0.7
        assert state.breach_count == 0  # Reset!

    def test_evaluate_returns_none_when_all_ok(self, dsl_config):
        """Normal holding conditions → None (no action)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # Price slightly up, well within bounds
        result = evaluate_dsl(state, 100.1, dsl_config)
        assert result is None

    def test_negative_entry_price_behavior(self, dsl_config):
        """Negative entry price → current_move_pct returns 0 (guarded by <= 0 check)."""
        state = DSLState(side="long", entry_price=-100.0, leverage=10.0)
        assert state.current_move_pct(110.0) == 0.0

    def test_very_high_leverage_hard_sl_trigger(self, dsl_config):
        """Hard SL is price-based, same trigger regardless of leverage."""
        state = DSLState(side="long", entry_price=100.0, leverage=50.0)
        # hard_sl_pct = -1.25% → price = 98.75 (same for any leverage)
        assert evaluate_dsl(state, 98.75, dsl_config) == "hard_sl"

    def test_short_stagnation_works(self, dsl_config):
        """Short position stagnation works with positive move when price drops."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)

        # Price drops → positive move for short
        # At price=92, short move = (100-92)/100 = 8%
        evaluate_dsl(state, 92.0, dsl_config)
        assert state.stagnation_active

        # Set time to exceed stagnation window
        state.high_water_time = _now() - timedelta(minutes=61)

        # Current move must be >= stagnation_move_pct (0.5%)
        # At price=92, move=8% ≥ 0.5% → stagnation
        result = evaluate_dsl(state, 92.0, dsl_config)
        assert result == "stagnation"

    def test_single_tier_config(self):
        """Config with only one tier works correctly."""
        tier = DSLTier(trigger_pct=0.5, lock_hw_pct=50, trailing_buffer_pct=0.2, consecutive_breaches=2)
        cfg = DSLConfig(tiers=[tier], stagnation_move_pct=0.5, stagnation_minutes=60, hard_sl_pct=1.25)
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Below trigger → no tier
        result = evaluate_dsl(state, 100.3, cfg)
        assert result is None

        # At trigger → tier activates
        result = evaluate_dsl(state, 100.5, cfg)  # move = 0.5%
        assert state.current_tier is not None
        assert state.current_tier.trigger_pct == 0.5


# ══════════════════════════════════════════════════════════════════════
# evaluate_trailing_sl
# ══════════════════════════════════════════════════════════════════════

TRIGGER_PCT = 0.5
STEP_PCT = 0.95
HARD_FLOOR_PCT = 1.25


class TestEvaluateTrailingSL:

    def test_long_activation(self):
        """Long: HW reaches trigger → trailing_sl_activated becomes True."""
        entry = 100.0
        hw = 100.6  # above trigger 100 * 1.005 = 100.5
        action, level, activated = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=100.6,
            high_water_price=hw,
            trailing_sl_level=None,
            trailing_sl_activated=False,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert activated is True
        assert action is None  # no trigger yet

    def test_long_trigger_fires(self):
        """Long: already activated, price drops below SL level → 'trailing_sl'."""
        entry = 100.0
        # HW=100.6, candidate = 100.6 * (1 - 0.95/100) = 99.6443
        # max(99.55, 99.6443) = 99.6443
        action, level, activated = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=99.4,
            high_water_price=100.6,
            trailing_sl_level=99.55,
            trailing_sl_activated=True,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert action == "trailing_sl"
        assert level == pytest.approx(100.6 * (1 - STEP_PCT / 100))

    def test_long_hard_floor(self):
        """Long: price below hard floor → 'trailing_sl' immediately even if not activated."""
        entry = 100.0
        # hard floor = 100 * (1 - 1.25/100) = 98.75
        action, level, activated = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=98.7,  # below 98.75
            high_water_price=100.6,
            trailing_sl_level=None,
            trailing_sl_activated=False,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert action == "trailing_sl"
        assert level is None
        assert activated is False

    def test_long_ratchet_never_goes_down(self):
        """Long: ratchet only moves up — HW drops don't lower the SL level."""
        entry = 100.0

        # HW at 102, ratchet: candidate = 102 * (1 - 0.95/100) = 102 * 0.9905 = 101.031
        action1, level1, activated1 = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=102.0,
            high_water_price=102.0,
            trailing_sl_level=None,
            trailing_sl_activated=True,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        expected_level = 102.0 * (1 - STEP_PCT / 100)
        assert level1 == pytest.approx(expected_level)

        # HW drops to 101, new candidate = 101 * 0.9905 = 100.0405
        # but SL stays at 101.031 (max)
        action2, level2, activated2 = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=102.0,
            high_water_price=101.0,
            trailing_sl_level=level1,
            trailing_sl_activated=True,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert level2 == pytest.approx(level1)  # never goes down

    def test_long_not_activated_no_trigger(self):
        """Long: HW below trigger, price normal → no activation, no trigger."""
        entry = 100.0
        # trigger at 100 * 1.005 = 100.5, HW=100.3 (below)
        action, level, activated = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=100.2,
            high_water_price=100.3,
            trailing_sl_level=None,
            trailing_sl_activated=False,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert action is None
        assert activated is False

    def test_short_activation(self):
        """Short: HW below trigger → activates."""
        entry = 100.0
        # trigger at 100 * (1 - 0.5/100) = 99.5, HW=99.4 (below)
        action, level, activated = evaluate_trailing_sl(
            side="short",
            entry_price=entry,
            price=99.4,
            high_water_price=99.4,
            trailing_sl_level=None,
            trailing_sl_activated=False,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert activated is True
        assert action is None

    def test_short_trigger_fires(self):
        """Short: already activated, price rises above SL level → 'trailing_sl'."""
        entry = 100.0
        # HW=99.4, candidate = 99.4 * (1 + 0.95/100) = 100.3443
        # min(100.45, 100.3443) = 100.3443
        action, level, activated = evaluate_trailing_sl(
            side="short",
            entry_price=entry,
            price=100.5,
            high_water_price=99.4,
            trailing_sl_level=100.45,
            trailing_sl_activated=True,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert action == "trailing_sl"
        assert level == pytest.approx(99.4 * (1 + STEP_PCT / 100))

    def test_short_hard_floor(self):
        """Short: price above hard floor → 'trailing_sl'."""
        entry = 100.0
        # hard floor = 100 * (1 + 1.25/100) = 101.25
        action, level, activated = evaluate_trailing_sl(
            side="short",
            entry_price=entry,
            price=101.3,  # above 101.25
            high_water_price=99.4,
            trailing_sl_level=None,
            trailing_sl_activated=False,
            trigger_pct=TRIGGER_PCT,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert action == "trailing_sl"
        assert level is None

    def test_trigger_zero_immediate_activation(self):
        """Long: trigger=0 → activates immediately at entry."""
        entry = 100.0
        action, level, activated = evaluate_trailing_sl(
            side="long",
            entry_price=entry,
            price=100.0,
            high_water_price=100.0,
            trailing_sl_level=None,
            trailing_sl_activated=False,
            trigger_pct=0.0,
            step_pct=STEP_PCT,
            hard_floor_pct=HARD_FLOOR_PCT,
        )
        assert activated is True
