"""
Position tracker with DSL (Dynamic Stop Loss) and legacy trailing TP/SL.

Manages per-position state, high-water marks, and trigger evaluation.
"""

import logging
from datetime import datetime, timezone

from config import BotConfig
from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl
from core.models import TrackedPosition


class PositionTracker:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.positions: dict[int, TrackedPosition] = {}  # key: market_id
        self.account_equity: float = 0.0  # Cached account equity for cross margin ROE
        # Build DSL config from bot config
        self.dsl_cfg = DSLConfig(
            stagnation_roe_pct=cfg.stagnation_roe_pct,
            stagnation_minutes=cfg.stagnation_minutes,
            hard_sl_pct=cfg.hard_sl_pct,
        )
        # Parse custom tiers from config if present
        if cfg.dsl_tiers:
            self.dsl_cfg.tiers = [
                DSLTier(
                    trigger_pct=t.get("trigger_pct", 7),
                    lock_hw_pct=t.get("lock_hw_pct", 40),
                    trailing_buffer_roe=t.get("trailing_buffer_roe"),
                    consecutive_breaches=t.get("consecutive_breaches", 3),
                )
                for t in cfg.dsl_tiers
            ]

    def compute_tp_price(self, pos: TrackedPosition) -> float | None:
        """Calculate trailing take-profit price based on high-water mark.
        
        Returns None if:
        - For longs: high-water mark hasn't exceeded trigger level yet
        - For shorts: high-water mark hasn't dropped below trigger level yet (at entry, returns None)
        """
        if pos.side == "long":
            trigger = pos.entry_price * (1 + self.cfg.trailing_tp_trigger_pct / 100)
            if pos.high_water_mark < trigger:
                return None
            return pos.high_water_mark * (1 - self.cfg.trailing_tp_delta_pct / 100)
        else:
            trigger = pos.entry_price * (1 - self.cfg.trailing_tp_trigger_pct / 100)
            if pos.high_water_mark > trigger:
                return None
            return pos.high_water_mark * (1 + self.cfg.trailing_tp_delta_pct / 100)

    def compute_sl_price(self, pos: TrackedPosition) -> float:
        """Return trailing stop loss level. Trails upward on longs, downward on shorts."""
        sl_pct = self._get_sl_pct(pos)
        if pos.trailing_sl_level is not None:
            return pos.trailing_sl_level
        return pos.entry_price * (1 - sl_pct / 100 if pos.side == "long" else 1 + sl_pct / 100)

    def _get_sl_pct(self, pos: TrackedPosition) -> float:
        """Get effective stop loss % — per-position (AI) or config default."""
        return pos.sl_pct if pos.sl_pct is not None else self.cfg.hard_sl_pct

    def update_price(self, market_id: int, price: float) -> str | None:
        pos = self.positions.get(market_id)
        if not pos:
            return None

        # ── DSL mode: tiered trailing stop loss ──
        if self.cfg.dsl_enabled and pos.dsl_state:
            result = evaluate_dsl(pos.dsl_state, price, self.dsl_cfg)
            roe = pos.dsl_state.current_roe(price)
            if result:
                floor_str = (f"Floor: {pos.dsl_state.locked_floor_roe:+.1f}%"
                             if pos.dsl_state.locked_floor_roe else "")
                tier_str = (str(pos.dsl_state.current_tier.trigger_pct)
                            if pos.dsl_state.current_tier else "none")
                logging.info(
                    f"🛑 {pos.symbol} DSL {result} | "
                    f"ROE: {roe:+.1f}% | HW: {pos.dsl_state.high_water_roe:+.1f}% | "
                    f"Tier: {tier_str} | {floor_str}"
                )
                return result

            # Log DSL state periodically (when tier changes or HW updates)
            if pos.dsl_state.current_tier:
                logging.debug(
                    f"📊 {pos.symbol} DSL | ROE: {roe:+.1f}% | "
                    f"HW: {pos.dsl_state.high_water_roe:+.1f}% | "
                    f"Tier: {pos.dsl_state.current_tier.trigger_pct}% | "
                    f"Breaches: {pos.dsl_state.breach_count}/{pos.dsl_state.current_tier.consecutive_breaches}"
                )

            # Alert on tier lock
            if pos.dsl_state.locked_floor_roe is not None and not pos.tier_lock_alerted:
                pos.tier_lock_alerted = True
                lev = pos.dsl_state.leverage if pos.dsl_state else self.cfg.dsl_leverage
                floor_price = pos.entry_price * (1 + pos.dsl_state.locked_floor_roe / 100 / lev) if pos.side == "long" \
                    else pos.entry_price * (1 - pos.dsl_state.locked_floor_roe / 100 / lev)
                return ("dsl_tier_lock", {
                    "roe": roe,
                    "floor_roe": pos.dsl_state.locked_floor_roe,
                    "floor_price": floor_price,
                    "tier": pos.dsl_state.current_tier.trigger_pct if pos.dsl_state.current_tier else 0,
                    "breaches": pos.dsl_state.breach_count,
                })

            # Alert on stagnation timer start
            if pos.dsl_state.stagnation_started and not pos.stagnation_alerted:
                pos.stagnation_alerted = True
                return ("dsl_stagnation_timer", {
                    "roe": roe,
                    "since": pos.dsl_state.stagnation_started,
                })

            # ── Trailing TP (works alongside DSL) ──
            # Sync high_water_mark for compute_tp_price() which uses it
            if pos.dsl_state.high_water_price > 0:
                pos.high_water_mark = pos.dsl_state.high_water_price

            # Check trailing TP activation
            if not pos.trailing_active:
                if pos.side == "long":
                    trigger = pos.entry_price * (1 + self.cfg.trailing_tp_trigger_pct / 100)
                    if pos.high_water_mark >= trigger:
                        pos.trailing_active = True
                        logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${pos.high_water_mark:,.2f}")
                else:
                    trigger = pos.entry_price * (1 - self.cfg.trailing_tp_trigger_pct / 100)
                    if pos.high_water_mark <= trigger:
                        pos.trailing_active = True
                        logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${pos.high_water_mark:,.2f}")

            # Check trailing TP trigger
            if pos.trailing_active:
                tp_price = self.compute_tp_price(pos)
                if tp_price:
                    pnl_pct = (price - pos.entry_price) / pos.entry_price * 100 if pos.side == "long" \
                        else (pos.entry_price - price) / pos.entry_price * 100
                    if pos.side == "long" and price <= tp_price and pnl_pct > 0:
                        return "trailing_take_profit"
                    elif pos.side == "short" and price >= tp_price and pnl_pct > 0:
                        return "trailing_take_profit"

            return None

        # ── Legacy mode: flat trailing TP/SL ──
        # Update high water mark (for trailing TP)
        trailing_just_activated = False
        if pos.side == "long" and price > pos.high_water_mark:
            pos.high_water_mark = price
            # Keep DSL state in sync for potential mode switch
            if pos.dsl_state:
                pos.dsl_state.high_water_price = pos.high_water_mark
                pos.dsl_state.high_water_time = datetime.now(timezone.utc)
            if not pos.trailing_active:
                trigger = pos.entry_price * (1 + self.cfg.trailing_tp_trigger_pct / 100)
                if price >= trigger:
                    pos.trailing_active = True
                    trailing_just_activated = True
                    logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${price:,.2f}")
        elif pos.side == "short" and price < pos.high_water_mark:
            pos.high_water_mark = price
            # Keep DSL state in sync for potential mode switch
            if pos.dsl_state:
                pos.dsl_state.high_water_price = pos.high_water_mark
                pos.dsl_state.high_water_time = datetime.now(timezone.utc)
            if not pos.trailing_active:
                trigger = pos.entry_price * (1 - self.cfg.trailing_tp_trigger_pct / 100)
                if price <= trigger:
                    pos.trailing_active = True
                    trailing_just_activated = True
                    logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${price:,.2f}")

        # Update trailing stop loss (ratchets up on longs, down on shorts — never reverses)
        sl_pct = self._get_sl_pct(pos)
        if pos.side == "long":
            candidate = price * (1 - sl_pct / 100)
            if pos.trailing_sl_level is None or candidate > pos.trailing_sl_level:
                old = pos.trailing_sl_level
                pos.trailing_sl_level = candidate
                if old is not None:
                    logging.info(f"🛡️ {pos.symbol} trailing SL advanced: ${old:,.2f} → ${candidate:,.2f}")
        else:
            candidate = price * (1 + sl_pct / 100)
            if pos.trailing_sl_level is None or candidate < pos.trailing_sl_level:
                old = pos.trailing_sl_level
                pos.trailing_sl_level = candidate
                if old is not None:
                    logging.info(f"🛡️ {pos.symbol} trailing SL advanced: ${old:,.2f} → ${candidate:,.2f}")

        # Alert on trailing TP activation
        if trailing_just_activated:
            pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100) if pos.side == "long" \
                else ((pos.entry_price - price) / pos.entry_price * 100)
            return ("trailing_activated", {
                "price": price,
                "roe": pnl_pct * self.cfg.dsl_leverage,
                "pnl": pnl_pct,
            })

        # Check triggers
        sl_price = self.compute_sl_price(pos)
        tp_price = self.compute_tp_price(pos)
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100 if pos.side == "long" \
            else (pos.entry_price - price) / pos.entry_price * 100

        if pos.side == "long":
            if price <= sl_price:
                return "stop_loss"
            if pos.trailing_active and tp_price and price <= tp_price and pnl_pct > 0:
                return "trailing_take_profit"
        else:
            if price >= sl_price:
                return "stop_loss"
            if pos.trailing_active and tp_price and price >= tp_price and pnl_pct > 0:
                return "trailing_take_profit"

        return None

    def add_position(self, market_id: int, symbol: str, side: str, entry: float, size: float, leverage: float = None, sl_pct: float = None):
        lev = leverage or self.cfg.dsl_leverage
        dsl_state = None
        if self.cfg.dsl_enabled:
            dsl_state = DSLState(
                side=side,
                entry_price=entry,
                leverage=lev,
                high_water_price=entry,
                high_water_time=datetime.now(timezone.utc),
            )
        pos = TrackedPosition(
            market_id=market_id,
            symbol=symbol,
            side=side,
            entry_price=entry,
            size=size,
            high_water_mark=entry,
            dsl_state=dsl_state,
            sl_pct=sl_pct,
        )
        self.positions[market_id] = pos
        sl_source = f"AI={sl_pct}%" if sl_pct is not None else f"config={self.cfg.hard_sl_pct}%"
        mode = f"DSL (lev={lev}x)" if self.cfg.dsl_enabled else "legacy trailing"
        logging.info(f"📌 Tracking: {side.upper()} {symbol} @ ${entry:,.2f}, size={size}, mode={mode}, SL={sl_source}")

    def remove_position(self, market_id: int):
        self.positions.pop(market_id, None)
