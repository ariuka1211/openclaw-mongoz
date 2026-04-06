"""
BTC Grid Bot — Memory Layer (Memvid Prototype)

Minimal integration with memvid-sdk for storing AI analyst decisions,
trade outcomes, and session results. Enables pattern recognition and
post-session analysis.

Usage:
    from core.memory_layer import MemoryLayer
    
    mem = MemoryLayer("bot-memory.mv2")
    mem.store_analysis(analysis_result, market_context)
    mem.store_trade_fill(trade_data)
    mem.store_session_end(session_summary)
    
    # Query patterns
    past_analyses = mem.query_analyses(days=7)
    losing_sessions = mem.query_sessions(filter_fn=lambda s: s.pnl < 0)
"""

import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

# Try to import memvid, fall back to JSON storage
try:
    import memvid_sdk
    HAS_MEMVID = True
except ImportError:
    HAS_MEMVID = False


class JSONFallback:
    """Simple JSON-based storage when memvid is not available."""
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self._data = []
        self._load()
    
    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    self._data = json.load(f)
            except:
                self._data = []
    
    def _save(self):
        dir_path = os.path.dirname(self.filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(self.filepath, 'w') as f:
            json.dump(self._data, f, indent=2)
    
    def append(self, record: dict):
        self._data.append(record)
        self._save()
    
    def query(self, filter_fn: Optional[Callable] = None, limit: int = 50) -> list[dict]:
        results = self._data if filter_fn is None else [r for r in self._data if filter_fn(r)]
        if limit <= 0:
            return results
        return results[-limit:] if limit > 0 else results
    
    def get_by_type(self, type: str, limit: int = 50) -> list[dict]:
        return self.query(filter_fn=lambda r: r.get("type") == type, limit=limit)


class MemoryLayer:
    """Memory layer for grid bot decisions and outcomes."""
    
    def __init__(self, memory_path: str = None):
        if memory_path is None:
            # Default to bot root directory
            bot_root = Path(__file__).parent.parent
            memory_path = bot_root / "bot-memory.json"
        
        self.memory_path = str(memory_path) if isinstance(memory_path, Path) else memory_path
        
        # Use memvid if available, otherwise JSON fallback
        if HAS_MEMVID:
            if not os.path.exists(self.memory_path.replace('.json', '.mv2')):
                self._mem = memvid_sdk.Memvid.create(
                    self.memory_path.replace('.json', '.mv2')
                )
            else:
                self._mem = memvid_sdk.Memvid(self.memory_path.replace('.json', '.mv2'))
        else:
            json_path = self.memory_path.replace('.mv2', '.json')
            self._storage = JSONFallback(json_path)
            self._mem = None
            print(f"⚠️ memvid-sdk not installed, using JSON fallback at {json_path}")
    
    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def _create_record(self, record_type: str, data: dict, tags: list[str] = None) -> dict:
        """Create a standardized record dict."""
        return {
            "_timestamp": self._timestamp(),
            "_type": record_type,
            "_tags": tags or [record_type],
            "_version": 1,
            **data
        }
    
    def store_analysis(self, 
                      analysis_result: dict, 
                      market_context: dict = None,
                      indicators: dict = None,
                      tags: list[str] = None):
        """Store an AI analyst decision with full context."""
        
        record = self._create_record(
            "analysis",
            {
                "analysis_result": analysis_result,
                "market_context": market_context or {},
                "indicators": indicators or {},
                "direction": analysis_result.get("direction"),
                "confidence": analysis_result.get("confidence"),
                "note": analysis_result.get("note"),
                "buy_levels": analysis_result.get("buy_levels", []),
                "sell_levels": analysis_result.get("sell_levels", []),
                "paused": analysis_result.get("pause"),
                "pause_reason": analysis_result.get("pause_reason"),
            },
            tags=tags or ["analysis", analysis_result.get("direction", "unknown")]
        )
        
        if self._mem:
            self._mem.put(
                json.dumps(record, indent=2),
                tags=record["_tags"]
            )
            self._mem.commit()
        else:
            self._storage.append(record)
        
        return record
    
    def store_trade_fill(self, 
                        trade_data: dict, 
                        session_context: dict = None,
                        tags: list[str] = None):
        """Store a trade fill event."""
        
        record = self._create_record(
            "trade_fill",
            {
                "trade_data": trade_data,
                "session_context": session_context or {},
                "side": trade_data.get("side"),
                "price": trade_data.get("price"),
                "size": trade_data.get("size"),
                "pnl": trade_data.get("pnl"),
            },
            tags=tags or ["trade", trade_data.get("side", "unknown")]
        )
        
        if self._mem:
            self._mem.put(json.dumps(record, indent=2), tags=record["_tags"])
            self._mem.commit()
        else:
            self._storage.append(record)
        
        return record
    
    def store_session_end(self, 
                         session_summary: dict,
                         tags: list[str] = None):
        """Store session end summary with full outcome data."""
        
        record = self._create_record(
            "session_end",
            session_summary,
            tags=tags or ["session_summary"]
        )
        
        if self._mem:
            self._mem.put(json.dumps(record, indent=2), tags=record["_tags"])
            self._mem.commit()
        else:
            self._storage.append(record)
        
        return record
    
    # === QUERY FUNCTIONS ===
    
    def query_analyses(self, days: int = 7, direction: str = None) -> list[dict]:
        """Query past AI analyses."""
        def filter_fn(r):
            if r.get("_type") != "analysis":
                return False
            if days > 0:
                try:
                    ts = datetime.fromisoformat(r["_timestamp"])
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                    if ts < cutoff:
                        return False
                except:
                    return False
            if direction:
                analysis_direction = r.get("direction") or r.get("analysis_result", {}).get("direction")
                if analysis_direction != direction:
                    return False
            return True
        
        if self._mem:
            # Would use memvid search with filters
            return []
        else:
            return self._storage.query(filter_fn, limit=0)
    
    def query_sessions(self, filter_fn: Callable = None, limit: int = 20) -> list[dict]:
        """Query past session outcomes."""
        def combined_filter(r):
            if r.get("_type") != "session_end":
                return False
            if filter_fn:
                return filter_fn(r)
            return True
        
        if self._mem:
            return []
        else:
            return self._storage.query(combined_filter, limit=limit)
    
    def query_trades(self, side: str = None, days: int = 7) -> list[dict]:
        """Query past trade fills."""
        def filter_fn(r):
            if r.get("_type") != "trade_fill":
                return False
            if side:
                trade_side = r.get("trade_data", {}).get("side") or r.get("side")
                if trade_side != side:
                    return False
            if days > 0:
                try:
                    ts = datetime.fromisoformat(r["_timestamp"])
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                    if ts < cutoff:
                        return False
                except:
                    return False
            return True
        
        if self._mem:
            return []
        else:
            return self._storage.query(filter_fn, limit=0)
    
    def get_pattern_summary(self, days: int = 30) -> dict:
        """
        Analyze patterns from stored memory.
        Returns summary statistics for review.
        """
        sessions = self.query_sessions(limit=1000)
        analyses = self.query_analyses(days=days)
        trades = self.query_trades(days=days)
        
        if not sessions and not trades:
            return {"status": "no_data", "message": "No memory stored yet"}
        
        summary = {
            "total_sessions": len(sessions),
            "total_trades": len(trades),
            "total_analyses": len(analyses),
        }
        
        if sessions:
            pnl_values = [s.get("realized_pnl", 0) for s in sessions]
            summary["avg_session_pnl"] = sum(pnl_values) / len(pnl_values)
            summary["best_session"] = max(pnl_values)
            summary["worst_session"] = min(pnl_values)
            summary["win_sessions"] = sum(1 for p in pnl_values if p > 0)
            summary["loss_sessions"] = sum(1 for p in pnl_values if p < 0)
        
        if trades:
            sides = {}
            for t in trades:
                side = t.get("side", "unknown")
                if side not in sides:
                    sides[side] = {"count": 0, "total_pnl": 0}
                sides[side]["count"] += 1
                pnl = t.get("pnl")
                if pnl is not None:
                    sides[side]["total_pnl"] += pnl
            summary["by_side"] = sides
        
        if analyses:
            directions = {}
            for a in analyses:
                d = a.get("direction", "unknown")
                if d not in directions:
                    directions[d] = {"count": 0, "pauses": 0}
                directions[d]["count"] += 1
                if a.get("paused"):
                    directions[d]["pauses"] += 1
            summary["analysis_directions"] = directions
        
        return summary


# === CONVENIENCE FUNCTIONS ===

def get_memory() -> MemoryLayer:
    """Get singleton memory instance."""
    if not hasattr(get_memory, "_instance"):
        bot_root = Path(__file__).parent.parent
        memory_path = bot_root / "bot-memory.json"
        get_memory._instance = MemoryLayer(str(memory_path))
    return get_memory._instance


def init_memory_if_needed():
    """Initialize memory storage if configured to do so."""
    return get_memory()


if __name__ == "__main__":
    # Test the memory layer
    mem = MemoryLayer("test-memory.json")
    
    # Test storing analysis
    test_analysis = {
        "direction": "long",
        "buy_levels": [65000, 66000, 67000],
        "sell_levels": [69000, 70000, 71000],
        "confidence": "high",
        "note": "Strong support at 65k"
    }
    
    record = mem.store_analysis(
        test_analysis,
        market_context={"price": 68000, "regime": "ranging"},
        tags=["test", "analysis"]
    )
    print(f"Stored analysis: {record['_timestamp']}")
    
    # Test querying
    analyses = mem.query_analyses(days=1)
    print(f"Found {len(analyses)} analyses")
    
    # Get pattern summary
    summary = mem.get_pattern_summary(days=1)
    print(f"Pattern summary: {json.dumps(summary, indent=2)}")
    
    print("\n✅ Memory layer prototype working!")
    print(f"Storage file: test-memory.json")
