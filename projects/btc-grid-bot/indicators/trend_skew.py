"""Trend skew score calculation."""

from typing import Dict, List

from .bollinger import calc_bollinger_bands
from .adx import calc_adx


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
