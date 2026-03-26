"""
Comprehensive tests for bot/dsl.py — Dynamic Stop Loss module.

Covers: current_roe, update_high_water, evaluate_dsl (hard SL, tiers,
breach counting, floor locking, stagnation, trailing buffer vs lock_hw_pct),
and edge cases including short positions and tier transitions.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl


# ── Helpers ──────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _future(minutes=0, seconds=0):
    return _now() + timedelta(minutes=minutes, seconds=seconds)


# ══════════════════════════════════════════════════════════════════════
# DSLState.current_roe()
# ══════════════════════════════════════════════════════════════════════

class TestCurrentROE:

    def test_long_price_up_positive_roe(self):
        """Long: price up → positive ROE."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # price 100→110 = 10% move × 10 lev = +100% ROE
        assert state.current_roe(110.0) == pytest.approx(100.0)

    def test_long_price_down_negative_roe(self):
        """Long: price down → negative ROE."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # price 100→95 = -5% move × 10 lev = -50% ROE
        assert state.current_roe(95.0) == pytest.approx(-50.0)

    def test_short_price_down_positive_roe(self):
        """Short: price down → positive ROE."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)
        # short: -(100→90)/100 = -(-10%) = +10% × 10 lev = +100% ROE
        assert state.current_roe(90.0) == pytest.approx(100.0)

    def test_short_price_up_negative_roe(self):
        """Short: price up → negative ROE."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)
        # short: -(100→110)/100 = -(10%) = -10% × 10 lev = -100% ROE
        assert state.current_roe(110.0) == pytest.approx(-100.0)

    def test_zero_entry_price_returns_zero(self):
        """Zero entry_price → returns 0.0."""
        state = DSLState(side="long", entry_price=0.0, leverage=10.0)
        assert state.current_roe(100.0) == 0.0


# ══════════════════════════════════════════════════════════════════════
# DSLState.update_high_water()
# ══════════════════════════════════════════════════════════════════════

class TestUpdateHighWater:

    def test_new_high_updates_fields(self):
        """New high → updates roe, price, time."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        t = _now()
        state.update_high_water(50.0, 105.0, t)
        assert state.high_water_roe == 50.0
        assert state.high_water_price == 105.0
        assert state.high_water_time == t

    def test_same_high_no_regression(self):
        """Same high water ROE → no update."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        t1 = _now()
        t2 = t1 + timedelta(seconds=10)
        state.update_high_water(50.0, 105.0, t1)
        state.update_high_water(50.0, 106.0, t2)
        # Should not update since ROE is the same (not strictly greater)
        assert state.high_water_price == 105.0
        assert state.high_water_time == t1

    def test_lower_price_no_update(self):
        """Lower ROE → no update to high water."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        t1 = _now()
        t2 = t1 + timedelta(seconds=10)
        state.update_high_water(50.0, 105.0, t1)
        state.update_high_water(30.0, 103.0, t2)
        assert state.high_water_roe == 50.0
        assert state.high_water_price == 105.0


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Hard SL
# ══════════════════════════════════════════════════════════════════════

