"""Exponential Moving Average (EMA) indicator."""

from typing import Dict, List


def calc_ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average (returns series)."""
    if len(values) < period:
        return [float("nan")] * len(values)

    multiplier = 2.0 / (period + 1)
    # Start with SMA
    ema = sum(values[:period]) / period
    result = [float("nan")] * (period - 1) + [ema]

    for i in range(period, len(values)):
        ema = (values[i] - ema) * multiplier + ema
        result.append(ema)

    return result


def calc_ema_single(candles: List[Dict], period: int) -> float | None:
    """Calculate Exponential Moving Average from candle data.

    candles: list of dicts with 'c' (close) key, in chronological order.
    period: EMA period (e.g., 50).
    Returns the latest EMA value, or None if not enough data.
    Requires at least `period` candles.
    """
    closes = [c["c"] for c in candles]
    if len(closes) < period:
        return None

    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA as initial

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 2)
