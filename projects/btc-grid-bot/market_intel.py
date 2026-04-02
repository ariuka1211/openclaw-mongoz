"""
BTC Grid Bot — Market Intelligence Module

Fetches market intelligence data from Coinalyze API:
- Open interest
- Funding rates
- Liquidation history
- Long/short ratios
- Higher timeframe candles for support/resistance

Enhances AI analyst decisions with market structure data.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

import httpx


logger = logging.getLogger(__name__)


class CoinalyzeClient:
    """Async client for Coinalyze API with rate limiting."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.coinalyze.net/v1"
        self.symbol = "BTCUSDT_PERP.A"
        self.client = httpx.AsyncClient(timeout=30)
        self.last_call_time = 0
        self.rate_limit_delay = 1.6  # 40 req/min = 1.5s min, use 1.6s for safety
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
    
    async def _enforce_rate_limit(self):
        """Ensure minimum delay between API calls."""
        now = time.time()
        elapsed = now - self.last_call_time
        if elapsed < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - elapsed
            await asyncio.sleep(sleep_time)
        self.last_call_time = time.time()
    
    async def _get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make rate-limited GET request to Coinalyze API."""
        await self._enforce_rate_limit()
        
        if params is None:
            params = {}
        
        params["api_key"] = self.api_key
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Coinalyze API error for {endpoint}: {e}")
            return None
    
    async def get_current_data(self) -> Dict:
        """Fetch current market data: OI, funding rate, predicted funding."""
        result = {
            "open_interest_usd": 0.0,
            "funding_rate": 0.0,
            "predicted_funding": 0.0,
        }
        
        # 1. Open Interest
        oi_data = await self._get("open-interest", {
            "symbols": self.symbol,
            "convert_to_usd": "true"
        })
        if oi_data and isinstance(oi_data, list):
            for item in oi_data:
                if item.get("symbol") == self.symbol:
                    result["open_interest_usd"] = float(item.get("value", 0))
                    break
        
        # 2. Current funding rate
        funding_data = await self._get("funding-rate", {"symbols": self.symbol})
        if funding_data and isinstance(funding_data, list):
            for item in funding_data:
                if item.get("symbol") == self.symbol:
                    result["funding_rate"] = float(item.get("value", 0))
                    break
        
        # 3. Predicted funding rate
        pred_funding_data = await self._get("predicted-funding-rate", {"symbols": self.symbol})
        if pred_funding_data and isinstance(pred_funding_data, list):
            for item in pred_funding_data:
                if item.get("symbol") == self.symbol:
                    result["predicted_funding"] = float(item.get("value", 0))
                    break
        
        return result
    
    async def get_liquidation_history(self, hours: int = 48) -> List[Dict]:
        """Fetch liquidation history for last N hours."""
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(hours=hours)
        
        params = {
            "symbols": self.symbol,
            "interval": "1hour",
            "from": int(from_time.timestamp()),
            "to": int(now.timestamp())
        }
        
        data = await self._get("liquidation-history", params)
        if not data or not isinstance(data, list):
            return []
        
        result = []
        for item in data:
            if item.get("symbol") == self.symbol:
                history = item.get("history", [])
                for entry in history:
                    result.append({
                        "t": entry.get("t"),
                        "long_liqs": float(entry.get("l", 0)) * 1e6,  # Convert to USD
                        "short_liqs": float(entry.get("s", 0)) * 1e6  # Convert to USD
                    })
        
        return sorted(result, key=lambda x: x["t"])
    
    async def get_long_short_ratio(self, hours: int = 48) -> List[Dict]:
        """Fetch long/short ratio history."""
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(hours=hours)
        
        params = {
            "symbols": self.symbol,
            "interval": "1hour",
            "from": int(from_time.timestamp()),
            "to": int(now.timestamp())
        }
        
        data = await self._get("long-short-ratio-history", params)
        if not data or not isinstance(data, list):
            return []
        
        result = []
        for item in data:
            if item.get("symbol") == self.symbol:
                history = item.get("history", [])
                for entry in history:
                    result.append({
                        "t": entry.get("t"),
                        "long_ratio": float(entry.get("v", 0.5))
                    })
        
        return sorted(result, key=lambda x: x["t"])
    
    async def get_oi_history(self, hours: int = 48) -> List[Dict]:
        """Fetch open interest history."""
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(hours=hours)
        
        params = {
            "symbols": self.symbol,
            "interval": "1hour",
            "from": int(from_time.timestamp()),
            "to": int(now.timestamp())
        }
        
        data = await self._get("open-interest-history", params)
        if not data or not isinstance(data, list):
            return []
        
        result = []
        for item in data:
            if item.get("symbol") == self.symbol:
                history = item.get("history", [])
                for entry in history:
                    result.append({
                        "t": entry.get("t"),
                        "oi_usd": float(entry.get("v", 0))
                    })
        
        return sorted(result, key=lambda x: x["t"])
    
    async def get_higher_tf_candles(self, interval: str = "4hour", hours: int = 168) -> List[Dict]:
        """Fetch higher timeframe candles for major S/R identification."""
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(hours=hours)
        
        params = {
            "symbols": self.symbol,
            "interval": interval,
            "from": int(from_time.timestamp()),
            "to": int(now.timestamp())
        }
        
        data = await self._get("ohlcv-history", params)
        if not data or not isinstance(data, list):
            return []
        
        result = []
        for item in data:
            if item.get("symbol") == self.symbol:
                history = item.get("history", [])
                for entry in history:
                    result.append({
                        "ts": entry.get("t"),
                        "o": float(entry.get("o", 0)),
                        "h": float(entry.get("h", 0)),
                        "l": float(entry.get("l", 0)),
                        "c": float(entry.get("c", 0)),
                        "v": float(entry.get("v", 0))
                    })
        
        return sorted(result, key=lambda x: x["ts"])


def format_market_intel(data: Dict) -> str:
    """Format market intelligence data for LLM prompt injection."""
    current = data.get("current", {})
    liquidations = data.get("liquidations", [])
    ls_ratio = data.get("long_short_ratio", [])
    oi_history = data.get("oi_history", [])
    
    # Format open interest
    oi_usd = current.get("open_interest_usd", 0)
    oi_str = f"${oi_usd/1e9:.1f}B" if oi_usd >= 1e9 else f"${oi_usd/1e6:.0f}M"
    
    # OI trend
    oi_trend = "stable"
    if len(oi_history) >= 2:
        latest_oi = oi_history[-1].get("oi_usd", 0)
        prev_oi = oi_history[-2].get("oi_usd", 0) if len(oi_history) >= 2 else latest_oi
        if latest_oi > prev_oi * 1.02:
            oi_trend = "↑ rising — more leverage entering"
        elif latest_oi < prev_oi * 0.98:
            oi_trend = "↓ falling — leverage exiting"
        else:
            oi_trend = "→ stable"
    
    # Format funding rate
    funding_rate = current.get("funding_rate", 0)
    funding_pct = funding_rate * 100
    funding_meaning = ""
    if funding_rate > 0.0001:  # > 0.01%
        funding_meaning = " (longs paying shorts — crowded long)"
    elif funding_rate < -0.0001:  # < -0.01%
        funding_meaning = " (shorts paying longs — crowded short)"
    else:
        funding_meaning = " (neutral)"
    
    # Predicted funding
    pred_funding = current.get("predicted_funding", 0)
    pred_funding_pct = pred_funding * 100
    
    # Long/short ratio
    current_ls = "50% long"
    crowded_status = ""
    if ls_ratio:
        latest_ls = ls_ratio[-1].get("long_ratio", 0.5)
        ls_pct = latest_ls * 100
        current_ls = f"{ls_pct:.0f}% long"
        if latest_ls > 0.6:
            crowded_status = " (crowded)"
        elif latest_ls < 0.4:
            crowded_status = " (crowded short)"
    
    # 24h liquidations summary
    liq_summary = "$0 longs / $0 shorts"
    if liquidations:
        # Sum last 24h
        now = time.time()
        day_ago = now - 86400
        total_long_liqs = 0
        total_short_liqs = 0
        
        for liq in liquidations:
            if liq.get("t", 0) >= day_ago:
                total_long_liqs += liq.get("long_liqs", 0)
                total_short_liqs += liq.get("short_liqs", 0)
        
        long_str = f"${total_long_liqs/1e6:.1f}M" if total_long_liqs >= 1e6 else f"${total_long_liqs/1e3:.0f}K"
        short_str = f"${total_short_liqs/1e6:.1f}M" if total_short_liqs >= 1e6 else f"${total_short_liqs/1e3:.0f}K"
        liq_summary = f"{long_str} longs / {short_str} shorts"
        
        # Add insight
        if total_long_liqs > total_short_liqs * 2:
            liq_summary += " (more long liqs)"
        elif total_short_liqs > total_long_liqs * 2:
            liq_summary += " (more short liqs)"
    
    # Generate key insight
    insight = "Market conditions normal."
    if funding_rate > 0.0003 and ls_ratio and ls_ratio[-1].get("long_ratio", 0.5) > 0.6:
        insight = "High funding + crowded long = short squeeze risk above. Long liquidation cluster likely at support breaks."
    elif funding_rate < -0.0003 and ls_ratio and ls_ratio[-1].get("long_ratio", 0.5) < 0.4:
        insight = "Negative funding + crowded short = long squeeze risk below. Short liquidation cluster likely at resistance breaks."
    elif oi_trend.startswith("↑") and funding_rate > 0.0002:
        insight = "Rising leverage + positive funding = potential long squeeze on any rejection."
    elif oi_trend.startswith("↓"):
        insight = "Falling leverage = reduced volatility, cleaner technical levels."
    
    return f"""=== MARKET INTEL ===
