"""
State persistence, load, and exchange reconciliation.

Handles save/load of bot state, DSL state restoration,
and startup reconciliation with the exchange.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from config import BotConfig
from core.models import TrackedPosition
from api.lighter_api import LighterAPI
from core.position_tracker import PositionTracker
from dsl import DSLState


class StateManager:
    def __init__(self, cfg: BotConfig, api: LighterAPI, tracker: PositionTracker, alerter, bot):
        """Initialize state manager.

        Args:
            cfg: Bot configuration
            api: LighterAPI instance
            tracker: PositionTracker instance
            alerter: TelegramAlerter instance
            bot: LighterCopilot reference (for state, alerts, bot_managed_market_ids)
        """
        self.cfg = cfg
        self.api = api
        self.tracker = tracker
        self.alerter = alerter
        self.bot = bot

    def _serialize_dsl_state(self, dsl: DSLState) -> dict:
        """Serialize DSLState to a JSON-compatible dict."""
        return {
            "side": dsl.side,
            "entry_price": dsl.entry_price,
            "leverage": dsl.leverage,
            "high_water_roe": dsl.high_water_roe,
            "high_water_price": dsl.high_water_price,
            "high_water_time": dsl.high_water_time.isoformat() if dsl.high_water_time else None,
            "current_tier_trigger": dsl.current_tier.trigger_pct if dsl.current_tier else None,
            "breach_count": dsl.breach_count,
            "locked_floor_roe": dsl.locked_floor_roe,
            "stagnation_active": dsl.stagnation_active,
            "stagnation_started": dsl.stagnation_started.isoformat() if dsl.stagnation_started else None,
        }




    def _save_state(self):
        """Persist critical ephemeral state to disk for crash/restart recovery."""
        now = time.monotonic()
        state = {
            "last_ai_decision_ts": self.bot._last_ai_decision_ts,
            "last_signal_timestamp": self.bot._last_signal_timestamp,
            "last_signal_hash": self.bot._last_signal_hash,
            # Convert monotonic deadlines to remaining seconds for portability
            "recently_closed": {str(mid): max(0, t - now) for mid, t in self.bot._recently_closed.items()},
            "ai_close_cooldown": {s: max(0, t - now) for s, t in self.bot._ai_close_cooldown.items()},
            "close_attempts": self.bot._close_attempts,
            "close_attempt_cooldown": {s: max(0, t - now) for s, t in self.bot._close_attempt_cooldown.items()},
            "dsl_close_attempts": self.bot._dsl_close_attempts,
            "dsl_close_attempt_cooldown": {s: max(0, t - now) for s, t in self.bot._dsl_close_attempt_cooldown.items()},
            # BUG-07: Which market_ids were opened by the bot
            "bot_managed_market_ids": sorted(self.bot.bot_managed_market_ids),
            # Persist position state + DSL state for restart recovery
            "positions": {
                str(mid): {
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "size": pos.size,
                    "leverage": pos.dsl_state.leverage if pos.dsl_state else self.cfg.dsl_leverage,
                    "sl_pct": pos.sl_pct,
                    "high_water_mark": pos.high_water_mark,
                    "trailing_sl_activated": pos.trailing_sl_activated,
                    "trailing_sl_level": pos.trailing_sl_level,
                    "unverified_at": pos.unverified_at,
                    "unverified_ticks": pos.unverified_ticks,
                    "active_sl_order_id": pos.active_sl_order_id,  # MED-18
                    "dsl": self._serialize_dsl_state(pos.dsl_state) if pos.dsl_state else None,
                }
                for mid, pos in self.tracker.positions.items()
            },
        }
        try:
            state_path = Path(__file__).parent / "state" / "bot_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(state_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, str(state_path))
        except Exception as e:
            logging.debug(f"Failed to save bot state: {e}")



    def _load_state(self):
        """Restore critical ephemeral state from disk after restart."""
        state_path = Path(__file__).parent / "state" / "bot_state.json"
        if not state_path.exists():
            return
        try:
            with open(state_path) as f:
                state = json.load(f)
            now = time.monotonic()

            self.bot._last_ai_decision_ts = state.get("last_ai_decision_ts")
            self.bot._last_signal_timestamp = state.get("last_signal_timestamp")
            self.bot._last_signal_hash = state.get("last_signal_hash")

            # Convert remaining seconds back to monotonic deadlines
            for mid_str, remaining in state.get("recently_closed", {}).items():
                if remaining > 0:
                    self.bot._recently_closed[int(mid_str)] = now + remaining

            for symbol, remaining in state.get("ai_close_cooldown", {}).items():
                if remaining > 0:
                    self.bot._ai_close_cooldown[symbol] = now + remaining

            self.bot._close_attempts = state.get("close_attempts", {})

            for symbol, remaining in state.get("close_attempt_cooldown", {}).items():
                if remaining > 0:
                    self.bot._close_attempt_cooldown[symbol] = now + remaining

            self.bot._dsl_close_attempts = state.get("dsl_close_attempts", {})

            for symbol, remaining in state.get("dsl_close_attempt_cooldown", {}).items():
                if remaining > 0:
                    self.bot._dsl_close_attempt_cooldown[symbol] = now + remaining

            # BUG-07: Load bot-managed market IDs
            managed_ids = state.get("bot_managed_market_ids", [])
            self.bot.bot_managed_market_ids = set(managed_ids)

            # If managed market IDs were lost but we have saved positions, reconstruct
            if not self.bot.bot_managed_market_ids and state.get("positions"):
                for mid_str in state["positions"]:
                    try:
                        self.bot.bot_managed_market_ids.add(int(mid_str))
                    except (ValueError, TypeError):
                        pass
                if self.bot.bot_managed_market_ids:
                    logging.warning(f"Reconstructed bot_managed_market_ids from saved positions: {sorted(self.bot.bot_managed_market_ids)}")

            # Load saved positions for DSL state restoration (applied after exchange detection)
            self.bot._saved_positions = state.get("positions") or None

            restored = []
            if self.bot._last_ai_decision_ts:
                restored.append(f"ai_decision_ts={self.bot._last_ai_decision_ts}")
            if self.bot._recently_closed:
                restored.append(f"recently_closed={len(self.bot._recently_closed)}")
            if self.bot._ai_close_cooldown:
                restored.append(f"ai_close_cooldown={len(self.bot._ai_close_cooldown)}")
            if self.bot._close_attempts:
                restored.append(f"close_attempts={len(self.bot._close_attempts)}")
            if self.bot._saved_positions:
                restored.append(f"saved_positions={len(self.bot._saved_positions)}")
            if restored:
                logging.info(f"🔄 Bot state restored: {', '.join(restored)}")

            # If we restored a last decision timestamp, check if the current decision
            # is the same one we were processing before the crash.
            # - Same timestamp → ACK to unblock AI trader (post-crash unblock, from IPC fix)
            # - Different timestamp → new decision arrived during downtime, skip ACK
            #   so _tick() processes it normally (otherwise the decision is lost)
            if self.bot._last_ai_decision_ts:
                try:
                    current_decision = safe_read_json(Path(self.bot._ai_decision_file))
                    if current_decision and current_decision.get("timestamp") == self.bot._last_ai_decision_ts:
                        # Same decision bot was processing before crash — ACK to unblock AI trader
                        ack_path = str(Path(self.bot._ai_decision_file)) + ".ack"
                        decision_id = current_decision.get("decision_id", "")
                        with open(ack_path, "w") as f:
                            f.write(decision_id)
                        logging.info(f"🔓 Post-crash ACK written for decision {decision_id} (same decision, unblocking AI trader)")
                    elif current_decision:
                        logging.info("⏸️ New decision arrived during downtime — skipping ACK, will process on first tick")
                    else:
                        logging.info("⏸️ No decision file found — skipping ACK")
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"Failed to load bot state: {e}")



    async def _reconcile_state_with_exchange(self):
        """Reconcile bot state with actual exchange positions on startup.

        Ensures bot_managed_market_ids and tracker.positions match what's on the exchange.
        Prevents drift from crashes where state was saved but exchange state diverged.

        - Exchange positions not in state → adopted with fresh DSL state
        - State positions not on exchange → removed
        - Positions in both → validated and updated if stale
        """
        if not self.api:
            logging.warning("⚠️ Reconciliation skipped: API not initialized")
            return

        try:
            # Fetch current positions from exchange
            live_positions = await self.api.get_positions()

            # Handle API failure gracefully — don't crash startup
            if live_positions is None:
                logging.warning("⚠️ Reconciliation skipped: failed to fetch positions from exchange")
                return

            # Safety: if exchange returns empty but we have positions in state,
            # don't remove them — likely proxy/connection not ready yet on first call.
            # Only reconcile removals when exchange actually returns data.
            state_mids = set(self.bot.bot_managed_market_ids)
            if not live_positions and state_mids:
                logging.warning(
                    f"⚠️ Reconciliation: exchange returned 0 positions but state has "
                    f"{len(state_mids)} — skipping removal (proxy may not be ready)"
                )
                # Still try to save any adopted positions, just don't remove
                # Since there are no exchange positions, there's nothing to adopt either
                return

            # Build market ID sets
            exchange_mids = {p["market_id"] for p in live_positions}

            # Track changes for summary
            adopted = 0
            removed = 0
            confirmed = 0
            updated = 0
            changed = False

            # Exchange positions NOT in state → adopt them
            for pos_data in live_positions:
                mid = pos_data["market_id"]
                if mid not in state_mids:
                    # Adopt this position — it exists on exchange but not in our state
                    self.bot.bot_managed_market_ids.add(mid)
                    self.tracker.add_position(
                        mid,
                        pos_data["symbol"],
                        pos_data["side"],
                        pos_data["entry_price"],
                        pos_data["size"],
                        leverage=pos_data.get("leverage"),
                    )
                    adopted += 1
                    changed = True
                    logging.info(
                        f"📥 Reconciled adopted: {pos_data['side'].upper()} {pos_data['symbol']} "
                        f"@ ${pos_data['entry_price']:,.2f}"
                    )
                else:
                    # Position exists in both — validate and update if stale
                    existing = self.tracker.positions.get(mid)
                    if existing:
                        # Update size if stale (use exchange as source of truth)
                        if abs(existing.size - pos_data["size"]) > 0.001:
                            logging.info(
                                f"🔄 Reconciled updated size: {pos_data['symbol']} "
                                f"state={existing.size} → exchange={pos_data['size']}"
                            )
                            existing.size = pos_data["size"]
                            updated += 1
                            changed = True
                        else:
                            confirmed += 1

            # State positions NOT on exchange → remove them
            for mid in list(state_mids):
                if mid not in exchange_mids:
                    # Remove from state
                    self.bot.bot_managed_market_ids.discard(mid)
                    self.tracker.positions.pop(mid, None)
                    removed += 1
                    changed = True
                    logging.info(f"🗑️ Reconciled removed: market_id={mid} (no longer on exchange)")

            # Save state if any changes were made
            if changed:
                self._save_state()
                logging.info(
                    f"🔄 Reconciled: +{adopted} adopted, -{removed} removed, "
                    f"={confirmed} confirmed, ~{updated} updated"
                )
            else:
                logging.info(f"✅ Reconciled: all {confirmed} positions match exchange")

        except Exception as e:
            # Never crash startup due to reconciliation failure
            logging.warning(f"⚠️ Reconciliation failed (non-fatal): {e}")
            # Continue with normal startup — _tick() will handle position sync



    async def _restore_dsl_state(self, dsl_data: dict, pos: TrackedPosition):
        """Restore saved DSL state onto a live TrackedPosition.

        Overwrites all tracked fields on the existing DSLState from saved data.
        The DSLState object is already constructed by tracker.add_position() with
        correct entry_price/side/leverage — we just restore the progress fields.
        """
        if not dsl_data or not pos.dsl_state:
            return
        try:
            saved_tier_trigger = dsl_data.get("current_tier_trigger")
            tier = None
            if saved_tier_trigger is not None:
                for t in self.tracker.dsl_cfg.tiers:
                    if t.trigger_pct == saved_tier_trigger:
                        tier = t
                        break
                # MED-24: Default to first tier on mismatch instead of None
                if tier is None and self.tracker.dsl_cfg.tiers:
                    tier = self.tracker.dsl_cfg.tiers[0]
                    logging.warning(
                        f"⚠️ Saved tier trigger {saved_tier_trigger}% not found in current config, "
                        f"defaulting to first tier ({tier.trigger_pct}%)"
                    )

            saved_hw_time = dsl_data.get("high_water_time")
            hw_time = datetime.fromisoformat(saved_hw_time) if saved_hw_time else None
            saved_stag_time = dsl_data.get("stagnation_started")
            stag_time = datetime.fromisoformat(saved_stag_time) if saved_stag_time else None

            pos.dsl_state.high_water_roe = dsl_data.get("high_water_roe", 0.0)
            pos.dsl_state.high_water_price = dsl_data.get("high_water_price", 0.0)
            pos.dsl_state.high_water_time = hw_time
            pos.dsl_state.current_tier = tier
            pos.dsl_state.breach_count = dsl_data.get("breach_count", 0)
            pos.dsl_state.locked_floor_roe = dsl_data.get("locked_floor_roe")
            pos.dsl_state.stagnation_active = dsl_data.get("stagnation_active", False)
            pos.dsl_state.stagnation_started = stag_time
            # Backward compat: old state files may have effective_leverage (≈0.1) — ignore if < 1.0
            saved_lev = dsl_data.get("leverage", self.cfg.dsl_leverage)
            if saved_lev < 1.0:
                saved_lev = self.cfg.dsl_leverage
            pos.dsl_state.leverage = saved_lev

            logging.info(
                f"🔄 Restored DSL state for {pos.symbol}: "
                f"HW_ROE={pos.dsl_state.high_water_roe:+.1f}%, "
                f"Tier={tier.trigger_pct if tier else 'none'}, "
                f"Floor={pos.dsl_state.locked_floor_roe}, "
                f"Breaches={pos.dsl_state.breach_count}"
            )
        except Exception as e:
            logging.warning(f"Failed to restore DSL state for {pos.symbol}: {e}")
            try:
                await self.alerter.send(f"⚠️ *DSL State Lost:* {pos.symbol}\nRestart reset tier progress. Starting fresh.")
            except Exception:
                pass



    async def _reconcile_positions(self, live_positions: list[dict] | None):
        """Reconcile saved positions with live exchange positions.

        Called once per tick (no-op after first successful reconciliation).
        Restores saved DSLState for positions that match the exchange.

        Saved but not on exchange → dropped (position was closed).
        On exchange but not saved → stays with fresh DSLState (new position).
        Both → DSLState restored from saved data.
        """
        if not self.bot._saved_positions or live_positions is None:
            return

        live_mids = {p["market_id"] for p in live_positions}

        for mid_str, saved_pos in self.bot._saved_positions.items():
            try:
                mid = int(mid_str)
            except (ValueError, TypeError):
                continue

            if mid in live_mids and mid in self.tracker.positions:
                # Position exists on both exchange and tracker — restore DSL state
                pos = self.tracker.positions[mid]
                dsl_data = saved_pos.get("dsl")
                if dsl_data:
                    await self._restore_dsl_state(dsl_data, pos)
                # Also restore legacy trailing state
                if saved_pos.get("trailing_sl_level") is not None:
                    pos.trailing_sl_level = saved_pos["trailing_sl_level"]
                if saved_pos.get("trailing_sl_activated"):
                    pos.trailing_sl_activated = True
                # Backward compat: migrate old field
                elif saved_pos.get("trailing_active"):
                    pos.trailing_sl_activated = True
                if saved_pos.get("high_water_mark"):
                    pos.high_water_mark = max(pos.high_water_mark, saved_pos["high_water_mark"])
                # Restore AI-specified stop loss % (Fix #10)
                if saved_pos.get("sl_pct") is not None:
                    pos.sl_pct = saved_pos["sl_pct"]
                # CRITICAL-2: Restore unverified state (reset ticks to 1 on restart)
                if saved_pos.get("unverified_at") is not None:
                    pos.unverified_at = time.time()
                    pos.unverified_ticks = 1  # Reset count on restart
                    logging.info(f"🔄 Restored unverified state for {pos.symbol} (tick 1/3)")
                # MED-18: Restore active SL order ID for cancellation
                if saved_pos.get("active_sl_order_id"):
                    pos.active_sl_order_id = saved_pos["active_sl_order_id"]
            elif mid not in live_mids:
                logging.info(f"🗑️ Saved position {saved_pos.get('symbol', mid)} no longer on exchange — dropped")

        # Clear after reconciliation (one-time operation per restart)
        self.bot._saved_positions = None







