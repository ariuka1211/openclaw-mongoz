"""Market regime detection."""

from typing import List, Dict

from .adx import calc_adx
from .atr import calc_atr
from .bollinger import calc_bollinger_bands


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
