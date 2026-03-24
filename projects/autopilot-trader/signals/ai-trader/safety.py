"""
Safety layer — hard rules the LLM cannot override.
Every decision passes through these checks BEFORE reaching the bot.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from db import DecisionDB

log = logging.getLogger("ai-trader.safety")


class SafetyLayer:
    __slots__ = (
        'max_positions', 'max_leverage', 'max_size_pct_equity',
        'max_daily_drawdown_pct', 'max_total_exposure_pct',
        'min_confidence', 'min_scanner_score', 'required_stop_loss',
        'min_stop_loss_pct', 'max_orders_per_hour', 'cooldown_after_loss_seconds',
        'max_consecutive_failures', 'max_rejection_halt_count',
        'rejection_halt_window_minutes', 'failure_decay_minutes',
        'db', '_order_timestamps', '_last_loss_time', '_last_failure_time',
    )

    def __init__(self, config: dict, db: DecisionDB):
        cfg = config.get("safety", {})
        self.max_positions = cfg.get("max_positions", 3)
        self.max_leverage = cfg.get("max_leverage", 20.0)
        self.max_size_pct_equity = cfg.get("max_size_pct_equity", 5.0)
        self.max_daily_drawdown_pct = cfg.get("max_daily_drawdown_pct", 10.0)
        self.max_total_exposure_pct = cfg.get("max_total_exposure_pct", 15.0)
        self.min_confidence = cfg.get("min_confidence", 0.3)
        self.min_scanner_score = cfg.get("min_scanner_score", 60)
        self.required_stop_loss = cfg.get("required_stop_loss", True)
        self.min_stop_loss_pct = cfg.get("min_stop_loss_pct", 0.5)
        self.max_orders_per_hour = cfg.get("max_orders_per_hour", 12)
        self.cooldown_after_loss_seconds = cfg.get("cooldown_after_loss_seconds", 300)

        # Kill switch thresholds (from config, not hardcoded)
        self.max_consecutive_failures = config.get("max_consecutive_failures", 5)
        self.max_rejection_halt_count = config.get("max_rejection_halt_count", 15)
        self.rejection_halt_window_minutes = config.get("rejection_halt_window_minutes", 30)
        self.failure_decay_minutes = config.get("failure_decay_minutes", 10)

        self.db = db
        self._order_timestamps = []
        self._last_loss_time: float | None = None
        self._last_failure_time: float = 0  # Track last LLM/execution failure for time-based decay

    def validate(self, decision: dict, positions: list, signals: list, equity: float = 1000.0) -> tuple[bool, list[str]]:
        """Validate decision against all safety rules. Returns (approved, reasons)."""
        reasons = []

        # Hold is always safe
        if decision.get("action") == "hold":
            return True, ["hold — always safe"]

        action = decision.get("action", "")

        # ── Schema validation (Rule 11) ──
        schema_ok, schema_errors = self._validate_schema(decision)
        if not schema_ok:
            reasons.extend(schema_errors)

        if action == "open":
            approved, open_reasons = self._validate_open(decision, positions, signals, equity, reasons)
        elif action == "close":
            approved, close_reasons = self._validate_close(decision, positions, reasons)
        elif action == "close_all":
            approved = len(reasons) == 0
        else:
            reasons.append(f"unknown action: {action}")
            approved = False

        # ── Confidence check (must apply to ALL actions including close_all) ──
        confidence = decision.get("confidence", 0)
        if confidence < self.min_confidence:
            reasons.append(f"confidence {confidence:.2f} < {self.min_confidence}")

        return len(reasons) == 0, reasons

    def _validate_schema(self, decision: dict) -> tuple[bool, list[str]]:
        """Rule 11: Validate LLM JSON schema."""
        errors = []
        required = ["action", "reasoning", "confidence"]
        for key in required:
            if key not in decision:
                errors.append(f"missing required field: {key}")

        action = decision.get("action", "")
        if action not in ("open", "close", "close_all", "hold"):
            errors.append(f"invalid action: {action}")

        if action == "open":
            open_required = ["symbol", "direction", "size_pct_equity", "leverage", "stop_loss_pct"]
            for key in open_required:
                if key not in decision:
                    errors.append(f"missing open field: {key}")

        if action == "close":
            if "symbol" not in decision:
                errors.append("missing symbol for close")

        return len(errors) == 0, errors

    def _validate_open(self, decision: dict, positions: list, signals: list, equity: float, reasons: list) -> tuple[bool, list[str]]:
        """Validate open position decision."""
        symbol = decision.get("symbol", "")
        leverage = decision.get("leverage", 0)
        size_pct = decision.get("size_pct_equity", 0)
        sl_pct = decision.get("stop_loss_pct")
        direction = decision.get("direction", "")

        # Rule 1: Max 3 concurrent positions
        if len(positions) >= self.max_positions:
            reasons.append(f"max positions ({self.max_positions}) reached")

        # Rule 9: No opening if same-direction position exists (unless adding)
        for p in positions:
            if p.get("symbol") == symbol and p.get("side") == direction:
                reasons.append(f"already have {direction} position in {symbol}")

        # Already in this market any direction
        if any(p.get("symbol") == symbol for p in positions):
            reasons.append(f"already have position in {symbol} (different direction)")

        # Rule 2: Max 20x leverage (must be positive)
        if leverage <= 0:
            reasons.append(f"leverage {leverage}x must be positive")
        elif leverage > self.max_leverage:
            reasons.append(f"leverage {leverage}x > max {self.max_leverage}x")

        # Rule 3: Max 5% equity per position (must be positive)
        if size_pct <= 0:
            reasons.append(f"size {size_pct}% must be positive")
        elif size_pct > self.max_size_pct_equity:
            reasons.append(f"size {size_pct}% > max {self.max_size_pct_equity}%")

        # Rule 12: All positions need stop losses
        if self.required_stop_loss and (not sl_pct or sl_pct < self.min_stop_loss_pct):
            reasons.append(f"stop loss {sl_pct}% invalid (min {self.min_stop_loss_pct}%)")

        # Rule 8: Liquidation distance ≥ 2x stop-loss distance
        # For safety: max_leverage * sl_pct must leave margin
        # Simple check: at given leverage, sl_pct must be <= 100/max_leverage/2
        if leverage > 0 and sl_pct is not None:
            max_safe_sl = 100.0 / leverage / 2.0
            if sl_pct > max_safe_sl:
                reasons.append(
                    f"sl {sl_pct}% with {leverage}x lev — liquidation at {100/leverage:.1f}%, "
                    f"need SL < {max_safe_sl:.1f}%"
                )

        # Rule 4: Max 15% total exposure
        current_exposure = sum(
            abs(p.get("position_size_usd", p.get("size_usd", 0))) for p in positions
        )
        new_exposure = equity * size_pct / 100
        total_exposure_pct = (current_exposure + new_exposure) / equity * 100
        if total_exposure_pct > self.max_total_exposure_pct:
            reasons.append(
                f"total exposure {total_exposure_pct:.1f}% > max {self.max_total_exposure_pct}%"
            )

        # Rule 5: Daily drawdown check
        daily_pnl = self.db.get_daily_pnl()
        if equity > 0:
            daily_dd = -daily_pnl / equity * 100
            if daily_dd > self.max_daily_drawdown_pct * 0.8:
                reasons.append(f"approaching daily drawdown limit ({daily_dd:.1f}%)")

        # Signal score floor
        matching_signal = next((s for s in signals if s.get("symbol") == symbol), None)
        if matching_signal and matching_signal.get("compositeScore", 0) < self.min_scanner_score:
            reasons.append(f"scanner score {matching_signal['compositeScore']} < {self.min_scanner_score}")

        if matching_signal and not matching_signal.get("safetyPass", False):
            reasons.append(f"scanner safety check failed: {matching_signal.get('safetyReason', '?')}")

        # Rule 10: Rate limiting
        if not self._check_rate_limit():
            reasons.append("rate limit exceeded")

        # Cooldown after loss
        if self._in_cooldown():
            reasons.append(f"cooldown after recent loss ({self.cooldown_after_loss_seconds}s)")

        return len(reasons) == 0, reasons

    def _validate_close(self, decision: dict, positions: list, reasons: list) -> tuple[bool, list[str]]:
        """Validate close decision."""
        symbol = decision.get("symbol", "")
        if not any(p.get("symbol") == symbol for p in positions):
            reasons.append(f"no position in {symbol} to close")
        return len(reasons) == 0, reasons

    def _check_rate_limit(self) -> bool:
        """Rule 10: Max orders per hour."""
        now = time.time()
        # Clean old entries
        self._order_timestamps = [t for t in self._order_timestamps if now - t < 3600]
        return len(self._order_timestamps) < self.max_orders_per_hour

    def record_order(self):
        """Record an order execution for rate limiting."""
        self._order_timestamps.append(time.time())

    def record_loss(self):
        """Record a loss for cooldown timer."""
        self._last_loss_time = time.time()

    def _in_cooldown(self) -> bool:
        """Check if we're in cooldown after a recent loss."""
        if self._last_loss_time is None:
            return False
        return (time.time() - self._last_loss_time) < self.cooldown_after_loss_seconds

    def get_daily_drawdown(self, equity: float = 0) -> float:
        """Calculate today's realized PnL as % drawdown."""
        daily_pnl = self.db.get_daily_pnl()
        if daily_pnl >= 0:
            return 0.0
        if equity <= 0:
            return 0.0  # Can't calculate drawdown without equity
        return abs(daily_pnl) / equity * 100

    def check_kill_switch(self, consecutive_failures: int, rejection_window_count: int, equity: float = 0) -> list[str]:
        """Check all kill switch conditions. Returns list of triggered reasons."""
        triggers = []

        # Time-based decay: reset consecutive failures if last failure was > N minutes ago
        if self._last_failure_time > 0:
            minutes_since = (time.time() - self._last_failure_time) / 60
            if minutes_since > self.failure_decay_minutes:
                if consecutive_failures > 0:
                    log.info(f"Kill switch: resetting consecutive_failures from {consecutive_failures} to 0 "
                             f"(no failures for {minutes_since:.1f} min)")
                consecutive_failures = 0

        # Daily drawdown
        dd = self.get_daily_drawdown(equity)
        if dd > self.max_daily_drawdown_pct:
            triggers.append(f"Daily drawdown {dd:.1f}% > {self.max_daily_drawdown_pct}%")

        # Consecutive failures
        if consecutive_failures >= self.max_consecutive_failures:
            triggers.append(f"{consecutive_failures} consecutive LLM failures (limit: {self.max_consecutive_failures})")

        # Too many rejections (within the configured time window)
        if rejection_window_count >= self.max_rejection_halt_count:
            triggers.append(f"{rejection_window_count} safety rejections in {self.rejection_halt_window_minutes} min (limit: {self.max_rejection_halt_count})")

        return triggers
