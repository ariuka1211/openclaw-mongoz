"""Helpers for indicator calculations."""

import math
from typing import Dict, List


def calc_sma(candles: List[Dict], period: int) -> List[float]:
    """Simple moving average of close prices."""
    closes = [c["c"] for c in candles]
    result = [float("nan")] * (period - 1)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        result.append(sum(window) / period)
    return result


def calc_std(candles: List[Dict], period: int) -> List[float]:
    """Rolling standard deviation of close prices."""
    closes = [c["c"] for c in candles]
    result = [float("nan")] * (period - 1)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        result.append(math.sqrt(variance))
    return result
