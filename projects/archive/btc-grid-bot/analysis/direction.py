"""Direction scoring - multi-signal market direction check."""

import logging

from analysis.analyst import fetch_candles
from market.intel import gather_all_intel
from indicators import gather_indicators, direction_score

logger = logging.getLogger("btc-grid.direction")


async def check_direction(cfg: dict, price: float) -> dict:
    """Multi-signal direction check using all available indicators.
    
    Returns a dict with:
      - "direction": "long" | "short" | "neutral"
      - "score": int (-100 to +100)
      - "confidence": "high" | "medium" | "low"
      - "recommendation": "deploy_long" | "deploy_short" | "pause" | "neutral_prefer_long"
      - "flags": list of safety override strings
    """
    # Fetch candles
    candles_15m = await fetch_candles("15m", limit=200)
    candles_30m = await fetch_candles("30m", limit=200)
    candles_4h = await fetch_candles("4H", limit=48)
    try:
        candles_1d = await fetch_candles("1D", limit=90)
    except Exception:
        candles_1d = []
    
    # Get market intel (funding, OI)
    market_intel = await gather_all_intel(cfg)
    indicators = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel, candles_1d)
    
    # Extract individual indicator components for direction_score
    raw_funding = market_intel.get("current", {}).get("funding_rate", 0)
    score_result = direction_score(
        trend_skew=indicators["skew"],
        oi_div=indicators["oi_divergence"],
        adx_data=indicators["adx"],
        funding_data=indicators["funding_adj"],
        volume_spike=indicators["volume_spike"],
        regime=indicators["regime"],
        ema_50_4h=indicators["ema_50_4h"],
        ema_50_1d=indicators["ema_50_1d"],
        ema_20_1d=indicators["ema_20_1d"],
        current_price=price,
        funding_rate=raw_funding,
    )
    
    # Log the result
    logger.info("Direction check: score=%s, direction=%s, recommendation=%s, confidence=%s",
             score_result['score'], score_result['direction'],
             score_result['recommendation'], score_result['confidence'])
    
    return score_result
