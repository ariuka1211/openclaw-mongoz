"""Direction score calculation."""

from typing import Dict, List


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
