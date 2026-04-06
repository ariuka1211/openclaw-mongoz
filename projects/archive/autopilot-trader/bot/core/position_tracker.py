"""
Position tracker with DSL (Dynamic Stop Loss) and legacy trailing TP/SL.

Manages per-position state, high-water marks, and trigger evaluation.
"""

import logging
from datetime import datetime, timezone

from config import BotConfig
from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl, evaluate_trailing_sl
from core.models import TrackedPosition


class PositionTracker:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.positions: dict[int, TrackedPosition] = {}  # key: market_id
        self.account_equity: float = 0.0  # Cached account equity for exposure calculations
        # Build DSL config from bot config
        self.dsl_cfg = DSLConfig(
            stagnation_move_pct=cfg.stagnation_move_pct,
            stagnation_minutes=cfg.stagnation_minutes,
            hard_sl_pct=cfg.hard_sl_pct,
        )
        # Parse custom tiers from config if present
        if cfg.dsl_tiers:
            self.dsl_cfg.tiers = [
                DSLTier(
                    trigger_pct=t.get("trigger_pct", 7),
                    lock_hw_pct=t.get("lock_hw_pct", 40),
                    trailing_buffer_pct=t.get("trailing_buffer_pct"),
                    consecutive_breaches=t.get("consecutive_breaches", 3),
                )
                for t in cfg.dsl_tiers
            ]

    def _compute_hard_floor_price(self, pos: TrackedPosition) -> float:
        """Return hard floor price (per-position sl_pct or config default)."""
        sl = pos.sl_pct if pos.sl_pct is not None else self.cfg.hard_sl_pct
        if pos.side == "long":
            return pos.entry_price * (1 - sl / 100)
        else:
            return pos.entry_price * (1 + sl / 100)

    def update_price(self, market_id: int, price: float) -> str | None:
        pos = self.positions.get(market_id)
        if not pos:
            return None

        # ── DSL mode: tiered trailing stop loss ──
        if self.cfg.dsl_enabled and pos.dsl_state:
            result = evaluate_dsl(pos.dsl_state, price, self.dsl_cfg)
            move_pct = pos.dsl_state.current_move_pct(price)
            if result:
                floor_str = (f"Floor: {pos.dsl_state.locked_floor_pct:+.1f}%"
                             if pos.dsl_state.locked_floor_pct else "")
                tier_str = (str(pos.dsl_state.current_tier.trigger_pct)
                            if pos.dsl_state.current_tier else "none")
                logging.info(
                    f"🛑 {pos.symbol} DSL {result} | "
                    f"Move: {move_pct:+.2f}% | HW: {pos.dsl_state.high_water_move_pct:+.2f}% | "
                    f"Tier: {tier_str} | {floor_str}"
                )
                return result

            # Log DSL state periodically (when tier changes or HW updates)
            if pos.dsl_state.current_tier:
                logging.debug(
                    f"📊 {pos.symbol} DSL | Move: {move_pct:+.2f}% | "
                    f"HW: {pos.dsl_state.high_water_move_pct:+.2f}% | "
                    f"Tier: {pos.dsl_state.current_tier.trigger_pct}% | "
                    f"Breaches: {pos.dsl_state.breach_count}/{pos.dsl_state.current_tier.consecutive_breaches}"
                )

            # Alert on tier lock
            if pos.dsl_state.locked_floor_pct is not None and not getattr(pos, '_tier_lock_alerted', False):
                pos._tier_lock_alerted = True
                # locked_floor_pct is already in price move %, no leverage division needed
                floor_price = pos.entry_price * (1 + pos.dsl_state.locked_floor_pct / 100) if pos.side == "long" \
                    else pos.entry_price * (1 - pos.dsl_state.locked_floor_pct / 100)
                return ("dsl_tier_lock", {
                    "move_pct": move_pct,
                    "floor_move_pct": pos.dsl_state.locked_floor_pct,
                    "floor_price": floor_price,
                    "tier": pos.dsl_state.current_tier.trigger_pct if pos.dsl_state.current_tier else 0,
                    "breaches": pos.dsl_state.breach_count,
                })

            # Alert on stagnation timer start
            if pos.dsl_state.stagnation_started and not getattr(pos, '_stagnation_alerted', False):
                pos._stagnation_alerted = True
                return ("dsl_stagnation_timer", {
                    "move_pct": move_pct,
                    "since": pos.dsl_state.stagnation_started,
                })

            # ── Trailing SL (downside protection alongside DSL) ──
            # Sync high_water_mark from DSL for trailing SL
            if pos.dsl_state.high_water_price > 0:
                pos.high_water_mark = pos.dsl_state.high_water_price

            # Use DSL's high water price for trailing SL
            hw_price = pos.dsl_state.high_water_price if pos.dsl_state.high_water_price > 0 else pos.high_water_mark
            sl_floor_pct = pos.sl_pct if pos.sl_pct is not None else self.cfg.hard_sl_pct

            action, new_level, new_activated = evaluate_trailing_sl(
                side=pos.side,
                entry_price=pos.entry_price,
                price=price,
                high_water_price=hw_price,
                trailing_sl_level=pos.trailing_sl_level,
                trailing_sl_activated=pos.trailing_sl_activated,
                trigger_pct=self.cfg.trailing_sl_trigger_pct,
                step_pct=self.cfg.trailing_sl_step_pct,
                hard_floor_pct=sl_floor_pct,
            )
            pos.trailing_sl_level = new_level
            pos.trailing_sl_activated = new_activated
            if action:
                sl_display = f"${new_level:,.2f}" if new_level is not None else "hard floor"
                logging.info(f"🔻 {pos.symbol} trailing SL triggered | Price: ${price:,.2f} | SL: {sl_display}")
                return "trailing_sl"
            return None

        # ── Legacy mode: trailing SL only (no trailing TP) ──
        # Update high water mark
        if pos.side == "long" and price > pos.high_water_mark:
            pos.high_water_mark = price
            if pos.dsl_state:
                pos.dsl_state.high_water_price = pos.high_water_mark
                pos.dsl_state.high_water_time = datetime.now(timezone.utc)
        elif pos.side == "short" and price < pos.high_water_mark:
            pos.high_water_mark = price
            if pos.dsl_state:
                pos.dsl_state.high_water_price = pos.high_water_mark
                pos.dsl_state.high_water_time = datetime.now(timezone.utc)

        # Evaluate trailing SL
        sl_floor_pct = pos.sl_pct if pos.sl_pct is not None else self.cfg.hard_sl_pct
        action, new_level, new_activated = evaluate_trailing_sl(
            side=pos.side,
            entry_price=pos.entry_price,
            price=price,
            high_water_price=pos.high_water_mark,
            trailing_sl_level=pos.trailing_sl_level,
            trailing_sl_activated=pos.trailing_sl_activated,
            trigger_pct=self.cfg.trailing_sl_trigger_pct,
            step_pct=self.cfg.trailing_sl_step_pct,
            hard_floor_pct=sl_floor_pct,
        )
        pos.trailing_sl_level = new_level
        pos.trailing_sl_activated = new_activated
        if action:
            sl_display = f"${new_level:,.2f}" if new_level is not None else "hard floor"
            logging.info(f"🔻 {pos.symbol} trailing SL triggered | Price: ${price:,.2f} | SL: {sl_display}")
            return "trailing_sl"
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
