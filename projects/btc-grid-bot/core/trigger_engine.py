"""
BTC Grid Bot — Trigger Engine

Rules-based system that decides when to take action based on market conditions.
Input: MarketSnapshot from market_monitor.py
Output: TriggerEvent with specific actions to take.

Does NOT call APIs, does NOT trade, does NOT call AI — just decision logic.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """Market data snapshot for trigger evaluation."""
    timestamp: float                    # unix timestamp
    price: float                       # current BTC price
    volume_5m: float                   # 5-minute volume
    volume_1h_avg: float              # 1-hour average volume
    atr_5m: float                     # 5-minute ATR
    atr_1h: float                     # 1-hour ATR
    grid_center: float                # current grid center price
    grid_low: float                   # grid lower boundary
    grid_high: float                  # grid upper boundary
    last_fill_time: float             # unix timestamp of last fill
    fill_count_24h: int              # number of fills in last 24h
    market_hours: str                # "us_session", "asia_session", "off_hours"


@dataclass 
class TriggerEvent:
    """Trigger event with action to take."""
    action: str                        # "none", "roll_grid", "ai_reanalysis", "ai_redeploy"
    direction: Optional[str]           # for roll_grid: "up", "down", or None
    reason: str                        # human readable reason
    urgency: str                       # "low", "medium", "high"
    conditions_met: List[str]          # list of trigger condition names that fired
    timestamp: float                   # unix timestamp
    cooldown_minutes: int              # how long to wait before triggering again


class TriggerEngine:
    """
    Rules-based trigger engine for BTC grid bot.
    
    Evaluates market conditions and decides when to:
    - Roll grid boundaries
    - Call AI for reanalysis
    - Trigger full AI redeployment
    
    Pure function approach: no side effects, no API calls, no file I/O.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize trigger engine with configuration.
        
        Args:
            config: Dictionary containing trigger thresholds and settings
        """
        self.config = self._validate_config(config)
        self.last_triggers: Dict[str, float] = {}  # action -> timestamp
        self.recent_triggers: List[Dict] = []      # escalation tracking
        
    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and set defaults for configuration."""
        defaults = {
            "price_drift_pct": 1.5,
            "edge_proximity_pct": 5.0,
            "fill_drought_hours": 2.0,
            "volume_spike_ratio": 3.0,
            "volatility_regime_ratio": 2.0,
            "escalation_window_minutes": 15,
            "cooldown_minutes": {
                "roll_grid": 30,
                "ai_reanalysis": 60,
                "ai_redeploy": 240
            },
            "us_session_multiplier": 0.8  # Lower thresholds during US hours
        }
        
        # Merge user config with defaults
        result = defaults.copy()
        if config:
            result.update(config)
            if "cooldown_minutes" in config:
                result["cooldown_minutes"].update(config["cooldown_minutes"])
        
        return result
        
    def evaluate(self, snapshot: MarketSnapshot) -> TriggerEvent:
        """
        Evaluate market snapshot and return trigger event.
        
        Args:
            snapshot: Current market data snapshot
            
        Returns:
            TriggerEvent with action to take
        """
        try:
            # Validate snapshot
            self._validate_snapshot(snapshot)
            
            # Check all trigger conditions
            conditions = self._check_all_conditions(snapshot)
            
            # Apply cooldowns
            available_actions = self._filter_by_cooldown(conditions, snapshot.timestamp)
            
            # Apply escalation logic
            action_info = self._apply_escalation(available_actions, snapshot.timestamp)
            
            # Create trigger event
            return self._create_trigger_event(action_info, snapshot.timestamp)
            
        except Exception as e:
            logger.error(f"Trigger evaluation failed: {e}")
            return TriggerEvent(
                action="none",
                direction=None,
                reason=f"Error in trigger evaluation: {e}",
                urgency="low",
                conditions_met=[],
                timestamp=snapshot.timestamp,
                cooldown_minutes=0
            )
    
    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Validate market snapshot data."""
        required_fields = [
            'timestamp', 'price', 'volume_5m', 'volume_1h_avg',
            'atr_5m', 'atr_1h', 'grid_center', 'grid_low', 'grid_high',
            'last_fill_time'
        ]
        
        for field in required_fields:
            if not hasattr(snapshot, field):
                raise ValueError(f"Missing required field: {field}")
            
            value = getattr(snapshot, field)
            if value is None or (isinstance(value, (int, float)) and value < 0):
                raise ValueError(f"Invalid value for {field}: {value}")
    
    def _check_all_conditions(self, snapshot: MarketSnapshot) -> List[Dict]:
        """Check all trigger conditions and return list of triggered conditions."""
        conditions = []
        
        # 1. Price drift from grid center
        drift_condition = self._check_price_drift(snapshot)
        if drift_condition:
            conditions.append(drift_condition)
        
        # 2. Grid edge proximity
        edge_condition = self._check_grid_edge(snapshot)
        if edge_condition:
            conditions.append(edge_condition)
            
        # 3. Fill drought
        drought_condition = self._check_fill_drought(snapshot)
        if drought_condition:
            conditions.append(drought_condition)
            
        # 4. Volume spike
        volume_condition = self._check_volume_spike(snapshot)
        if volume_condition:
            conditions.append(volume_condition)
            
        # 5. Volatility regime shift
        volatility_condition = self._check_volatility_regime(snapshot)
        if volatility_condition:
            conditions.append(volatility_condition)
        
        return conditions
    
    def _check_price_drift(self, snapshot: MarketSnapshot) -> Optional[Dict]:
        """Check if price has drifted significantly from grid center."""
        drift_pct = abs(snapshot.price - snapshot.grid_center) / snapshot.grid_center * 100
        threshold = self._get_adjusted_threshold("price_drift_pct", snapshot.market_hours)
        
        if drift_pct > threshold:
            return {
                "name": "price_drift",
                "action": "ai_reanalysis",
                "urgency": "medium",
                "priority": 3,
                "reason": f"Price drifted {drift_pct:.1f}% from grid center (>{threshold:.1f}%)",
                "direction": None
            }
        return None
    
    def _check_grid_edge(self, snapshot: MarketSnapshot) -> Optional[Dict]:
        """Check if price is near grid boundaries."""
        price_range = snapshot.grid_high - snapshot.grid_low
        edge_threshold = price_range * (self.config["edge_proximity_pct"] / 100)
        
        distance_to_low = snapshot.price - snapshot.grid_low
        distance_to_high = snapshot.grid_high - snapshot.price
        
        if distance_to_low < edge_threshold:
            return {
                "name": "grid_edge_low",
                "action": "roll_grid", 
                "urgency": "high",
                "priority": 1,  # Highest priority
                "reason": f"Price {distance_to_low:.0f} from lower grid edge",
                "direction": "down"
            }
        elif distance_to_high < edge_threshold:
            return {
                "name": "grid_edge_high",
                "action": "roll_grid",
                "urgency": "high", 
                "priority": 1,  # Highest priority
                "reason": f"Price {distance_to_high:.0f} from upper grid edge",
                "direction": "up"
            }
        return None
    
    def _check_fill_drought(self, snapshot: MarketSnapshot) -> Optional[Dict]:
        """Check if there's been no fills for too long."""
        if snapshot.last_fill_time == 0:  # No fills ever
            return None
            
        hours_since_fill = (snapshot.timestamp - snapshot.last_fill_time) / 3600
        threshold = self.config["fill_drought_hours"]
        
        if hours_since_fill > threshold:
            return {
                "name": "fill_drought",
                "action": "ai_reanalysis",
                "urgency": "low",
                "priority": 4,  # Lowest priority
                "reason": f"No fills for {hours_since_fill:.1f}h (>{threshold}h)",
                "direction": None
            }
        return None
    
    def _check_volume_spike(self, snapshot: MarketSnapshot) -> Optional[Dict]:
        """Check for significant volume spike."""
        if snapshot.volume_1h_avg <= 0:
            return None
            
        volume_ratio = snapshot.volume_5m / snapshot.volume_1h_avg
        threshold = self._get_adjusted_threshold("volume_spike_ratio", snapshot.market_hours)
        
        if volume_ratio > threshold:
            return {
                "name": "volume_spike",
                "action": "ai_reanalysis",
                "urgency": "medium",
                "priority": 2,
                "reason": f"5m volume {volume_ratio:.1f}x average (>{threshold:.1f}x)",
                "direction": None
            }
        return None
    
    def _check_volatility_regime(self, snapshot: MarketSnapshot) -> Optional[Dict]:
        """Check for volatility regime shift."""
        if snapshot.atr_1h <= 0:
            return None
            
        atr_ratio = snapshot.atr_5m / snapshot.atr_1h
        threshold = self.config["volatility_regime_ratio"]
        
        if atr_ratio > threshold:
            return {
                "name": "volatility_regime",
                "action": "ai_reanalysis", 
                "urgency": "medium",
                "priority": 2,
                "reason": f"5m ATR {atr_ratio:.1f}x 1h ATR (>{threshold:.1f}x)",
                "direction": None
            }
        return None
    
    def _get_adjusted_threshold(self, threshold_name: str, market_hours: str) -> float:
        """Get threshold adjusted for market session."""
        base_threshold = self.config[threshold_name]
        
        # Lower thresholds during US market hours (more sensitive)
        if market_hours == "us_session":
            return base_threshold * self.config["us_session_multiplier"]
        
        return base_threshold
    
    def _filter_by_cooldown(self, conditions: List[Dict], current_time: float) -> List[Dict]:
        """Filter conditions by cooldown periods."""
        available = []
        
        for condition in conditions:
            action = condition["action"]
            cooldown_minutes = self.config["cooldown_minutes"].get(action, 0)
            
            last_trigger = self.last_triggers.get(action, 0)
            time_since_last = (current_time - last_trigger) / 60  # minutes
            
            if time_since_last >= cooldown_minutes:
                available.append(condition)
            else:
                remaining = cooldown_minutes - time_since_last
                logger.debug(f"Action {action} on cooldown for {remaining:.1f} more minutes")
        
        return available
    
    def _apply_escalation(self, conditions: List[Dict], current_time: float) -> Dict:
        """Apply escalation logic for multiple simultaneous triggers."""
        if not conditions:
            return {
                "action": "none",
                "urgency": "low", 
                "priority": 999,
                "reason": "No conditions triggered",
                "direction": None,
                "conditions_met": []
            }
        
        # Clean old triggers outside escalation window
        escalation_window_sec = self.config["escalation_window_minutes"] * 60
        cutoff_time = current_time - escalation_window_sec
        self.recent_triggers = [t for t in self.recent_triggers if t["timestamp"] > cutoff_time]
        
        # Add current triggers to recent list
        for condition in conditions:
            self.recent_triggers.append({
                "timestamp": current_time,
                "condition": condition["name"],
                "action": condition["action"]
            })
        
        # Check for escalation (2+ triggers within window)
        recent_condition_count = len([t for t in self.recent_triggers if t["timestamp"] > cutoff_time])
        
        if recent_condition_count >= 2:
            # Escalate to full AI redeployment
            return {
                "action": "ai_redeploy",
                "urgency": "high",
                "priority": 0,  # Highest priority
                "reason": f"Multiple triggers ({recent_condition_count}) within {self.config['escalation_window_minutes']} minutes",
                "direction": None,
                "conditions_met": [c["name"] for c in conditions]
            }
        
        # No escalation - return highest priority condition
        best_condition = min(conditions, key=lambda x: x["priority"])
        best_condition["conditions_met"] = [c["name"] for c in conditions]
        
        return best_condition
    
    def _create_trigger_event(self, action_info: Dict, timestamp: float) -> TriggerEvent:
        """Create TriggerEvent from action info."""
        action = action_info["action"]
        
        # Update last trigger time
        if action != "none":
            self.last_triggers[action] = timestamp
        
        # Get cooldown for this action
        cooldown = self.config["cooldown_minutes"].get(action, 0)
        
        return TriggerEvent(
            action=action,
            direction=action_info.get("direction"),
            reason=action_info["reason"],
            urgency=action_info["urgency"],
            conditions_met=action_info.get("conditions_met", []),
            timestamp=timestamp,
            cooldown_minutes=cooldown
        )