Open Interest: {oi_str} ({oi_trend})
Funding Rate: {funding_pct:+.3f}%{funding_meaning}
Predicted Funding: {pred_funding_pct:+.3f}%
Long/Short Ratio: {current_ls}{crowded_status}
24h Liquidations: {liq_summary}
Key Insight: {insight}
=== END INTEL ==="""


async def gather_all_intel(cfg: Dict) -> Dict:
    """Orchestrate all Coinalyze API calls and return formatted data."""
    # Get API key from config, env, or fallback
    api_key = (
        cfg.get("coinalyze", {}).get("api_key") or
        os.environ.get("COINALYZE_API_KEY") or
        "0b84b892-eec9-4629-b55c-54adc3639ae9"
    )
    
    result = {
        "current": {},
        "liquidations": [],
        "long_short_ratio": [],
        "oi_history": [],
        "higher_tf_candles": [],
        "formatted": "=== MARKET INTEL ===\nData unavailable\n=== END INTEL ===",
        "error": None
    }
    
    try:
        async with CoinalyzeClient(api_key) as client:
            # Fetch all data
            result["current"] = await client.get_current_data()
            result["liquidations"] = await client.get_liquidation_history(48)
            result["long_short_ratio"] = await client.get_long_short_ratio(48)
            result["oi_history"] = await client.get_oi_history(48)
            result["higher_tf_candles"] = await client.get_higher_tf_candles("4hour", 168)
            
            # Format for prompt
            result["formatted"] = format_market_intel(result)
            
    except Exception as e:
        logger.warning(f"Failed to gather market intelligence: {e}")
        result["error"] = str(e)
        result["formatted"] = "=== MARKET INTEL ===\nData temporarily unavailable\n=== END INTEL ==="
    
    return result


async def main():
    """Standalone testing."""
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        cfg = {}
    
    result = await gather_all_intel(cfg)
    print("=== RAW DATA ===")
    for key, value in result.items():
        if key != "formatted":
            print(f"{key}: {value}")
    
    print("\n=== FORMATTED OUTPUT ===")
    print(result["formatted"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())