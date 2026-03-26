"""
Position Sizer — Dynamic equity-based position sizing.

Calculates position size from live equity, risk rules, and signal volatility.
Replaces the old hardcoded max_position_usd cap.

Rules:
  - Max risk per trade: max_risk_pct of equity (default 2%)
  - Max margin per position: max_margin_pct of equity (default 15%)
  - Min risk/reward ratio: min_rr (default 1.5)
  - Hard SL floor: never set SL closer than hard_sl_pct
"""

import logging

log = logging.getLogger("bot.position_sizer")


class PositionSizer:
    """Calculate position size from equity and signal data."""

    __slots__ = (
        'max_risk_pct', 'max_margin_pct', 'min_rr',
        'hard_sl_pct', 'leverage', 'max_concurrent',
    )

    def __init__(self, cfg):
        """Initialize from BotConfig."""
        self.max_risk_pct = getattr(cfg, 'max_risk_pct', 0.02)
        self.max_margin_pct = getattr(cfg, 'max_margin_pct', 0.15)
        self.min_rr = getattr(cfg, 'min_risk_reward', 1.5)
        self.hard_sl_pct = cfg.hard_sl_pct
        self.leverage = cfg.dsl_leverage
        self.max_concurrent = getattr(cfg, 'max_concurrent_signals', 3)

    def size_position(self, equity, signal):
        """Calculate position size for a signal.

        Args:
            equity: Account equity in USD
            signal: Dict from signals.json opportunity

        Returns:
            (size_usd, risk_usd, sl_pct, reason)
        """
        if equity <= 0:
            return (0, 0, 0, f"equity=${equity:.2f} <= 0")

        # 1. Risk budget per trade
        risk_usd = equity * self.max_risk_pct

        # 2. SL distance from signal volatility
        daily_vol = signal.get('dailyVolatility', 0)
        if daily_vol <= 0:
            sl_pct = self.hard_sl_pct / 100
        else:
            sl_pct = max(daily_vol, self.hard_sl_pct / 100)

        # 3. Position size from risk: size = risk / sl_distance
        size_usd = risk_usd / sl_pct

        # 4. Margin cap
        max_notional = equity * self.max_margin_pct * self.leverage
        if size_usd > max_notional:
            size_usd = max_notional

        # 5. Minimum position check
        if size_usd < 1.0:
            return (0, 0, 0, f"position ${size_usd:.2f} too small")

        # 6. R:R check
        ob_dist_pct = signal.get('obDistancePct')
        if ob_dist_pct is not None and ob_dist_pct > 0:
            rr = ob_dist_pct / (sl_pct * 100)
            if rr < self.min_rr:
                return (0, 0, 0, f"R:R {rr:.1f} < {self.min_rr}")

        return (size_usd, risk_usd, sl_pct, "OK")
