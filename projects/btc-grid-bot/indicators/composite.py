"""Composite indicator gathering - combines all indicators."""

import logging
from typing import Dict, List

from .bollinger import calc_bollinger_bands
from .atr import calc_atr
from .adx import calc_adx
from .ema import calc_ema_single
from .trend_skew import calc_trend_skew
from .volume import calc_volume_profile, detect_volume_spike
from .regime import detect_regime
from .funding import funding_rate_adjustment
from .time_adj import time_awareness_adjustment
from .oi_divergence import oi_divergence
from .direction_score import direction_score
from .format_indicators import format_indicators, _format_regime

logger = logging.getLogger(__name__)


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
    from analysis.analyst import fetch_candles
    from market.intel import gather_all_intel
    
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