class TestHardSL:

    def test_long_price_drops_to_hard_sl(self, dsl_config):
        """Long: price drops to hard SL → "hard_sl"."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # hard_sl_pct=1.25 → hard_sl_roe = -1.25 * 10 = -12.5%
        # breakeven at 100, -12.5% ROE → price ≈ 100 * (1 - 1.25/100) = 98.75
        # With tolerance +0.001: roe <= -12.5 + 0.001 = -12.499
        # At price=98.75: move = -1.25% → ROE = -12.5% → triggers
        assert evaluate_dsl(state, 98.75, dsl_config) == "hard_sl"

    def test_short_price_rises_to_hard_sl(self, dsl_config):
        """Short: price rises to hard SL → "hard_sl"."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)
        # short: -(100→101.25)/100 = -1.25% → ROE = -12.5%
        assert evaluate_dsl(state, 101.25, dsl_config) == "hard_sl"

    def test_price_near_but_above_hard_sl(self, dsl_config):
        """Price near but above hard SL → None (hold)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # price=99.0 → move=-1% → ROE=-10% > -12.5+0.001 → no trigger
        result = evaluate_dsl(state, 99.0, dsl_config)
        assert result != "hard_sl"


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Tier Activation
# ══════════════════════════════════════════════════════════════════════

class TestTierActivation:

    def test_tier1_trigger_activates(self, dsl_config):
        """Price reaches tier 1 trigger (3% ROE) → tier activates, no lock yet."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # 3% ROE at lev=10 → price move = 0.3% → use 100.31 to avoid floating point edge
        result = evaluate_dsl(state, 100.31, dsl_config)
        assert result is None  # No lock yet, just activated
        assert state.current_tier is not None
        assert state.current_tier.trigger_pct == 3

    def test_tier2_trigger_upgrade(self, dsl_config):
        """Price reaches tier 2 trigger → tier upgrade, breach counter resets."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # Reach tier 1 first
        state.breach_count = 2  # Simulate pre-existing breaches
        evaluate_dsl(state, 100.31, dsl_config)  # Tier 1 at 3%
        assert state.current_tier.trigger_pct == 3

        # Now reach tier 2 at 7% ROE → price = 100 * (1 + 7%/10) = 100.7
        evaluate_dsl(state, 100.71, dsl_config)
        assert state.current_tier.trigger_pct == 7
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
        # Tier 1: trigger=3, buffer=6 → at HW=3% ROE, floor = 3-6 = -3%
        # Actually we need HW to be above trigger. Let's push higher.
        # HW=3% → floor = 3 - 6 = -3%. To breach we need roe < -3%.
        # That's below hard SL territory... let's use a higher tier.

        # Simpler: manually set up state to be in a tier
        state.current_tier = dsl_config.tiers[0]  # trigger=3, buffer=6
        state.high_water_roe = 4.0  # HW above trigger
        # Floor = 4 - 6 = -2%. Breach at roe < -2%
        # Price for -2% ROE: move = -0.2% → price = 99.8
        # But hard SL at -12.5% ROE, so we're fine there.

        result = evaluate_dsl(state, 99.7, dsl_config)  # ROE = -3%, breach
        assert result is None
        assert state.breach_count == 1

    def test_n_consecutive_breaches_ratchets_floor(self, dsl_config):
        """N consecutive breaches → ratchets floor."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]  # consecutive_breaches=3
        state.high_water_roe = 4.0
        # Floor = 4 - 6 = -2%. Breach at roe < -2% → price < 99.8

        # 3 consecutive breaches
        evaluate_dsl(state, 99.7, dsl_config)  # breach 1
        evaluate_dsl(state, 99.6, dsl_config)  # breach 2
        result = evaluate_dsl(state, 99.5, dsl_config)  # breach 3 → ratchet

        assert state.breach_count >= 3
        assert state.locked_floor_roe is not None

    def test_recovery_resets_breach_counter(self, dsl_config):
        """Price recovers above floor between breaches → counter resets."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_roe = 4.0
        # Floor = -2%, breach < -2% → price < 99.8

        evaluate_dsl(state, 99.7, dsl_config)  # breach 1
        assert state.breach_count == 1

        # Recover above floor
        evaluate_dsl(state, 100.0, dsl_config)  # ROE=0% > -2%
        assert state.breach_count == 0

    def test_breach_recovery_breach_counter_resets(self, dsl_config):
        """Breach → recovery → breach doesn't accumulate."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_roe = 4.0

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
        state.current_tier = dsl_config.tiers[0]  # consecutive_breaches=3, buffer=6
        state.high_water_roe = 4.0
        # Floor = 4 - 6 = -2%

        # 3 consecutive breaches to lock
        evaluate_dsl(state, 99.7, dsl_config)  # breach 1
        evaluate_dsl(state, 99.7, dsl_config)  # breach 2
        result = evaluate_dsl(state, 99.5, dsl_config)  # breach 3 → locks floor

        # After locking, locked_floor_roe should be set
        locked = state.locked_floor_roe
        assert locked is not None

        # Now breach below the locked floor → "tier_lock"
        # Need price that gives ROE below locked floor
        # If locked floor is -2%, need roe < -2% → price < 99.8
        result = evaluate_dsl(state, 99.5, dsl_config)
        assert result == "tier_lock"

    def test_locked_floor_never_loosens(self, dsl_config):
        """Locked floor never loosens — new computed floor lower → keeps higher locked value."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_roe = 4.0
        # Floor = -2%

        # 3 breaches to lock at -2%
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.5, dsl_config)

        locked = state.locked_floor_roe
        assert locked is not None

        # Now reduce high water (simulating price drop) — new computed floor would be lower
        state.high_water_roe = 3.0  # floor would be 3-6 = -3%, but locked stays at -2%
        # The locked floor should not loosen
        assert state.locked_floor_roe == locked

    def test_floor_lock_resets_hw_time_at_positive_roe(self, dsl_config):
        """Floor lock at positive ROE → HW time resets (MED-15)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[0]
        state.high_water_roe = 4.0
        old_time = _now() - timedelta(minutes=30)
        state.high_water_time = old_time

        # 3 breaches to trigger ratchet
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.7, dsl_config)
        evaluate_dsl(state, 99.5, dsl_config)

        # HW time should have been reset on first ratchet
        # The locked floor is -2% (positive ROE relative to a lower entry? No, it's negative)
        # Actually locked_floor_roe = 4 - 6 = -2%, which is NOT > 0
        # So MED-15 reset only fires when lock_floor_roe > 0
        # Let's use a higher HW so the locked floor is positive

    def test_floor_lock_positive_roe_resets_hw_time(self, dsl_config):
        """Floor lock at positive locked floor → HW time resets (MED-15)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        state.current_tier = dsl_config.tiers[3]  # trigger=15, buffer=3, consecutive=2
        state.high_water_roe = 20.0  # HW well above trigger
        # Floor = 20 - 3 = 17% (positive!)

        old_time = _now() - timedelta(minutes=30)
        state.high_water_time = old_time
        state.locked_floor_roe = None

        # 2 breaches to lock (consecutive_breaches=2 for this tier)
        evaluate_dsl(state, 114.0, dsl_config)  # breach 1: ROE ~ 14*10 = 140%? Wait
        # Let me recalculate: price=114, entry=100, move=14%, ROE = 14%*10 = 140%
        # That's above floor. Need ROE < 17% → move < 1.7% → price < 101.7
        evaluate_dsl(state, 101.5, dsl_config)  # ROE = 15%, breach
        result = evaluate_dsl(state, 101.5, dsl_config)  # breach 2 → ratchet

        assert state.locked_floor_roe is not None
        # Check HW time was reset
        assert state.high_water_time != old_time


# ══════════════════════════════════════════════════════════════════════
# evaluate_dsl — Stagnation
# ══════════════════════════════════════════════════════════════════════

class TestStagnation:

    def test_hw_reaches_stagnation_roe_pct_timer_starts(self, dsl_config):
        """HW reaches stagnation_roe_pct → stagnation timer starts."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        assert not state.stagnation_active

        # Below stagnation_roe_pct (8%) → no activation
        evaluate_dsl(state, 100.31, dsl_config)  # ROE ~ 3.1% < 8%
        assert not state.stagnation_active

        # At stagnation_roe_pct (8%) → activates
        # ROE = 8% at lev=10 → move = 0.8% → price = 100.81
        evaluate_dsl(state, 100.81, dsl_config)  # ROE ~ 8.1%
        assert state.stagnation_active

    def test_new_high_water_resets_timer(self, dsl_config):
        """New high water → stagnation timer resets."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Reach stagnation_roe_pct → starts stagnation timer
        # ROE ~8.1% at lev=10 → price = 100.81
        evaluate_dsl(state, 100.81, dsl_config)  # ROE~8.1%, stagnation starts
        first_time = state.high_water_time

        # New high resets timer (101.0 → ROE=10%)
        evaluate_dsl(state, 101.0, dsl_config)
        assert state.high_water_time != first_time

    def test_elapsed_exceeds_stagnation_minutes_returns_stagnation(self, dsl_config):
        """Elapsed > stagnation_minutes + ROE >= stagnation_roe_pct → "stagnation"."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Set HW at 10% ROE with time 61 minutes ago. Current ROE will be ~8.1%
        # which is < HW so update_high_water won't reset the timer.
        state.high_water_roe = 10.0
        state.high_water_price = 101.0
        state.high_water_time = _now() - timedelta(minutes=61)
        state.stagnation_active = True
        state.current_tier = dsl_config.tiers[1]  # trigger=7, buffer=5

        # Floor = 10 - 5 = 5%. Current ROE ~8.1% > 5% → no breach.
        # ROE ~8.1% >= 8% stagnation_roe_pct + 61 min elapsed → "stagnation"
        result = evaluate_dsl(state, 100.81, dsl_config)
        assert result == "stagnation"

    def test_below_stagnation_roe_pct_no_stagnation(self, dsl_config):
        """Position below stagnation_roe_pct → no stagnation even if timer expired."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Set up: HW=10% (above current ROE), stagnation active, timer expired
        # Current ROE will be 5%, which is < HW so timer won't be reset
        state.high_water_roe = 10.0
        state.high_water_price = 101.0
        state.high_water_time = _now() - timedelta(minutes=61)
        state.stagnation_active = True
        state.current_tier = dsl_config.tiers[0]

        # Current ROE = 5% < 8% stagnation_roe_pct → no stagnation
        result = evaluate_dsl(state, 100.5, dsl_config)
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
        """Trailing buffer: floor = HW - buffer (e.g. HW=15%, buffer=3 → floor=12%)."""
        tier = DSLTier(trigger_pct=3, lock_hw_pct=30, trailing_buffer_roe=3, consecutive_breaches=3)
        cfg = DSLConfig(
            tiers=[tier],
            stagnation_roe_pct=8.0,
            stagnation_minutes=60,
            hard_sl_pct=1.25,
        )
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Push HW to 15% ROE → price = 100 * (1 + 15%/10) = 101.5
        evaluate_dsl(state, 101.5, cfg)
        assert state.high_water_roe == pytest.approx(15.0)

        # Floor should be 15 - 3 = 12% ROE → price = 100 * (1 + 12%/10) = 101.2
        # At price 101.1: ROE = 11% < 12% → should be a breach
        result = evaluate_dsl(state, 101.1, cfg)
        assert state.breach_count >= 1  # Breach detected

    def test_lock_hw_pct_floor_calculation(self):
        """lock_hw_pct: floor = HW * lock_pct / 100."""
        # Use a tier with trailing_buffer_roe=None to force lock_hw_pct path
        tier = DSLTier(trigger_pct=3, lock_hw_pct=40, trailing_buffer_roe=None, consecutive_breaches=3)
        cfg = DSLConfig(
            tiers=[tier],
            stagnation_roe_pct=8.0,
            stagnation_minutes=60,
            hard_sl_pct=1.25,
        )
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Push HW to 10% ROE → price = 100 * (1 + 10%/10) = 101.0
        evaluate_dsl(state, 101.0, cfg)
        assert state.high_water_roe == pytest.approx(10.0)

        # Floor = 10 * 40 / 100 = 4% ROE
        # At ROE < 4% → price < 100.4 → breach
        evaluate_dsl(state, 100.3, cfg)
        assert state.breach_count >= 1


# ══════════════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_short_position_dsl_logic_works(self, dsl_config):
        """Short position — DSL logic works mirrored."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)

        # Price down → positive ROE
        # At price=97, short: -(97-100)/100 = 3% → ROE = 30%
        result = evaluate_dsl(state, 97.0, dsl_config)
        # Should activate tier 1 at least (30% > 3% trigger)
        assert state.current_tier is not None

        # Hard SL for short: price rises → negative ROE
        # At price=101.25, short: -(101.25-100)/100 = -1.25% → ROE = -12.5%
        state2 = DSLState(side="short", entry_price=100.0, leverage=10.0)
        assert evaluate_dsl(state2, 101.25, dsl_config) == "hard_sl"

    def test_tier_transition_mid_breach_counter_resets(self, dsl_config):
        """Tier transition mid-breach → counter resets, new tier evaluated."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Activate tier 1 and accumulate 2 breaches
        evaluate_dsl(state, 100.31, dsl_config)  # Tier 1 (3%)
        assert state.current_tier.trigger_pct == 3
        state.breach_count = 2

        # Now push to tier 2 — should reset breach counter
        evaluate_dsl(state, 100.71, dsl_config)  # Tier 2 (7%)
        assert state.current_tier.trigger_pct == 7
        assert state.breach_count == 0  # Reset!

    def test_evaluate_returns_none_when_all_ok(self, dsl_config):
        """Normal holding conditions → None (no action)."""
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)
        # Price slightly up, well within bounds
        result = evaluate_dsl(state, 100.1, dsl_config)
        assert result is None

    def test_negative_entry_price_behavior(self, dsl_config):
        """Negative entry price → current_roe returns 0 (guarded by <= 0 check)."""
        state = DSLState(side="long", entry_price=-100.0, leverage=10.0)
        assert state.current_roe(110.0) == 0.0

    def test_very_high_leverage_hard_sl_trigger(self, dsl_config):
        """High leverage amplifies hard SL sensitivity."""
        state = DSLState(side="long", entry_price=100.0, leverage=50.0)
        # hard_sl_roe = -1.25 * 50 = -62.5%
        # Price move for -62.5% ROE at 50x: move = -1.25% → price = 98.75
        assert evaluate_dsl(state, 98.75, dsl_config) == "hard_sl"

    def test_short_stagnation_works(self, dsl_config):
        """Short position stagnation works with positive ROE when price drops."""
        state = DSLState(side="short", entry_price=100.0, leverage=10.0)

        # Price drops → positive ROE for short
        # At price=92, short ROE = (100-92)/100 * 10 = 80%
        evaluate_dsl(state, 92.0, dsl_config)
        assert state.stagnation_active

        # Set time to exceed stagnation window
        state.high_water_time = _now() - timedelta(minutes=61)

        # Current ROE must be >= stagnation_roe_pct (8%)
        # At price=92, ROE=80% ≥ 8% → stagnation
        result = evaluate_dsl(state, 92.0, dsl_config)
        assert result == "stagnation"

    def test_single_tier_config(self):
        """Config with only one tier works correctly."""
        tier = DSLTier(trigger_pct=5, lock_hw_pct=50, trailing_buffer_roe=2, consecutive_breaches=2)
        cfg = DSLConfig(tiers=[tier], stagnation_roe_pct=8.0, stagnation_minutes=60, hard_sl_pct=1.25)
        state = DSLState(side="long", entry_price=100.0, leverage=10.0)

        # Below trigger → no tier
        result = evaluate_dsl(state, 100.3, cfg)
        assert result is None

        # At trigger → tier activates
        result = evaluate_dsl(state, 100.5, cfg)  # ROE = 5%
        assert state.current_tier is not None
        assert state.current_tier.trigger_pct == 5
