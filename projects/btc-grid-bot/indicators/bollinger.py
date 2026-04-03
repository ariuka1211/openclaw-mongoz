"""Bollinger Bands indicator."""

import math
from typing import Dict, List

from .helpers import calc_sma, calc_std


def calc_bollinger_bands(candles: List[Dict], period: int = 20, std_mult: float = 2.0) -> Dict:
    """Calculate Bollinger Bands from candle data."""
    if len(candles) < period:
        return {
            "upper": 0, "middle": 0, "lower": 0,
            "width": 0, "width_pct": 0, "position": 0.5, "expanding": False,
        }

    sma = calc_sma(candles, period)
    std = calc_std(candles, period)

    latest_sma = sma[-1]
    latest_std = std[-1]
    upper = latest_sma + std_mult * latest_std
    lower = latest_sma - std_mult * latest_std
    width = upper - lower
    width_pct = (width / latest_sma * 100) if latest_sma > 0 else 0

    # Price position within bands (0 = lower, 1 = upper)
    price = candles[-1]["c"]
    if width > 0:
        position = (price - lower) / width
    else:
        position = 0.5
    position = max(0.0, min(1.0, position))

    # Check if bands are expanding (last 3 wider than previous 3)
    expanding = False
    widths = []
    for i in range(len(sma)):
        if not math.isnan(sma[i]) and not math.isnan(std[i]):
            widths.append(2 * std_mult * std[i])
    if len(widths) >= 6:
        recent = sum(widths[-3:]) / 3
        previous = sum(widths[-6:-3]) / 3
        expanding = recent > previous

    return {
        "upper": round(upper, 2),
        "middle": round(latest_sma, 2),
        "lower": round(lower, 2),
        "width": round(width, 2),
        "width_pct": round(width_pct, 2),
        "position": round(position, 3),
        "expanding": expanding,
    }
