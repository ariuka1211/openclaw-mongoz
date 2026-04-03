"""Average Directional Index (ADX) indicator."""

from typing import Dict, List


def calc_adx(candles: List[Dict], period: int = 14) -> Dict:
    """Calculate Average Directional Index."""
    if len(candles) < period + 1:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend_strength": "none"}

    # Calculate +DM, -DM, TR
    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, len(candles)):
        up_move = candles[i]["h"] - candles[i - 1]["h"]
        down_move = candles[i - 1]["l"] - candles[i]["l"]

        pd = up_move if (up_move > down_move and up_move > 0) else 0
        md = down_move if (down_move > up_move and down_move > 0) else 0

        tr = max(
            candles[i]["h"] - candles[i]["l"],
            abs(candles[i]["h"] - candles[i - 1]["c"]),
            abs(candles[i]["l"] - candles[i - 1]["c"]),
        )

        plus_dm.append(pd)
        minus_dm.append(md)
        tr_list.append(tr)

    if len(tr_list) < period:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend_strength": "none"}

    # Smooth with Wilder's method
    def wilders_smooth(data, period):
        smoothed = [sum(data[:period]) / period]
        for i in range(period, len(data)):
            smoothed.append((smoothed[-1] * (period - 1) + data[i]) / period)
        return smoothed

    smooth_plus = wilders_smooth(plus_dm, period)
    smooth_minus = wilders_smooth(minus_dm, period)
    smooth_tr = wilders_smooth(tr_list, period)

    # DI+ and DI-
    plus_di_list = []
    minus_di_list = []
    dx_list = []

    length = min(len(smooth_plus), len(smooth_tr))
    for i in range(length):
        if smooth_tr[i] == 0:
            plus_di_list.append(0)
            minus_di_list.append(0)
            dx_list.append(0)
            continue
        pdi = 100 * smooth_plus[i] / smooth_tr[i]
        mdi = 100 * smooth_minus[i] / smooth_tr[i]
        plus_di_list.append(pdi)
        minus_di_list.append(mdi)

        denom = pdi + mdi
        if denom == 0:
            dx_list.append(0)
        else:
            dx_list.append(100 * abs(pdi - mdi) / denom)

    # ADX = smoothed DX
    if len(dx_list) < period:
        adx = sum(dx_list) / len(dx_list) if dx_list else 0
    else:
        adx = sum(dx_list[:period]) / period
        for i in range(period, len(dx_list)):
            adx = (adx * (period - 1) + dx_list[i]) / period

    plus_di = plus_di_list[-1] if plus_di_list else 0
    minus_di = minus_di_list[-1] if minus_di_list else 0

    if adx > 40:
        strength = "strong"
    elif adx > 25:
        strength = "moderate"
    elif adx > 20:
        strength = "weak"
    else:
        strength = "none"

    return {
        "adx": round(adx, 2),
        "plus_di": round(plus_di, 2),
        "minus_di": round(minus_di, 2),
        "trend_strength": strength,
    }
