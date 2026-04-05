"""
BTC Grid Bot — Intelligence Engine

Analyzes stored patterns and provides actionable recommendations.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional
from core.memory_layer import get_memory


class PatternAnalyzer:
    """Analyzes trading patterns and generates recommendations."""
    
    def __init__(self):
        self.memory = get_memory()
    
    def get_recommendations(self, days: int = 14) -> Dict:
        """Generate actionable recommendations based on recent patterns."""
        try:
            sessions = self.memory.query_sessions(limit=50)
            analyses = self.memory.query_analyses(days=days)
            fills = self.memory.query_trades(days=days)
            
            if len(sessions) < 3:
                return {
                    "status": "insufficient_data",
                    "message": f"Need 3+ sessions for recommendations (have {len(sessions)})",
                    "recommendations": []
                }
            
            recs = []
            
            # 1. Fill timing analysis
            timing_rec = self._analyze_fill_timing(fills)
            if timing_rec:
                recs.append(timing_rec)
            
            # 2. Direction bias analysis
            direction_rec = self._analyze_direction_bias(sessions, analyses)
            if direction_rec:
                recs.append(direction_rec)
            
            # 3. Roll frequency analysis
            roll_rec = self._analyze_roll_patterns(sessions)
            if roll_rec:
                recs.append(roll_rec)
            
            # 4. Market regime performance
            regime_rec = self._analyze_regime_performance(sessions, analyses)
            if regime_rec:
                recs.append(regime_rec)
            
            # 5. Risk management
            risk_rec = self._analyze_risk_patterns(sessions)
            if risk_rec:
                recs.append(risk_rec)
            
            return {
                "status": "success",
                "analysis_period_days": days,
                "sessions_analyzed": len(sessions),
                "recommendations": recs,
                "summary": self._generate_summary(sessions)
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"Analysis failed: {e}",
                "recommendations": []
            }
    
    def _analyze_direction_bias(self, sessions: List[Dict], analyses: List[Dict]) -> Optional[Dict]:
        """Analyze long vs short grid performance."""
        if len(sessions) < 5:
            return None
        
        long_pnl = []
        short_pnl = []
        
        # Match sessions with their analysis direction
        for i, session in enumerate(sessions[:len(analyses)]):
            if i < len(analyses):
                direction = analyses[i].get('direction') or analyses[i].get('analysis_result', {}).get('direction')
                pnl = session.get('realized_pnl', 0)
                
                if direction == 'long':
                    long_pnl.append(pnl)
                elif direction == 'short':
                    short_pnl.append(pnl)
        
        if len(long_pnl) >= 3 and len(short_pnl) >= 2:
            long_avg = sum(long_pnl) / len(long_pnl)
            short_avg = sum(short_pnl) / len(short_pnl)
            long_wins = sum(1 for p in long_pnl if p > 0) / len(long_pnl)
            short_wins = sum(1 for p in short_pnl if p > 0) / len(short_pnl)
            
            if abs(short_avg - long_avg) > 0.15:
                better_dir = "short" if short_avg > long_avg else "long"
                worse_dir = "long" if better_dir == "short" else "short"
                better_avg = short_avg if better_dir == "short" else long_avg
                worse_avg = long_avg if better_dir == "short" else short_avg
                
                return {
                    "type": "direction_bias",
                    "priority": "high",
                    "title": f"Consider bias toward {better_dir} grids",
                    "finding": f"{better_dir.capitalize()} grids: avg ${better_avg:.2f}, {worse_dir} grids: avg ${worse_avg:.2f}",
                    "recommendation": f"Increase confidence weighting for {better_dir} grid signals from AI",
                    "data": {
                        "long_sessions": len(long_pnl),
                        "short_sessions": len(short_pnl),
                        "long_avg_pnl": round(long_avg, 3),
                        "short_avg_pnl": round(short_avg, 3)
                    }
                }
        
        return None
    
    def _analyze_fill_timing(self, fills: List[Dict]) -> Optional[Dict]:
        """Analyze fill timing patterns."""
        completed_trades = [f for f in fills if f.get('trade_data', {}).get('type') == 'completed_trade']
        
        if len(completed_trades) < 6:
            return None
        
        quick_fills = []
        slow_fills = []
        
        for trade in completed_trades:
            duration = trade.get('trade_data', {}).get('trade_duration_min', 0)
            pnl = trade.get('trade_data', {}).get('pnl', 0)
            
            if duration <= 10:
                quick_fills.append(pnl)
            elif duration > 30:
                slow_fills.append(pnl)
        
        if len(quick_fills) >= 3 and len(slow_fills) >= 3:
            quick_avg = sum(quick_fills) / len(quick_fills)
            slow_avg = sum(slow_fills) / len(slow_fills)
            
            if quick_avg > slow_avg + 0.05:
                return {
                    "type": "timing",
                    "priority": "medium", 
                    "title": "Optimize deployment timing",
                    "finding": f"Quick fills avg ${quick_avg:.2f}, slow fills avg ${slow_avg:.2f}",
                    "recommendation": "Deploy grids during higher volatility periods when fills happen quickly",
                    "data": {
                        "quick_fills": len(quick_fills),
                        "slow_fills": len(slow_fills)
                    }
                }
        
        return None
    
    def _analyze_roll_patterns(self, sessions: List[Dict]) -> Optional[Dict]:
        """Analyze grid roll impact."""
        roll_sessions = [s for s in sessions if s.get('roll_count', 0) > 0]
        no_roll_sessions = [s for s in sessions if s.get('roll_count', 0) == 0]
        
        if len(roll_sessions) < 2 or len(no_roll_sessions) < 2:
            return None
        
        roll_avg = sum(s.get('realized_pnl', 0) for s in roll_sessions) / len(roll_sessions)
        no_roll_avg = sum(s.get('realized_pnl', 0) for s in no_roll_sessions) / len(no_roll_sessions)
        
        if roll_avg < no_roll_avg - 0.10:
            return {
                "type": "roll_frequency",
                "priority": "medium",
                "title": "Reduce grid roll frequency", 
                "finding": f"Roll sessions avg ${roll_avg:.2f}, no-roll sessions avg ${no_roll_avg:.2f}",
                "recommendation": "Consider reducing max_rolls_per_session to 1",
                "data": {
                    "roll_sessions": len(roll_sessions),
                    "no_roll_sessions": len(no_roll_sessions)
                }
            }
        
        return None
    
    def _analyze_regime_performance(self, sessions: List[Dict], analyses: List[Dict]) -> Optional[Dict]:
        """Analyze performance by market regime."""
        regime_results = {}
        
        for i, session in enumerate(sessions[:len(analyses)]):
            if i < len(analyses):
                regime = analyses[i].get('market_context', {}).get('regime', 'unknown')
                pnl = session.get('realized_pnl', 0)
                
                if regime not in regime_results:
                    regime_results[regime] = []
                regime_results[regime].append(pnl)
        
        significant_regimes = {k: v for k, v in regime_results.items() if len(v) >= 2}
        
        if len(significant_regimes) >= 2:
            regime_avgs = {k: sum(v) / len(v) for k, v in significant_regimes.items()}
            best_regime = max(regime_avgs, key=regime_avgs.get)
            worst_regime = min(regime_avgs, key=regime_avgs.get)
            
            if regime_avgs[best_regime] > regime_avgs[worst_regime] + 0.15:
                return {
                    "type": "market_regime",
                    "priority": "medium",
                    "title": f"Optimize for {best_regime} markets",
                    "finding": f"{best_regime} markets: avg ${regime_avgs[best_regime]:.2f}, {worst_regime} markets: avg ${regime_avgs[worst_regime]:.2f}",
                    "recommendation": f"Consider avoiding trades during {worst_regime} conditions",
                    "data": {
                        "regime_performance": {k: round(v, 3) for k, v in regime_avgs.items()}
                    }
                }
        
        return None
    
    def _analyze_risk_patterns(self, sessions: List[Dict]) -> Optional[Dict]:
        """Analyze risk and drawdown patterns."""
        if len(sessions) < 5:
            return None
        
        recent_pnls = [s.get('realized_pnl', 0) for s in sessions[-5:]]
        losing_streak = 0
        
        for pnl in reversed(recent_pnls):
            if pnl < 0:
                losing_streak += 1
            else:
                break
        
        if losing_streak >= 3:
            return {
                "type": "risk_management",
                "priority": "high",
                "title": "Consider position size reduction",
                "finding": f"Losing streak: {losing_streak} sessions",
                "recommendation": "Reduce size_per_level by 25-50% until performance improves",
                "data": {
                    "losing_streak": losing_streak
                }
            }
        
        return None
    
    def _generate_summary(self, sessions: List[Dict]) -> Dict:
        """Generate overall performance summary."""
        if not sessions:
            return {"total_sessions": 0}
        
        pnls = [s.get('realized_pnl', 0) for s in sessions]
        
        return {
            "total_sessions": len(sessions),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl_per_session": round(sum(pnls) / len(pnls), 3),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
            "best_session": round(max(pnls), 2),
            "worst_session": round(min(pnls), 2)
        }


def get_intelligence_report(days: int = 14) -> Dict:
    """Get comprehensive intelligence report with recommendations."""
    analyzer = PatternAnalyzer()
    return analyzer.get_recommendations(days)


if __name__ == "__main__":
    report = get_intelligence_report(days=7)
    print(json.dumps(report, indent=2))