def create_trigger_engine(config: Optional[Dict] = None) -> TriggerEngine:
    """
    Factory function to create trigger engine with configuration.
    
    Args:
        config: Optional configuration dictionary
        
    Returns:
        Configured TriggerEngine instance
    """
    if config is None:
        config = {}
    
    return TriggerEngine(config)


# Example usage and testing functions
if __name__ == "__main__":
    # Example configuration
    test_config = {
        "price_drift_pct": 1.5,
        "edge_proximity_pct": 5.0,
        "fill_drought_hours": 2.0,
        "volume_spike_ratio": 3.0,
        "volatility_regime_ratio": 2.0,
        "cooldown_minutes": {
            "roll_grid": 30,
            "ai_reanalysis": 60,
            "ai_redeploy": 240
        }
    }
    
    # Create engine
    engine = create_trigger_engine(test_config)
    
    # Example snapshot
    snapshot = MarketSnapshot(
        timestamp=time.time(),
        price=45000,
        volume_5m=100,
        volume_1h_avg=80,
        atr_5m=200,
        atr_1h=150,
        grid_center=44000,  # 2.27% drift - should trigger
        grid_low=42000,
        grid_high=46000,
        last_fill_time=time.time() - 3600,  # 1 hour ago
        fill_count_24h=12,
        market_hours="us_session"
    )
    
    # Evaluate
    result = engine.evaluate(snapshot)
    print(f"Action: {result.action}")
    print(f"Reason: {result.reason}")
    print(f"Urgency: {result.urgency}")
    print(f"Conditions: {result.conditions_met}")