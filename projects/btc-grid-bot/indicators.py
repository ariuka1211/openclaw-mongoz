"""
BTC Grid Bot — Technical Indicators Module

Calculates Bollinger Bands, ATR, ADX, and Trend Skew Score
to enhance AI analyst grid level decisions.
"""

import math
import logging
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)


def funding_rate_adjustment(funding_data: dict, price: float) -> dict:
    """Calculate grid size adjustment based on funding rate.

    Args:
        funding_data: dict with 'current' key containing funding info from market_intel.
        price: current BTC price (used for context in warning messages).

    Returns:
        {"adj_multiplier": float, "label": str, "warning": str | None}
    """
    current = funding_data.get("current", {})
    funding_rate = current.get("funding_rate", 0)

    if funding_rate < 0:
        # Negative funding: shorts paying longs — bullish squeeze potential
        if funding_rate < -0.0003:
            adj_multiplier = 1.15
            label = "Very negative funding — strong squeeze potential (1.15x)"
            warning = None
        elif funding_rate < -0.0001:
            adj_multiplier = 1.1
            label = "Negative funding — moderate squeeze potential (1.1x)"
            warning = None
        else:  # -0.0001 to 0
            adj_multiplier = 1.05
            label = "Slightly negative funding — mild squeeze potential (1.05x)"
            warning = None
    elif funding_rate > 0.001:
        # > 0.1% — extreme positive funding
        adj_multiplier = 0.4
        label = "Extreme positive funding — reduce 60%"
        warning = "⚠️ EXTREME FUNDING: Size reduced 60% to avoid liquidation squeeze"
    elif funding_rate > 0.0004:
        # 0.04% to 0.1%
        adj_multiplier = 0.6
        label = "High positive funding — reduce 40%"
        warning = None
    elif funding_rate > 0.0001:
        # 0.01% to 0.04%
        adj_multiplier = 0.8
        label = "Elevated positive funding — reduce 20%"
        warning = None
    else:
        # 0 to 0.01%
        adj_multiplier = 1.0
        label = "Normal funding — no adjustment"
        warning = None

    formatted = (
        f"Funding Rate: {funding_rate*100:.4f}% per 8h"
        f" | Adjustment: {adj_multiplier:.2f}x ({label})"
    )
    if warning:
        formatted += f" | {warning}"

    return {
        "adj_multiplier": adj_multiplier,
        "label": label,
        "warning": warning,
        "formatted": formatted,
    }


