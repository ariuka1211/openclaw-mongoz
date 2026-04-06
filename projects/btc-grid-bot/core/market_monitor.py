"""
BTC Grid Bot — Market Monitor

Lightweight polling (every 2 minutes) that detects actionable market conditions.
Does NOT trade, does NOT call AI, does NOT place orders.
Uses OKX public candles (no auth needed) for market data.

Returns MarketSnapshot with trigger flags.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"


@dataclass
class MarketSnapshot:
    """Market conditions snapshot with trigger flags."""
    price: float                        # current BTC price
    grid_center: float                  # midpoint of buy + sell levels
    price_drift_pct: float              # |price - grid_center| / grid_center
    near_edge: bool                     # price within 5% of range_high or range_low
    near_top: bool                      # price within 5% of range_high
    near_bottom: bool                   # price within 5% of range_low
    fills_in_last_hour: int             # count from grid_state trades
    fill_drought: bool                  # 0 fills in 2+ hours
    hours_since_last_fill: float        # hours since most recent trade
    volume_spike: bool                  # 5m vol > 3x average
    volume_ratio: float                 # current_5m_vol / avg_5m_vol
    atr_5m: float                       # 14-period ATR on 5m
    atr_1h: float                       # 14-period ATR on 1h
    vol_ratio_atr: float                # atr_5m / atr_1h (spiking if > 2.0)
    timestamp: float                    # unix timestamp


class MarketMonitor:
    """Lightweight market condition monitor for the BTC grid bot."""
    
    def __init__(self, api, grid_state: dict):
        """Initialize the market monitor.
        
        Args:
            api: LighterAPI instance (for potential future use)
            grid_state: Current grid state dictionary
        """
        self.api = api
        self.grid_state = grid_state
        
    def poll(self) -> MarketSnapshot:
        """Poll market conditions and return snapshot with trigger flags.
        
        Returns:
            MarketSnapshot with current market conditions and flags
        """
        try:
            # Fetch market data
            price = self._get_current_price()
            volume_data = self._get_volume_data()
            atr_data = self._get_atr_data()
            
            # Calculate grid metrics
            grid_center = self._calculate_grid_center()
            price_drift_pct = abs(price - grid_center) / grid_center * 100 if grid_center > 0 else 0
            
            # Edge proximity checks
            range_high = self.grid_state.get("range_high", 0)
            range_low = self.grid_state.get("range_low", 0)
            near_top, near_bottom, near_edge = self._check_edge_proximity(price, range_high, range_low)
            
            # Fill analysis
            fills_in_last_hour, hours_since_last_fill = self._analyze_fills()
            fill_drought = fills_in_last_hour == 0 and hours_since_last_fill >= 2.0
            
            # Volume spike detection
            volume_spike = volume_data["volume_spike"]
            volume_ratio = volume_data["volume_ratio"]
            
            # ATR volatility analysis
            atr_5m = atr_data["atr_5m"]
            atr_1h = atr_data["atr_1h"]
            vol_ratio_atr = atr_5m / atr_1h if atr_1h > 0 else 0
            
            return MarketSnapshot(
                price=price,
                grid_center=grid_center,
                price_drift_pct=price_drift_pct,
                near_edge=near_edge,
                near_top=near_top,
                near_bottom=near_bottom,
                fills_in_last_hour=fills_in_last_hour,
                fill_drought=fill_drought,
                hours_since_last_fill=hours_since_last_fill,
                volume_spike=volume_spike,
                volume_ratio=volume_ratio,
                atr_5m=atr_5m,
                atr_1h=atr_1h,
                vol_ratio_atr=vol_ratio_atr,
                timestamp=time.time()
            )
            
        except Exception as e:
            logger.warning(f"Error polling market conditions: {e}")
            # Return safe snapshot on error
            return MarketSnapshot(
                price=0.0,
                grid_center=0.0,
                price_drift_pct=0.0,
                near_edge=False,
                near_top=False,
                near_bottom=False,
                fills_in_last_hour=0,
                fill_drought=False,
                hours_since_last_fill=0.0,
                volume_spike=False,
                volume_ratio=0.0,
                atr_5m=0.0,
                atr_1h=0.0,
                vol_ratio_atr=0.0,
                timestamp=time.time()
            )
    
    def _get_current_price(self) -> float:
        """Fetch current BTC price from OKX public API."""
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(OKX_CANDLES_URL, params={
                    "instId": "BTC-USDT",
                    "bar": "1m",
                    "limit": "1"
                })
                resp.raise_for_status()
                
                data = resp.json()
                if data.get("code") != "0":
                    raise ValueError(f"OKX API error: {data.get('msg', data)}")
                
                candles = data.get("data", [])
                if not candles:
                    raise ValueError("No candle data received")
                
                # Return close price of latest candle
                return float(candles[0][4])
                
        except Exception as e:
            logger.warning(f"Failed to fetch current price: {e}")
            return 0.0
    
    def _get_volume_data(self) -> Dict[str, float]:
        """Fetch 5m volume data and detect spikes."""
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(OKX_CANDLES_URL, params={
                    "instId": "BTC-USDT",
                    "bar": "5m",
                    "limit": "21"  # Current + 20 for average
                })
                resp.raise_for_status()
                
                data = resp.json()
                if data.get("code") != "0":
                    raise ValueError(f"OKX API error: {data.get('msg', data)}")
                
                candles = data.get("data", [])
                if len(candles) < 21:
                    return {"volume_spike": False, "volume_ratio": 0.0}
                
                # Candles are newest first, so reverse for chronological order
                candles.reverse()
                
                # Current volume (latest candle)
                current_volume = float(candles[-1][5])
                
                # Average volume of previous 20 candles
                previous_volumes = [float(candle[5]) for candle in candles[-21:-1]]
                avg_volume = sum(previous_volumes) / len(previous_volumes)
                
                volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
                volume_spike = volume_ratio > 3.0
                
                return {
                    "volume_spike": volume_spike,
                    "volume_ratio": volume_ratio
                }
                
        except Exception as e:
            logger.warning(f"Failed to fetch volume data: {e}")
            return {"volume_spike": False, "volume_ratio": 0.0}
    
    def _get_atr_data(self) -> Dict[str, float]:
        """Fetch ATR data for 5m and 1h timeframes."""
        try:
            # Fetch 5m data for short-term ATR
            atr_5m = self._calculate_atr("5m", 15)  # 15 candles for 14-period ATR
            
            # Fetch 1h data for long-term ATR
            atr_1h = self._calculate_atr("1H", 15)  # 15 candles for 14-period ATR
            
            return {
                "atr_5m": atr_5m,
                "atr_1h": atr_1h
            }
            
        except Exception as e:
            logger.warning(f"Failed to fetch ATR data: {e}")
            return {"atr_5m": 0.0, "atr_1h": 0.0}
    
    def _calculate_atr(self, timeframe: str, limit: int) -> float:
        """Calculate ATR for given timeframe."""
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(OKX_CANDLES_URL, params={
                    "instId": "BTC-USDT",
                    "bar": timeframe,
                    "limit": str(limit)
                })
                resp.raise_for_status()
                
                data = resp.json()
                if data.get("code") != "0":
                    raise ValueError(f"OKX API error: {data.get('msg', data)}")
                
                candles = data.get("data", [])
                if len(candles) < 2:
                    return 0.0
                
                # Reverse to chronological order
                candles.reverse()
                
                # Calculate True Range for each candle
                true_ranges = []
                for i in range(1, len(candles)):
                    high = float(candles[i][2])
                    low = float(candles[i][3])
                    prev_close = float(candles[i-1][4])
                    
                    tr = max(
                        high - low,
                        abs(high - prev_close),
                        abs(low - prev_close)
                    )
                    true_ranges.append(tr)
                
                if not true_ranges:
                    return 0.0
                
                # Calculate ATR using Wilder's smoothing (14-period)
                period = min(14, len(true_ranges))
                if len(true_ranges) < period:
                    return sum(true_ranges) / len(true_ranges)
                
                atr = sum(true_ranges[:period]) / period
                for i in range(period, len(true_ranges)):
                    atr = (atr * (period - 1) + true_ranges[i]) / period
                
                return atr
                
        except Exception as e:
            logger.warning(f"Failed to calculate ATR for {timeframe}: {e}")
            return 0.0
    
    def _calculate_grid_center(self) -> float:
        """Calculate center of current grid levels."""
        levels = self.grid_state.get("levels", {})
        buy_levels = levels.get("buy", [])
        sell_levels = levels.get("sell", [])
        
        if not buy_levels and not sell_levels:
            return 0.0
        
        all_levels = buy_levels + sell_levels
        if not all_levels:
            return 0.0
        
        return sum(all_levels) / len(all_levels)
    
    def _check_edge_proximity(self, price: float, range_high: float, range_low: float) -> tuple[bool, bool, bool]:
        """Check if price is near grid edges.
        
        Returns:
            (near_top, near_bottom, near_edge)
        """
        if range_high <= 0 or range_low <= 0:
            return False, False, False
        
        # 5% proximity threshold
        high_threshold = range_high * 0.95
        low_threshold = range_low * 1.05
        
        near_top = price >= high_threshold
        near_bottom = price <= low_threshold
        near_edge = near_top or near_bottom
        
        return near_top, near_bottom, near_edge
    
    def _analyze_fills(self) -> tuple[int, float]:
        """Analyze recent fills and calculate metrics.
        
        Returns:
            (fills_in_last_hour, hours_since_last_fill)
        """
        trades = self.grid_state.get("trades", [])
        if not trades:
            return 0, 24.0  # Default to 24 hours if no trades
        
        current_time = time.time()
        one_hour_ago = current_time - 3600
        
        # Count fills in last hour
        fills_in_last_hour = 0
        last_fill_time = 0
        
        for trade in trades:
            # Handle different timestamp formats
            ts_str = trade.get("ts", trade.get("timestamp", ""))
            if isinstance(ts_str, str) and ts_str:
                try:
                    from datetime import datetime
                    trade_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    trade_time = trade_dt.timestamp()
                except Exception:
                    continue
            elif isinstance(ts_str, (int, float)):
                trade_time = float(ts_str)
            else:
                continue
                
            if trade_time > one_hour_ago:
                fills_in_last_hour += 1
            
            # Track most recent fill
            if trade_time > last_fill_time:
                last_fill_time = trade_time
        
        # Calculate hours since last fill
        if last_fill_time > 0:
            hours_since_last_fill = (current_time - last_fill_time) / 3600
        else:
            hours_since_last_fill = 24.0  # No fills found
        
        return fills_in_last_hour, hours_since_last_fill


def create_monitor(api, grid_state: dict) -> MarketMonitor:
    """Factory function to create a MarketMonitor instance.
    
    Args:
        api: LighterAPI instance
        grid_state: Current grid state dictionary
        
    Returns:
        MarketMonitor instance
    """
    return MarketMonitor(api, grid_state)


# Example usage
if __name__ == "__main__":
    # Mock grid state for testing
    mock_grid_state = {
        "levels": {"buy": [95000, 94000, 93000], "sell": [96000, 97000, 98000]},
        "range_high": 100000,
        "range_low": 90000,
        "trades": [
            {"timestamp": time.time() - 1800, "side": "buy", "price": 95000},
            {"timestamp": time.time() - 3600, "side": "sell", "price": 96000}
        ]
    }
    
    monitor = MarketMonitor(None, mock_grid_state)
    snapshot = monitor.poll()
    
    print("Market Snapshot:")
    print(f"Price: ${snapshot.price:,.2f}")
    print(f"Grid Center: ${snapshot.grid_center:,.2f}")
    print(f"Price Drift: {snapshot.price_drift_pct:.2f}%")
    print(f"Near Edge: {snapshot.near_edge}")
    print(f"Volume Spike: {snapshot.volume_spike} (ratio: {snapshot.volume_ratio:.2f})")
    print(f"Fill Drought: {snapshot.fill_drought} ({snapshot.hours_since_last_fill:.1f}h since last)")
    print(f"ATR Ratio: {snapshot.vol_ratio_atr:.2f} (5m: {snapshot.atr_5m:.2f}, 1h: {snapshot.atr_1h:.2f})")