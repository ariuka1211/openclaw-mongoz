"""
BTC Grid Bot — Technical Indicators Module

Calculates Bollinger Bands, ATR, ADX, and Trend Skew Score
to enhance AI analyst grid level decisions.
"""

import math
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


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


def calc_trend_skew(candles_15m: List[Dict], candles_4h: List[Dict], market_intel: Dict) -> Dict:
    """Multi-signal trend skew score (-100 to +100)."""
    breakdown = {}
    score = 0

    # --- Signal 1: Price vs Bollinger Middle (25%) ---
    bb = calc_bollinger_bands(candles_15m)
    bb_score = 0
    if bb["middle"] > 0:
        price = candles_15m[-1]["c"]
        diff_pct = (price - bb["middle"]) / bb["middle"] * 100
        if abs(diff_pct) < 0.2:
            bb_score = 0
        elif price > bb["middle"]:
            bb_score = 25
        else:
            bb_score = -25
    breakdown["BB"] = bb_score
    score += bb_score

    # --- Signal 2: 4H Trend (30%) ---
    tf_score = 0
    if len(candles_4h) >= 6:
        last_6 = candles_4h[-6:]
        highs = [c["h"] for c in last_6]
        lows = [c["l"] for c in last_6]

        higher_highs = all(highs[i] >= highs[i - 1] for i in range(1, len(highs)))
        higher_lows = all(lows[i] >= lows[i - 1] for i in range(1, len(lows)))
        lower_highs = all(highs[i] <= highs[i - 1] for i in range(1, len(highs)))
        lower_lows = all(lows[i] <= lows[i - 1] for i in range(1, len(lows)))

        if higher_highs and higher_lows:
            tf_score = 30
        elif lower_highs and lower_lows:
            tf_score = -30
        else:
            # Partial signals
            hh_count = sum(1 for i in range(1, len(highs)) if highs[i] >= highs[i - 1])
            hl_count = sum(1 for i in range(1, len(lows)) if lows[i] >= lows[i - 1])
            bullish = hh_count + hl_count
            bearish = (5 - hh_count) + (5 - hl_count)
            if bullish > bearish + 2:
                tf_score = 15
            elif bearish > bullish + 2:
                tf_score = -15
    breakdown["4H"] = tf_score
    score += tf_score

    # --- Signal 3: ADX Strength (15%) ---
    adx_data = calc_adx(candles_15m)
    adx_score = 0
    adx_val = adx_data["adx"]
    if adx_val > 25:
        if adx_data["plus_di"] > adx_data["minus_di"]:
            adx_score = 15
        else:
            adx_score = -15
    elif adx_val >= 20:
        # Scale proportionally
        scale = (adx_val - 20) / 5.0
        if adx_data["plus_di"] > adx_data["minus_di"]:
            adx_score = round(15 * scale)
        else:
            adx_score = round(-15 * scale)
    breakdown["ADX"] = adx_score
    score += adx_score

    # --- Signal 4: Funding Rate (15%) ---
    funding_score = 0
    funding = market_intel.get("current", {}).get("funding_rate", 0)
    if funding > 0.0003:
        funding_score = -15  # crowded long, expect reversion down
    elif funding < -0.0003:
        funding_score = 15  # crowded short, expect reversion up
    elif abs(funding) > 0.0001:
        # Linear scale between thresholds
        if funding > 0:
            funding_score = round(-15 * (funding - 0.0001) / 0.0002)
        else:
            funding_score = round(15 * (-funding - 0.0001) / 0.0002)
    breakdown["Funding"] = funding_score
    score += funding_score

    # --- Signal 5: OI Direction (15%) ---
    oi_score = 0
    oi_history = market_intel.get("oi_history", [])
    if len(oi_history) >= 24:
        current_oi = oi_history[-1].get("oi_usd", 0)
        past_oi = oi_history[-24].get("oi_usd", 0)
        if past_oi > 0:
            oi_change = (current_oi - past_oi) / past_oi
            if oi_change > 0.02:  # OI rising
                # Check price direction
                if len(candles_15m) >= 96:
                    price_now = candles_15m[-1]["c"]
                    price_24h_ago = candles_15m[-96]["c"]
                    if price_now > price_24h_ago:
                        oi_score = 15  # OI up + price up
                    else:
                        oi_score = -15  # OI up + price down
            elif oi_change < -0.02:
                oi_score = 0  # OI falling, unclear
    breakdown["OI"] = oi_score
    score += oi_score

    # Clamp score
    score = max(-100, min(100, score))

    # Determine direction and allocation
    if score > 40:
        direction = "strong_uptrend"
        buy_pct, sell_pct = 70, 30
    elif score > 20:
        direction = "mild_uptrend"
        buy_pct, sell_pct = 60, 40
    elif score > -20:
        direction = "ranging"
        buy_pct, sell_pct = 50, 50
    elif score > -40:
        direction = "mild_downtrend"
        buy_pct, sell_pct = 40, 60
    else:
        direction = "strong_downtrend"
        buy_pct, sell_pct = 30, 70

    # Guidance
    if score > 20:
        guidance = "Place more buy levels deeper below to catch pullbacks. Fewer, wider sell levels above."
    elif score < -20:
        guidance = "Place more sell levels higher above to short rips. Fewer, wider buy levels below."
    else:
        guidance = "Symmetric grid — ranging market, balance buys and sells."

    breakdown_str = " ".join(f"{k}({'+' if v > 0 else ''}{v})" for k, v in breakdown.items())

    formatted = (
        f"=== TREND SKEW ===\n"
        f"Score: {'+' if score > 0 else ''}{score} ({direction.replace('_', ' ')})\n"
        f"Breakdown: {breakdown_str}\n"
        f"Direction: {buy_pct}% buy / {sell_pct}% sell\n"
        f"Guidance: {guidance}\n"
        f"=== END SKEW ==="
    )

    return {
        "score": score,
        "direction": direction,
        "buy_pct": buy_pct,
        "sell_pct": sell_pct,
        "breakdown": breakdown,
        "formatted": formatted,
    }


