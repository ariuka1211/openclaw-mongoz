"""Average True Range (ATR) indicator."""

from typing import Dict, List


def calc_atr(candles: List[Dict], period: int = 14) -> Dict:
    """Calculate Average True Range."""
    if len(candles) < 2:
        return {"atr": 0, "atr_pct": 0, "suggested_spacing": 0}

    true_ranges = []
    for i in range(1, len(candles)):
        h = candles[i]["h"]
        l = candles[i]["l"]
        prev_c = candles[i - 1]["c"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        atr = sum(true_ranges) / len(true_ranges)
    else:
        # Wilder's smoothing
        atr = sum(true_ranges[:period]) / period
        for i in range(period, len(true_ranges)):
            atr = (atr * (period - 1) + true_ranges[i]) / period

    close = candles[-1]["c"]
    atr_pct = (atr / close * 100) if close > 0 else 0
    suggested_spacing = atr * 0.5

    return {
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 3),
        "suggested_spacing": round(suggested_spacing, 2),
    }
