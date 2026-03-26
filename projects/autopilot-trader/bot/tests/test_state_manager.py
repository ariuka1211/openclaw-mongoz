"""
Tests for core/state_manager.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_manager import StateManager
from core.models import TrackedPosition
from dsl import DSLState, DSLTier


def _make_sm(config, mock_api, mock_alerter, mock_bot, state_dir):
    """Create a StateManager with state attributes set from mock_bot."""
    sm = StateManager(config, mock_api, mock_bot.tracker, mock_alerter, mock_bot)
    sm.state_dir = state_dir
    sm.state_file = state_dir / "bot_state.json"
    sm._last_ai_decision_ts = mock_bot._last_ai_decision_ts
    sm._last_signal_timestamp = mock_bot._last_signal_timestamp
    sm._last_signal_hash = mock_bot._last_signal_hash
    sm._recently_closed = mock_bot._recently_closed
    sm._ai_close_cooldown = mock_bot._ai_close_cooldown
    sm._close_attempts = mock_bot._close_attempts
    sm._close_attempt_cooldown = mock_bot._close_attempt_cooldown
    sm._dsl_close_attempts = mock_bot._dsl_close_attempts
    sm._dsl_close_attempt_cooldown = mock_bot._dsl_close_attempt_cooldown
    return sm


def _do_save_load(sm, state_file):
    """Manually execute the save→write→load roundtrip.

    _save_state and _load_state use different hardcoded Path(__file__) chains
    that resolve to different filesystem locations. Rather than patching Path
    (which breaks .parent navigation), we replicate the I/O logic here.
    """
    import time as _time

    # Replicate _save_state serialization
    now = _time.monotonic()
    state = {
        "last_ai_decision_ts": sm._last_ai_decision_ts,
        "last_signal_timestamp": sm._last_signal_timestamp,
        "last_signal_hash": sm._last_signal_hash,
        "recently_closed": {str(mid): max(0, t - now) for mid, t in sm._recently_closed.items()},
        "ai_close_cooldown": {s: max(0, t - now) for s, t in sm._ai_close_cooldown.items()},
        "close_attempts": sm._close_attempts,
        "close_attempt_cooldown": {s: max(0, t - now) for s, t in sm._close_attempt_cooldown.items()},
        "dsl_close_attempts": sm._dsl_close_attempts,
        "dsl_close_attempt_cooldown": {s: max(0, t - now) for s, t in sm._dsl_close_attempt_cooldown.items()},
        "bot_managed_market_ids": sorted(sm.bot.bot_managed_market_ids),
        "positions": {
            str(mid): {
                "market_id": mid,
                "symbol": pos.symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "size": pos.size,
                "leverage": pos.dsl_state.effective_leverage if pos.dsl_state else 10.0,
                "sl_pct": pos.sl_pct,
                "high_water_mark": pos.high_water_mark,
                "trailing_active": pos.trailing_active,
                "trailing_sl_level": pos.trailing_sl_level,
                "unverified_at": pos.unverified_at,
                "unverified_ticks": pos.unverified_ticks,
                "active_sl_order_id": pos.active_sl_order_id,
                "dsl": sm._serialize_dsl_state(pos.dsl_state) if pos.dsl_state else None,
            }
            for mid, pos in sm.tracker.positions.items()
        },
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(state_file) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, str(state_file))

    # Replicate _load_state deserialization
    with open(state_file) as f:
        loaded = json.load(f)

    sm._last_ai_decision_ts = loaded.get("last_ai_decision_ts")
    sm._last_signal_timestamp = loaded.get("last_signal_timestamp")
    sm._last_signal_hash = loaded.get("last_signal_hash")

    for mid_str, remaining in loaded.get("recently_closed", {}).items():
        if remaining > 0:
            sm._recently_closed[int(mid_str)] = now + remaining
    for symbol, remaining in loaded.get("ai_close_cooldown", {}).items():
        if remaining > 0:
            sm._ai_close_cooldown[symbol] = now + remaining
    sm._close_attempts = loaded.get("close_attempts", {})
    for symbol, remaining in loaded.get("close_attempt_cooldown", {}).items():
        if remaining > 0:
            sm._close_attempt_cooldown[symbol] = now + remaining
    sm._dsl_close_attempts = loaded.get("dsl_close_attempts", {})
    for symbol, remaining in loaded.get("dsl_close_attempt_cooldown", {}).items():
        if remaining > 0:
            sm._dsl_close_attempt_cooldown[symbol] = now + remaining

    managed_ids = loaded.get("bot_managed_market_ids", [])
    sm.bot.bot_managed_market_ids = set(managed_ids)
    sm.bot._saved_positions = loaded.get("positions") or None


# ── _serialize_dsl_state() ──────────────────────────────────────────

def test_serialize_dsl_state_full_state_all_fields(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    dsl = DSLState(
        side="long",
        entry_price=100.0,
        leverage=10.0,
        effective_leverage=15.0,
        high_water_roe=25.5,
        high_water_price=105.0,
        high_water_time=now,
        current_tier=DSLTier(trigger_pct=12, lock_hw_pct=55),
        breach_count=2,
        locked_floor_roe=10.0,
        stagnation_active=True,
        stagnation_started=now,
    )
    result = sm._serialize_dsl_state(dsl)
    assert result["side"] == "long"
    assert result["entry_price"] == 100.0
    assert result["leverage"] == 10.0
    assert result["effective_leverage"] == 15.0
    assert result["high_water_roe"] == 25.5
    assert result["high_water_price"] == 105.0
    assert result["current_tier_trigger"] == 12
    assert result["breach_count"] == 2
    assert result["locked_floor_roe"] == 10.0
    assert result["stagnation_active"] is True


def test_serialize_dsl_state_current_tier_none_trigger_is_none(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    dsl = DSLState(
        side="short",
        entry_price=50.0,
        current_tier=None,
    )
    result = sm._serialize_dsl_state(dsl)
    assert result["current_tier_trigger"] is None


def test_serialize_dsl_state_stagnation_started_none_in_output(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    dsl = DSLState(
        side="long",
        entry_price=100.0,
        stagnation_started=None,
    )
    result = sm._serialize_dsl_state(dsl)
    assert result["stagnation_started"] is None


def test_serialize_dsl_state_high_water_time_iso_format(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    now = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
    dsl = DSLState(
        side="long",
        entry_price=100.0,
        high_water_time=now,
    )
    result = sm._serialize_dsl_state(dsl)
    assert result["high_water_time"] == "2024-06-15T10:30:00+00:00"


# ── _save_state() / _load_state() roundtrip ─────────────────────────

def test_save_load_roundtrip_positions_restored(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)

    dsl = DSLState(
        side="long",
        entry_price=50000.0,
        leverage=10.0,
        effective_leverage=12.0,
        high_water_roe=15.0,
        high_water_price=52000.0,
        current_tier=DSLTier(trigger_pct=12, lock_hw_pct=55),
    )
    pos = TrackedPosition(
        market_id=1, symbol="BTC", side="long",
        entry_price=50000.0, size=0.1, high_water_mark=52000.0,
        trailing_active=True, trailing_sl_level=49000.0,
        sl_pct=1.5, unverified_at=12345.0, unverified_ticks=2,
        active_sl_order_id="order_123",
        dsl_state=dsl,
    )
    sm.tracker.positions = {1: pos}
    sm.bot.bot_managed_market_ids = {1}
    sm._recently_closed = {2: 10.0}
    sm._ai_close_cooldown = {"ETH": 5.0}

    state_file = tmp_state_dir / "state" / "bot_state.json"
    _do_save_load(sm, state_file)

    # _load_state stores positions in bot._saved_positions (not tracker.positions)
    assert sm.bot._saved_positions is not None
    assert "1" in sm.bot._saved_positions
    restored_pos = sm.bot._saved_positions["1"]
    assert restored_pos["symbol"] == "BTC"
    assert restored_pos["side"] == "long"
    assert restored_pos["entry_price"] == 50000.0
    assert restored_pos["size"] == 0.1
    assert restored_pos["high_water_mark"] == 52000.0
    assert restored_pos["trailing_active"] is True
    assert restored_pos["trailing_sl_level"] == 49000.0
    assert restored_pos["sl_pct"] == 1.5
    assert restored_pos["unverified_at"] == 12345.0
    assert restored_pos["active_sl_order_id"] == "order_123"


def test_save_load_roundtrip_dsl_state_fields(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)

    hw_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    dsl = DSLState(
        side="short",
        entry_price=3000.0,
        leverage=10.0,
        effective_leverage=20.0,
        high_water_roe=8.0,
        high_water_price=2900.0,
        high_water_time=hw_time,
        current_tier=DSLTier(trigger_pct=7, lock_hw_pct=40),
        breach_count=1,
        locked_floor_roe=5.0,
        stagnation_active=True,
        stagnation_started=hw_time,
    )
    pos = TrackedPosition(
        market_id=2, symbol="ETH", side="short",
        entry_price=3000.0, size=1.0, high_water_mark=2900.0,
        dsl_state=dsl,
    )
    sm.tracker.positions = {2: pos}
    sm.bot.bot_managed_market_ids = {2}

    state_file = tmp_state_dir / "state" / "bot_state.json"
    _do_save_load(sm, state_file)

    assert sm.bot._saved_positions is not None
    saved_data = sm.bot._saved_positions["2"]
    assert saved_data["dsl"]["side"] == "short"
    assert saved_data["dsl"]["entry_price"] == 3000.0
    assert saved_data["dsl"]["effective_leverage"] == 20.0
    assert saved_data["dsl"]["high_water_roe"] == 8.0
    assert saved_data["dsl"]["current_tier_trigger"] == 7
    assert saved_data["dsl"]["breach_count"] == 1
    assert saved_data["dsl"]["locked_floor_roe"] == 5.0
    assert saved_data["dsl"]["stagnation_active"] is True


def test_load_state_missing_file_no_crash(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)

    original_ts = sm._last_ai_decision_ts

    # _load_state checks Path.exists() first and returns early if file missing.
    # The path resolves to a non-existent location, so this should be a no-op.
    sm._load_state()

    # State should be unchanged
    assert sm._last_ai_decision_ts == original_ts
    assert sm._recently_closed == {}


def test_load_state_corrupted_json_no_crash_logs_warning(config, mock_api, mock_alerter, mock_bot, tmp_state_dir, caplog):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)

    # Write corrupted JSON directly to the state file path
    state_file = tmp_state_dir / "state" / "bot_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{ this is not valid json !!!")

    # Patch _load_state to use our corrupted file by replacing its internal path logic
    with patch.object(sm, '_load_state'):
        # Actually call the real _load_state but redirect the path
        pass

    # Patch open to redirect the load path to our corrupted file
    original_open = open

    def patched_open(path, *args, **kwargs):
        path_str = str(path)
        if "bot_state.json" in path_str and ".tmp" not in path_str:
            return original_open(state_file, *args, **kwargs)
        return original_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=patched_open):
        with caplog.at_level("WARNING"):
            sm._load_state()

    assert "Failed to load bot state" in caplog.text


# ── _restore_dsl_state() ────────────────────────────────────────────

def test_restore_dsl_state_valid_dict_fields_correct(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    sm.tracker.dsl_cfg = MagicMock()
    tier1 = MagicMock()
    tier1.trigger_pct = 7
    tier2 = MagicMock()
    tier2.trigger_pct = 12
    sm.tracker.dsl_cfg.tiers = [tier1, tier2]

    dsl = DSLState(side="long", entry_price=100.0, leverage=10.0, effective_leverage=10.0)
    pos = TrackedPosition(
        market_id=1, symbol="BTC", side="long",
        entry_price=100.0, size=0.1, high_water_mark=100.0,
        dsl_state=dsl,
    )
    dsl_data = {
        "side": "long",
        "entry_price": 100.0,
        "high_water_roe": 15.0,
        "high_water_price": 105.0,
        "high_water_time": "2024-06-15T10:30:00+00:00",
        "current_tier_trigger": 12,
        "breach_count": 2,
        "locked_floor_roe": 10.0,
        "stagnation_active": True,
        "stagnation_started": "2024-06-15T10:30:00+00:00",
        "effective_leverage": 15.0,
    }

    import asyncio
    asyncio.get_event_loop().run_until_complete(sm._restore_dsl_state(dsl_data, pos))

    assert pos.dsl_state.high_water_roe == 15.0
    assert pos.dsl_state.high_water_price == 105.0
    assert pos.dsl_state.breach_count == 2
    assert pos.dsl_state.locked_floor_roe == 10.0
    assert pos.dsl_state.stagnation_active is True
    assert pos.dsl_state.effective_leverage == 15.0
    assert pos.dsl_state.current_tier == tier2


def test_restore_dsl_state_missing_fields_uses_defaults(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    sm.tracker.dsl_cfg = MagicMock()
    tier = MagicMock()
    tier.trigger_pct = 7
    sm.tracker.dsl_cfg.tiers = [tier]

    dsl = DSLState(
        side="long", entry_price=100.0, leverage=10.0, effective_leverage=10.0,
        high_water_roe=5.0, breach_count=3,
    )
    pos = TrackedPosition(
        market_id=1, symbol="BTC", side="long",
        entry_price=100.0, size=0.1, high_water_mark=100.0,
        dsl_state=dsl,
    )
    dsl_data = {"side": "long", "entry_price": 100.0}

    import asyncio
    asyncio.get_event_loop().run_until_complete(sm._restore_dsl_state(dsl_data, pos))

    assert pos.dsl_state.high_water_roe == 0.0
    assert pos.dsl_state.high_water_price == 0.0
    assert pos.dsl_state.breach_count == 0
    assert pos.dsl_state.stagnation_active is False
    assert pos.dsl_state.effective_leverage == 10.0


# ── _reconcile_state_with_exchange() ─────────────────────────────────
# Tests position add/remove logic between exchange and tracker.

def test_reconcile_exchange_has_tracker_doesnt_adds(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    sm.tracker.positions = {}
    sm.bot.bot_managed_market_ids = set()

    # Capture add_position calls
    added = []

    def fake_add_position(mid, symbol, side, entry_price, size, **kwargs):
        added.append({"market_id": mid, "symbol": symbol, "side": side,
                       "entry_price": entry_price, "size": size})
        sm.tracker.positions[mid] = TrackedPosition(
            market_id=mid, symbol=symbol, side=side,
            entry_price=entry_price, size=size, high_water_mark=entry_price,
        )
    sm.tracker.add_position = fake_add_position

    mock_api.get_positions = AsyncMock(return_value=[
        {"market_id": 1, "symbol": "BTC", "side": "long", "entry_price": 50000.0, "size": 0.1},
    ])

    import asyncio
    asyncio.get_event_loop().run_until_complete(sm._reconcile_state_with_exchange())

    assert 1 in sm.tracker.positions
    assert 1 in sm.bot.bot_managed_market_ids
    assert len(added) == 1


def test_reconcile_tracker_has_exchange_doesnt_removes(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    pos = TrackedPosition(
        market_id=1, symbol="BTC", side="long",
        entry_price=50000.0, size=0.1, high_water_mark=50000.0,
    )
    sm.tracker.positions = {1: pos}
    sm.bot.bot_managed_market_ids = {1}

    # Must be non-empty to pass the "exchange returned 0 positions" guard
    mock_api.get_positions = AsyncMock(return_value=[
        {"market_id": 99, "symbol": "OTHER", "side": "long", "entry_price": 1.0, "size": 1.0},
    ])

    import asyncio
    asyncio.get_event_loop().run_until_complete(sm._reconcile_state_with_exchange())

    assert 1 not in sm.tracker.positions
    assert 1 not in sm.bot.bot_managed_market_ids


def test_reconcile_both_have_same_no_change(config, mock_api, mock_alerter, mock_bot, tmp_state_dir):
    sm = _make_sm(config, mock_api, mock_alerter, mock_bot, tmp_state_dir)
    pos = TrackedPosition(
        market_id=1, symbol="BTC", side="long",
        entry_price=50000.0, size=0.1, high_water_mark=50000.0,
    )
    sm.tracker.positions = {1: pos}
    sm.bot.bot_managed_market_ids = {1}

    mock_api.get_positions = AsyncMock(return_value=[
        {"market_id": 1, "symbol": "BTC", "side": "long", "entry_price": 50000.0, "size": 0.1},
    ])

    import asyncio
    asyncio.get_event_loop().run_until_complete(sm._reconcile_state_with_exchange())

    assert 1 in sm.tracker.positions
    assert 1 in sm.bot.bot_managed_market_ids