def format_indicators(bands: Dict, atr: Dict, skew: Dict) -> str:
    """Format all indicators into text block for LLM prompt."""
    position_pct = round(bands["position"] * 100)
    half = "upper" if position_pct > 50 else "lower"
    trend_word = "expanding" if bands["expanding"] else "contracting"

    return (
        f"=== BOLLINGER BANDS ===\n"
        f"Upper: ${bands['upper']:,.0f} | Middle: ${bands['middle']:,.0f} | Lower: ${bands['lower']:,.0f}\n"
        f"Band Width: ${bands['width']:,.0f} ({bands['width_pct']:.1f}%) — {trend_word}\n"
        f"Price Position: {position_pct}% through band ({half} half)\n"
        f"Suggested Level Spacing: ${atr['suggested_spacing']:,.0f} (0.5x ATR = ${atr['atr']:,.0f})\n"
        f"\n"
        f"{skew['formatted']}"
    )


def gather_indicators(candles_15m: List[Dict], candles_30m: List[Dict],
                       candles_4h: List[Dict], market_intel: Dict) -> Dict:
    """Calculate all indicators and return combined result."""
    bands = calc_bollinger_bands(candles_15m)
    atr = calc_atr(candles_15m)
    adx = calc_adx(candles_15m)
    skew = calc_trend_skew(candles_15m, candles_4h, market_intel)

    # 4H EMA(50) trend filter
    ema_50_4h = calc_ema_single(candles_4h, 50)
    current_price = candles_15m[-1]["c"]

    if ema_50_4h is not None:
        pct_diff = (current_price - ema_50_4h) / ema_50_4h
        if pct_diff > 0.01:  # price >1% above ema
            trend = "uptrend"
        elif pct_diff < -0.01:  # price <1% below ema
            trend = "downtrend"
        else:
            trend = "neutral"
    else:
        trend = "no_data"

    # Append trend info to formatted output
    base_formatted = format_indicators(bands, atr, skew)
    if ema_50_4h is not None:
        trend_line = f"\n📈 4H EMA(50): ${ema_50_4h:,.0f} | Trend: {trend}"
    else:
        trend_line = f"\n📈 4H EMA(50): N/A | Trend: no_data"
    formatted = base_formatted + trend_line

    return {
        "bollinger": bands,
        "atr": atr,
        "adx": adx,
        "skew": skew,
        "ema_50_4h": ema_50_4h,
        "trend": trend,
        "formatted": formatted,
    }


async def main():
    """Standalone testing."""
    import yaml
    from pathlib import Path
    from analyst import fetch_candles
    from market_intel import gather_all_intel
    
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        cfg = {}
    
    print("Fetching candle data...")
    candles_15m = await fetch_candles("15m", limit=200)
    candles_30m = await fetch_candles("30m", limit=200) 
    candles_4h = await fetch_candles("4H", limit=48)
    
    print("Gathering market intelligence...")
    market_intel = await gather_all_intel(cfg)
    
    print("Calculating indicators...")
    result = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel)
    
    print("\n=== INDICATORS OUTPUT ===")
    print(result["formatted"])
    print("\n=== RAW DATA ===")
    for key, value in result.items():
        if key != "formatted":
            print(f"{key}: {value}")


if __name__ == "__main__":
    import asyncio
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
