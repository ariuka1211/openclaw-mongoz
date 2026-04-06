"""
BTC Grid Bot — Snapshot Bridge

Converts between MarketMonitor MarketSnapshot and TriggerEngine MarketSnapshot.
"""

from core.market_monitor import MarketSnapshot as MonitorSnapshot
from core.trigger_engine import MarketSnapshot as TriggerSnapshot


def convert_snapshot(monitor_snapshot: MonitorSnapshot, grid_state: dict) -> TriggerSnapshot:
    """Convert MarketMonitor snapshot to TriggerEngine format."""
    
    # Get market hours
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    hour = now.hour
    
    # US market hours: 14:30-21:00 UTC (9:30am-4pm EST)
    if 14 <= hour <= 21:
        market_hours = "us_session"
    elif 0 <= hour <= 8:  # Asia session
        market_hours = "asia_session"
    else:
        market_hours = "off_hours"
    
    # Calculate last fill time
    trades = grid_state.get("trades", [])
    last_fill_time = 0
    fill_count_24h = 0
    current_time = monitor_snapshot.timestamp
    
    for trade in trades:
        # Handle different timestamp formats
        ts_str = trade.get("ts", trade.get("timestamp", ""))
        if isinstance(ts_str, str) and ts_str:
            try:
                trade_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                trade_time = trade_dt.timestamp()
            except Exception:
                continue
        elif isinstance(ts_str, (int, float)):
            trade_time = float(ts_str)
        else:
            continue
            
        if trade_time > last_fill_time:
            last_fill_time = trade_time
            
        # Count fills in last 24h
        if current_time - trade_time < 86400:  # 24 hours
            fill_count_24h += 1
    
    return TriggerSnapshot(
        timestamp=monitor_snapshot.timestamp,
        price=monitor_snapshot.price,
        volume_5m=monitor_snapshot.volume_ratio * 100,  # Approximate
        volume_1h_avg=100,  # Baseline
        atr_5m=monitor_snapshot.atr_5m,
        atr_1h=monitor_snapshot.atr_1h,
        grid_center=monitor_snapshot.grid_center,
        grid_low=grid_state.get("range_low", 0),
        grid_high=grid_state.get("range_high", 0),
        last_fill_time=last_fill_time,
        fill_count_24h=fill_count_24h,
        market_hours=market_hours
    )