def calc_volume_profile(all_candles: List[Dict], current_price: float, bin_size: float = 50.0) -> Dict:
    """Build a Volume-at-Price histogram from combined candle data.

    Buckets prices into $bin_size bins and sums volume per bin.

    Returns:
        {
            "hist": dict[int, float]   # rounded price -> total volume
            "nodes": list[dict]        # top volume nodes, sorted by volume desc
            "poc": float               # Point of Control (highest volume price)
            "hvn": list[float]         # High Volume Nodes (top 3 by volume)
            "lvn": list[float]         # Low Volume Nodes (gaps between HVNs, max 3)
            "formatted": str           # text representation for LLM prompt
        }
    """
    if not all_candles:
        return {
            "hist": {},
            "nodes": [],
            "poc": 0.0,
            "hvn": [],
            "lvn": [],
            "formatted": "No volume profile data available.",
        }

    # Build histogram: bucket each candle by rounded high+low midpoint volume
    hist: Dict[int, float] = {}
    for c in all_candles:
        # Use average of high and low as the "representative price" for this candle
        mid = (c["h"] + c["l"]) / 2.0
        bucket = int(mid // bin_size) * int(bin_size)
        vol = c.get("v", 0)
        hist[bucket] = hist.get(bucket, 0.0) + vol

    if not hist:
        return {
            "hist": {},
            "nodes": [],
            "poc": 0.0,
            "hvn": [],
            "lvn": [],
            "formatted": "No volume profile data available.",
        }

    # Find Point of Control (highest volume bucket)
    poc_price = max(hist, key=hist.get)
    poc_vol = hist[poc_price]

    # Build sorted nodes list
    nodes = sorted(
        [{"price": float(price), "volume": float(vol)} for price, vol in hist.items()],
        key=lambda x: x["volume"],
        reverse=True,
    )

    # HVN: top 3 volume nodes
    hvn = [n["price"] for n in nodes[:3]]

    # LVN: low-volume gaps between HVNs (up to 3)
    if len(hvn) >= 2:
        hvn_sorted = sorted(hvn)
        lvn = []
        for i in range(len(hvn_sorted) - 1):
            gap_mid = (hvn_sorted[i] + hvn_sorted[i + 1]) / 2.0
            gap_bucket = int(gap_mid // bin_size) * int(bin_size)
            # Only include if volume in this gap is significantly lower than neighbors
            gap_vol = hist.get(gap_bucket, 0)
            neighbor_vols = [hist.get(b, 0) for b in hist if abs(b - gap_bucket) < 3 * bin_size and b != gap_bucket]
            if neighbor_vols and gap_vol < sum(neighbor_vols) / len(neighbor_vols) * 0.3:
                lvn.append(float(gap_bucket))
        lvn = lvn[:3]
    else:
        lvn = []

    # Format for LLM prompt
    lines = []
    lines.append("=== VOLUME PROFILE ===")
    lines.append(f"Point of Control: ${poc_price:,.0f} (vol: {poc_vol:.2f})")
    lines.append(f"High Volume Nodes: {', '.join(f'${p:,.0f}' for p in hvn)}")
    if lvn:
        lines.append(f"Low Volume Nodes (gaps): {', '.join(f'${p:,.0f}' for p in lvn)}")
    lines.append("Top 10 volume bins:")
    for n in nodes[:10]:
        marker = " <-- POC" if n["price"] == poc_price else ""
        marker += " <-- HVN" if n["price"] in hvn else ""
        lines.append(f"  ${int(n['price']):,}: {n['volume']:.2f}{marker}")
    lines.append("=== END VOLUME PROFILE ===")
    formatted = "\n".join(lines)

    return {
        "hist": hist,
        "nodes": nodes,
        "poc": float(poc_price),
        "hvn": hvn,
        "lvn": lvn,
        "formatted": formatted,
    }


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


def _format_regime(regime: str) -> str:
    """Return a short regime label for LLM prompt."""
    labels = {
        "ranging_low_vol": "RANGING/LOW-VOL (ideal for grid farming — tight spacing OK)",
        "ranging_high_vol": "RANGING/HIGH-VOL (use wider spacing, fewer levels)",
        "trending_bullish": "TRENDING BULLISH (price climbing — fewer sells above, tighter)",
        "trending_bearish": "TRENDING BEARISH (price falling — caution, reduce size or pause)",
        "choppy": "CHOPPY (directionless but volatile — narrow grid, wide spacing)",
    }
    return labels.get(regime, f"UNKNOWN regime: {regime}")


def detect_regime(candles_15m: List[Dict], candles_4h: List[Dict], adx_data: Dict = None) -> str:
    """Classify market regime: ranging vs trending, low/med/high vol.
    
    Returns one of:
      'ranging_low_vol'     — ideal for grid farming
      'ranging_high_vol'    — grid works but widen spacing
      'trending_bullish'    — grid fights trend, reduce size
      'trending_bearish'    — caution: grid catches knives
      'choppy'              — directionless but volatile, narrow grid
    """
    # --- Trend component ---
    if adx_data is None:
        adx_data = calc_adx(candles_15m)
    
    adx_val = adx_data["adx"]
    plus_di = adx_data["plus_di"]
    minus_di = adx_data["minus_di"]
    
    is_trending = adx_val > 25
    di_diff = plus_di - minus_di
    
    if is_trending:
        if di_diff > 5:
            trend = "bullish"
        elif di_diff < -5:
            trend = "bearish"
        else:
            # ADX says trending but DI+/- are close = choppy
            return "choppy"
    else:
        trend = "ranging"
    
    # --- Volatility component (ATR %) ---
    atr_data = calc_atr(candles_15m, period=14)
    atr_pct = atr_data["atr_pct"] / 100.0  # convert from percent
    
    bb = calc_bollinger_bands(candles_15m)
    bb_width_pct = bb["width_pct"] / 100.0 if bb["width_pct"] > 0 else 0
    
    # Composite vol: average ATR% and BB width% for robustness
    vol = (atr_pct + bb_width_pct) / 2.0
    
    # Thresholds (tuned for BTC perp 15m)
    if vol < 0.008:
        vol_label = "low"
    elif vol > 0.025:
        vol_label = "high"
    else:
        vol_label = "med"
    
    # --- Classify ---
    return f"{trend}_{vol_label}"


def detect_volume_spike(candles: List[Dict], period: int = 20, threshold_mult: float = 2.5) -> dict:
    """Detect volume spike in the latest candle.

    A volume spike is when the latest candle's volume exceeds
    threshold_mult * rolling average volume over the previous `period` candles.

    Args:
        candles: list of OHLCV dicts with keys "ts", "o", "h", "l", "c", "v" (last ~100 candles)
        period: number of candles to use for the rolling average (default: 20)
        threshold_mult: multiplier for spike detection (default: 2.5)

    Returns:
        {
            "is_spike": bool,
            "volume_ratio": float,  # current_vol / avg_vol
            "avg_volume": float,
            "current_volume": float,
            "direction": str,       # "bullish" | "bearish" | "neutral"
            "candle_body_pct": float,  # (close - open) / open * 100
            "label": str,           # human-readable description
            "formatted": str,       # text for LLM prompt
            "mean_reversion_likely": bool  # True if spike and volume_ratio > threshold
        }
    """
    if len(candles) < period + 1:
        return {
            "is_spike": False,
            "volume_ratio": 0.0,
            "avg_volume": 0.0,
            "current_volume": 0.0,
            "direction": "neutral",
            "candle_body_pct": 0.0,
            "label": "Insufficient candle data for volume spike detection",
            "formatted": "Volume Spike: insufficient data",
            "mean_reversion_likely": False,
        }

    # Latest candle
    latest = candles[-1]
    current_vol = latest.get("v", 0)
    current_open = latest.get("o", 0)
    current_close = latest.get("c", 0)

    # Rolling average volume from the previous `period` candles (exclude latest)
    prev_candles = candles[-(period + 1):-1]  # candles before the latest, up to `period` back
    avg_volume = sum(c.get("v", 0) for c in prev_candles) / len(prev_candles) if prev_candles else 0

    if avg_volume == 0:
        return {
            "is_spike": False,
            "volume_ratio": 0.0,
            "avg_volume": 0.0,
            "current_volume": current_vol,
            "direction": "neutral",
            "candle_body_pct": 0.0,
            "label": "Average volume is zero — cannot detect spike",
            "formatted": "Volume Spike: avg volume zero",
            "mean_reversion_likely": False,
        }

    volume_ratio = current_vol / avg_volume
    is_spike = volume_ratio > threshold_mult

    # Determine direction
    if current_close > current_open:
        direction = "bullish"
    elif current_close < current_open:
        direction = "bearish"
    else:
        direction = "neutral"

    # Candle body percentage
    candle_body_pct = ((current_close - current_open) / current_open * 100) if current_open != 0 else 0.0

    # Human-readable label
    if is_spike:
        label = f"Volume spike detected ({volume_ratio:.2f}x average) — {direction}"
    else:
        label = f"Normal volume ({volume_ratio:.2f}x average)"

    # Formatted text for LLM prompt
    if is_spike:
        mean_rev_text = "MEAN REVERSION LIKELY — consider counter-spike grid placement" if volume_ratio > threshold_mult else ""
        formatted = (
            f"⚡ VOLUME SPIKE: {current_vol:.2f} ({volume_ratio:.2f}x avg {avg_volume:.2f})"
            f" | Direction: {direction} (body {candle_body_pct:.2f}%)"
            f" | Mean reversion likely: {is_spike and volume_ratio > threshold_mult}"
        )
        if mean_rev_text:
            formatted += f" | {mean_rev_text}"
    else:
        formatted = f"Volume: normal ({current_vol:.2f}, {volume_ratio:.2f}x avg {avg_volume:.2f})"

    return {
        "is_spike": is_spike,
        "volume_ratio": round(volume_ratio, 3),
        "avg_volume": round(avg_volume, 2),
        "current_volume": round(current_vol, 2),
        "direction": direction,
        "candle_body_pct": round(candle_body_pct, 3),
        "label": label,
        "formatted": formatted,
        "mean_reversion_likely": is_spike and volume_ratio > threshold_mult,
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


# Session schedule for time-of-day awareness
_TIME_SESSIONS = [
    (0, 5, 0.7, "Late Asian (quiet hours, reduce size 30%)"),
    (6, 7, 0.85, "Asian/London transition"),
    (8, 11, 1.0, "London session (normal sizing)"),
    (12, 15, 1.15, "London afternoon (increased activity)"),
    (16, 19, 1.2, "NY/London overlap (high volume, widen grid)"),
    (20, 23, 1.0, "NY close (normal sizing)"),
]


def time_awareness_adjustment(current_time_utc: datetime | None = None) -> dict:
    """Return time-of-day volatility multiplier based on trading session.

    Returns:
        {"adj_multiplier": float, "session_label": str, "description": str}
    """
    from datetime import datetime, timezone

    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)

    hour = current_time_utc.hour

    # Find matching session
    for start, end, mult, desc in _TIME_SESSIONS:
        if start <= hour <= end:
            return {
                "adj_multiplier": mult,
                "session_label": desc,
                "description": desc,
            }

    # Fallback (shouldn't happen with proper session coverage)
    return {
        "adj_multiplier": 1.0,
        "session_label": "Unknown session",
        "description": "Unknown trading session (default sizing)",
    }


def oi_divergence(price_history: list, oi_history: list) -> dict:
    """Detect OI/Price divergence to identify forced liquidation regimes.

    Compares last 12 readings (3 hours) vs the 12 before that (3-6 hours ago).

    Args:
        price_history: last ~50 candles with keys {"ts": int, "c": float}
        oi_history: last ~50 OI readings with keys {"t": int, "oi": float}

    Returns:
        {
            "state": str,  # "long_squeeze" | "capitulation" | "new_shorts" | "new_longs" | "neutral"
            "price_direction": str,  # "up", "down", "flat"
            "oi_direction": str,     # "up", "down", "flat"
            "price_change_pct": float,
            "oi_change_pct": float,
            "label": str,
            "formatted": str,
            "grid_implication": str
        }
    """
    if not price_history or not oi_history or len(price_history) < 24 or len(oi_history) < 24:
        return {
            "state": "neutral",
            "price_direction": "flat",
            "oi_direction": "flat",
            "price_change_pct": 0.0,
            "oi_change_pct": 0.0,
            "label": "Insufficient data for OI divergence analysis",
            "formatted": "OI Divergence: neutral (insufficient data)",
            "grid_implication": "none"
        }

    # Take last 24 readings for both price and OI
    recent_prices = price_history[-12:]
    old_prices = price_history[-24:-12]
    recent_oi = oi_history[-12:]
    old_oi = oi_history[-24:-12]

    # Compute averages
    recent_price_avg = sum(c["c"] for c in recent_prices) / len(recent_prices)
    old_price_avg = sum(c["c"] for c in old_prices) / len(old_prices)
    recent_oi_avg = sum(o["oi"] for o in recent_oi) / len(recent_oi)
    old_oi_avg = sum(o["oi"] for o in old_oi) / len(old_oi)

    # Compute direction (% change)
    if old_price_avg > 0:
        price_change_pct = (recent_price_avg - old_price_avg) / old_price_avg
    else:
        price_change_pct = 0.0

    if old_oi_avg > 0:
        oi_change_pct = (recent_oi_avg - old_oi_avg) / old_oi_avg
    else:
        oi_change_pct = 0.0

    # Classify directions (threshold: 0.1% for flat detection)
    def direction(pct):
        if pct > 0.001:
            return "up"
        elif pct < -0.001:
            return "down"
        else:
            return "flat"

    price_dir = direction(price_change_pct)
    oi_dir = direction(oi_change_pct)

    # Classify the 4 divergence states
    if price_dir == "up" and oi_dir == "down":
        state = "long_squeeze"
        label = f"Price up {price_change_pct*100:.2f}% + OI down {abs(oi_change_pct)*100:.2f}% → Shorts getting squeezed (bullish continuation)"
        grid_implication = "widen sells"
    elif price_dir == "down" and oi_dir == "down":
        state = "capitulation"
        label = f"Price down {abs(price_change_pct)*100:.2f}% + OI down {abs(oi_change_pct)*100:.2f}% → Longs getting liquidated (capitulation)"
        grid_implication = "prepare buys"
    elif price_dir == "down" and oi_dir == "up":
        state = "new_shorts"
        label = f"Price down {abs(price_change_pct)*100:.2f}% + OI up {oi_change_pct*100:.2f}% → New shorts entering (bearish trend)"
        grid_implication = "reduce size"
    elif price_dir == "up" and oi_dir == "up":
        state = "new_longs"
        label = f"Price up {price_change_pct*100:.2f}% + OI up {oi_change_pct*100:.2f}% → New longs entering (bullish trend)"
        grid_implication = "normal grid"
    else:
        state = "neutral"
        label = "Price and OI both flat — no clear divergence signal"
        grid_implication = "none"

    formatted = (
        f"OI Divergence: {state} | Price: {price_dir} ({price_change_pct*100:+.2f}%) | "
        f"OI: {oi_dir} ({oi_change_pct*100:+.2f}%)\n"
        f"  {label}\n"
        f"  Grid hint: {grid_implication}"
    )

    return {
        "state": state,
        "price_direction": price_dir,
        "oi_direction": oi_dir,
        "price_change_pct": round(price_change_pct, 6),
        "oi_change_pct": round(oi_change_pct, 6),
        "label": label,
        "formatted": formatted,
        "grid_implication": grid_implication
    }


def direction_score(
    trend_skew: dict,
    oi_div: dict,
    adx_data: dict,
    funding_data: dict,
    volume_spike: dict,
    regime: str,
    ema_50_4h: float | None,
    ema_50_1d: float | None,
    ema_20_1d: float | None,
    current_price: float,
    funding_rate: float = 0,
) -> dict:
    """Multi-signal direction score (-100 to +100).

    Aggregates trend skew, OI divergence, momentum, funding, and volume
    into a single directional bias.

    Returns:
        {
            "score": int,           # -100 to +100 (positive = LONG, negative = SHORT)
            "direction": str,       # "long" | "short" | "neutral"
            "confidence": str,      # "high" | "medium" | "low"
            "breakdown": dict,      # individual signal scores
            "flags": list[str],     # overrides/halts for directional safety
            "recommendation": str,  # "deploy_long" | "deploy_short" | "pause" | "neutral_prefer_long"
            "formatted": str,       # readable summary for prompt/alerts
        }
    """
    breakdown = {}
    flags = []

    # --- 1. Trend Skew (40%, max +/-40) ---
    raw_trend = trend_skew.get("score", 0)
    trend_score = (raw_trend / 100.0) * 40.0
    trend_score = max(-40, min(40, trend_score))
    breakdown["Trend Skew"] = {"score": round(trend_score, 1), "max": 40}

    # --- 2. OI Divergence (25%, max +/-25) ---
    oi_state = oi_div.get("state", "neutral")
    oi_scores = {
        "new_shorts": -25,
        "capitulation": 15,
        "long_squeeze": 25,
        "new_longs": 20,
        "neutral": 0,
    }
    oi_score = oi_scores.get(oi_state, 0)
    oi_score = max(-25, min(25, oi_score))
    breakdown["OI Divergence"] = {"score": round(oi_score, 1), "max": 25, "state": oi_state}

    # --- 3. Momentum via ADX (15%, max +/-25) ---
    adx_val = adx_data.get("adx", 0)
    plus_di = adx_data.get("plus_di", 0)
    minus_di = adx_data.get("minus_di", 0)

    if adx_val > 25:
        if plus_di > minus_di:
            momentum_score = 25
        else:
            momentum_score = -25
    elif adx_val >= 20:
        scale = (adx_val - 20) / 5.0
        if plus_di > minus_di:
            momentum_score = 15 * scale
        else:
            momentum_score = -15 * scale
    else:
        momentum_score = 0
    momentum_score = max(-25, min(25, momentum_score))
    breakdown["Momentum"] = {"score": round(momentum_score, 1), "max": 25, "adx": adx_val}

    # --- 4. Funding Rate (10%, max +/-15) ---
    # funding_rate is passed as a raw value (e.g., 0.0001 = 0.01%)
    if funding_rate > 0.0001:
        funding_score = -15
    elif funding_rate < -0.0001:
        funding_score = 15
    elif -0.0001 <= funding_rate <= 0:
        if funding_rate == 0:
            funding_score = 0
        else:
            funding_score = 15 * (funding_rate - (-0.0001)) / 0.0001
    elif 0 < funding_rate <= 0.0001:
        funding_score = -15 * (funding_rate / 0.0001)
    else:
        funding_score = 0
    funding_score = max(-15, min(15, funding_score))
    breakdown["Funding"] = {"score": round(funding_score, 1), "max": 15}

    # --- 5. Volume Spike Reversion (10%, max +/-10) ---
    if volume_spike.get("is_spike", False):
        spike_dir = volume_spike.get("direction", "neutral")
        if spike_dir == "bearish":
            volume_score = 10
        elif spike_dir == "bullish":
            volume_score = -10
        else:
            volume_score = 0
    else:
        volume_score = 0
    volume_score = max(-10, min(10, volume_score))
    breakdown["Volume Spike"] = {"score": round(volume_score, 1), "max": 10}

    # --- Sum raw score ---
    score = trend_score + oi_score + momentum_score + funding_score + volume_score

    # --- Apply regime / EMA overrides ---
    if regime == "trending_bearish" and score > 0:
        score -= 15
        flags.append("bearish_regime_reduction")
    elif regime == "trending_bullish" and score < 0:
        score += 15
        flags.append("bullish_regime_reduction")

    # EMA cross adjustments
    if ema_20_1d is not None and ema_50_1d is not None:
        if ema_20_1d < ema_50_1d and score > 0:
            score -= 10
            flags.append("death_cross_reduction")
        elif ema_20_1d > ema_50_1d and score < 0:
            score += 10
            flags.append("golden_cross_reduction")

    # OI hard flags
    if oi_state == "capitulation" and score < -20:
        flags.append("no_shorts_during_capitulation")
        score = max(score, -20) * 0.5
    elif oi_state == "long_squeeze" and score < 0:
        flags.append("no_shorts_during_squeeze")
        score = max(score, 0)

    # Clamp final score
    score = max(-100, min(100, int(round(score))))

    # --- Direction & recommendation ---
    if score > 15:
        direction = "long"
        recommendation = "deploy_long"
    elif score < -15:
        direction = "short"
        recommendation = "deploy_short"
    else:
        direction = "neutral"
        recommendation = "neutral_prefer_long"

    # --- Confidence ---
    abs_score = abs(score)
    if abs_score > 50:
        confidence = "high"
    elif abs_score > 25:
        confidence = "medium"
    else:
        confidence = "low"

    # --- Formatted output ---
    def fmt_component(name, comp):
        s = comp["score"]
        m = comp["max"]
        tag = f" ({comp.get('state', '')})" if "state" in comp else ""
        return f"  {name}: {s:+.1f}/{m}{tag}"

    lines = []
    lines.append("=== DIRECTION SCORE ===")
    bias_word = "LONG" if direction == "long" else ("SHORT" if direction == "short" else "NEUTRAL")
    lines.append(f"Score: {score:+d} ({bias_word} bias) | Confidence: {confidence}")
    lines.append("Components:")
    lines.append(fmt_component("Trend Skew", breakdown["Trend Skew"]))
    lines.append(fmt_component("OI Divergence", breakdown["OI Divergence"]))

    adx_val_show = breakdown["Momentum"].get("adx", 0)
    mom_dir = ""
    if adx_val_show > 25:
        if plus_di > minus_di:
            mom_dir = "(ADX strong bullish)"
        else:
            mom_dir = "(ADX strong bearish)"
    elif adx_val_show >= 20:
        mom_dir = "(ADX weak)"
    else:
        mom_dir = "(ADX flat)"
    lines.append(f"  Momentum:      {breakdown['Momentum']['score']:+.1f}/25 {mom_dir}")

    funding_label = ""
    if funding_rate > 0.0001:
        funding_label = "(crowded longs)"
    elif funding_rate < -0.0001:
        funding_label = "(crowded shorts)"
    else:
        funding_label = "(neutral funding)"
    lines.append(f"  Funding:       {breakdown['Funding']['score']:+.1f}/15 {funding_label}")

    vol_spike_status = "normal volume" if not volume_spike.get("is_spike", False) else f"spike ({volume_spike.get('direction', 'neutral')})"
    lines.append(f"  Volume Spike:   {breakdown['Volume Spike']['score']:+.1f}/10 ({vol_spike_status})")

    flag_text = ", ".join(flags) if flags else "[none]"
    lines.append(f"Flags: {flag_text}")
    lines.append(f"Recommendation: {recommendation} grid")
    lines.append("=== END DIRECTION SCORE ===")
    formatted = "\n".join(lines)

    return {
        "score": score,
        "direction": direction,
        "confidence": confidence,
        "breakdown": breakdown,
        "flags": flags,
        "recommendation": recommendation,
        "formatted": formatted,
    }


def gather_indicators(candles_15m: List[Dict], candles_30m: List[Dict],
                       candles_4h: List[Dict], market_intel: Dict, candles_1d: List[Dict] = None) -> Dict:
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

    # Regime detection
    regime = detect_regime(candles_15m, candles_4h, adx)
    regime_labels = {
        "ranging_low_vol": "🟢 Ranging, low volatility — ideal for grid",
        "ranging_high_vol": "🟡 Ranging, high volatility — widen spacing",
        "trending_bullish": "📈 Bullish trend — bias sells above",
        "trending_bearish": "📉 Bearish trend — caution, narrow grid",
        "choppy": "⚡ Choppy — directionless volatility, fewer levels",
    }
    regime_line = f"\n🔍 Market Regime: {regime} — {regime_labels.get(regime, regime)}"
    formatted = formatted + regime_line

    # Volume Profile (combine all timeframes for broader coverage)
    combined_candles = list(candles_15m) + list(candles_30m) + list(candles_4h)
    if candles_1d:
        combined_candles += list(candles_1d)
    current_price = candles_15m[-1]["c"]
    vp = calc_volume_profile(combined_candles, current_price, bin_size=50.0)
    vp_formatted = vp["formatted"]
    formatted = formatted + f"\n\n{vp_formatted}"

    # --- 1D indicators ---
    if candles_1d and len(candles_1d) >= 50:
        ema_50_1d = calc_ema_single(candles_1d, 50)
        ema_20_1d = calc_ema_single(candles_1d, 20)

        if ema_50_1d and ema_20_1d:
            if ema_20_1d > ema_50_1d:
                daily_trend = "bullish"
            elif ema_20_1d < ema_50_1d:
                daily_trend = "bearish"
            else:
                daily_trend = "neutral"
        else:
            daily_trend = "no_data"
            ema_50_1d = None
            ema_20_1d = None
    else:
        daily_trend = "no_data"
        ema_50_1d = None
        ema_20_1d = None

    # Format daily trend
    if candles_1d and ema_50_1d:
        daily_line = f"\n📊 Daily EMA(20): ${ema_20_1d:,.0f} | EMA(50): ${ema_50_1d:,.0f} | Trend: {daily_trend}"
    else:
        daily_line = "\n📊 Daily: no data"
    formatted = formatted + daily_line

    # Time-of-day awareness
    time_adj = time_awareness_adjustment()
    time_line = f"\n⏰ Time awareness: {time_adj['adj_multiplier']}x — {time_adj['session_label']}"
    formatted = formatted + time_line

    # Funding rate context
    funding_adj = funding_rate_adjustment(market_intel, candles_15m[-1]["c"] if candles_15m else 0)
    funding_line = f"\n💰 {funding_adj['formatted']}"
    formatted = formatted + funding_line

    # Volume spike detection (15m candles)
    volume_spike = detect_volume_spike(candles_15m)
    spike_line = f"\n{volume_spike['formatted']}"
    formatted = formatted + spike_line

    # OI divergence analysis
    oi_history = market_intel.get("oi_history_15min", [])
    price_history = [{"ts": c["ts"], "c": c["c"]} for c in candles_15m[-50:]]
    oi_div = oi_divergence(price_history, oi_history)
    oi_div_line = f"\n\n📊 {oi_div['formatted']}"
    formatted = formatted + oi_div_line

    # Direction score computation
    raw_funding = market_intel.get("current", {}).get("funding_rate", 0)
    dir_score = direction_score(
        trend_skew=skew,
        oi_div=oi_div,
        adx_data=adx,
        funding_data=funding_adj,
        volume_spike=volume_spike,
        regime=regime,
        ema_50_4h=ema_50_4h,
        ema_50_1d=ema_50_1d,
        ema_20_1d=ema_20_1d,
        current_price=current_price,
        funding_rate=raw_funding,
    )

    return {
        "bollinger": bands,
        "atr": atr,
        "adx": adx,
        "skew": skew,
        "ema_50_4h": ema_50_4h,
        "ema_50_1d": ema_50_1d,
        "ema_20_1d": ema_20_1d,
        "daily_trend": daily_trend,
        "trend": trend,
        "regime": regime,
        "volume_profile": vp,
        "time_awareness": time_adj,
        "funding_adj": funding_adj,
        "volume_spike": volume_spike,
        "oi_divergence": oi_div,
        "direction_score": dir_score,
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
