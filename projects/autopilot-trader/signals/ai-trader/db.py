"""
SQLite helpers for AI trader decision journal.
WAL mode, thread-safe, structured logging.
"""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ai-trader.db")


class DecisionDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_tables()

    @property
    def conn(self):
        """Direct access to the connection (for simple read queries)."""
        return self._conn

    def _init_tables(self):
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT,
                direction TEXT,
                reasoning TEXT,
                confidence REAL,
                safety_approved INTEGER NOT NULL DEFAULT 0,
                safety_reasons TEXT,
                executed INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER,
                positions_snapshot TEXT,
                signals_snapshot TEXT
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cycle_id TEXT,
                symbol TEXT NOT NULL,
                direction TEXT,
                entry_price REAL,
                exit_price REAL,
                size_usd REAL,
                pnl_usd REAL,
                pnl_pct REAL,
                roe_pct REAL,
                hold_time_seconds INTEGER,
                max_drawdown_pct REAL,
                exit_reason TEXT,
                decision_snapshot TEXT
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp);
            CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_outcomes_ts ON outcomes(timestamp);
            CREATE INDEX IF NOT EXISTS idx_outcomes_symbol ON outcomes(symbol);
            CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp);
        """)
            # Migration: add roe_pct column if missing
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(outcomes)").fetchall()}
            if "roe_pct" not in columns:
                self._conn.execute("ALTER TABLE outcomes ADD COLUMN roe_pct REAL")
            self._conn.commit()

    def log_decision(
        self,
        cycle_id: str,
        decision: dict,
        safety_approved: bool,
        safety_reasons: list[str],
        executed: bool,
        positions_snapshot: list[dict] | None = None,
        signals_snapshot: list[dict] | None = None,
        latency_ms: int = 0,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO decisions
                   (timestamp, cycle_id, action, symbol, direction, reasoning, confidence,
                    safety_approved, safety_reasons, executed, latency_ms,
                    positions_snapshot, signals_snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    cycle_id,
                    decision.get("action", "unknown"),
                    decision.get("symbol"),
                    decision.get("direction"),
                    decision.get("reasoning", ""),
                    decision.get("confidence", 0),
                    int(safety_approved),
                    json.dumps(safety_reasons),
                    int(executed),
                    latency_ms,
                    json.dumps(positions_snapshot or []),
                    json.dumps(signals_snapshot or []),
                ),
            )
            self._conn.commit()

    def log_outcome(self, outcome: dict):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO outcomes
                   (timestamp, cycle_id, symbol, direction, entry_price, exit_price,
                    size_usd, pnl_usd, pnl_pct, roe_pct, hold_time_seconds, max_drawdown_pct,
                    exit_reason, decision_snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    outcome.get("cycle_id"),
                    outcome["symbol"],
                    outcome.get("direction"),
                    outcome.get("entry_price"),
                    outcome.get("exit_price"),
                    outcome.get("size_usd"),
                    outcome.get("pnl_usd"),
                    outcome.get("pnl_pct"),
                    outcome.get("roe_pct"),
                    outcome.get("hold_time_seconds", 0),
                    outcome.get("max_drawdown_pct", 0),
                    outcome.get("exit_reason", "unknown"),
                    json.dumps(outcome.get("decision_snapshot", {})),
                ),
            )
            self._conn.commit()

    def log_alert(self, level: str, message: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO alerts (timestamp, level, message) VALUES (?, ?, ?)",
                (now, level, message),
            )
            self._conn.commit()

    def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT timestamp, cycle_id, action, symbol, direction, reasoning,
                          confidence, safety_approved, safety_reasons, executed, latency_ms
                   FROM decisions ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "timestamp": r[0],
                "cycle_id": r[1],
                "action": r[2],
                "symbol": r[3],
                "direction": r[4],
                "reasoning": r[5],
                "confidence": r[6],
                "safety_approved": bool(r[7]),
                "safety_reasons": json.loads(r[8]) if r[8] else [],
                "executed": bool(r[9]),
                "latency_ms": r[10],
            }
            for r in rows
        ]

    def get_recent_outcomes(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT timestamp, symbol, direction, entry_price, exit_price,
                          size_usd, pnl_usd, pnl_pct, roe_pct, hold_time_seconds, exit_reason
                   FROM outcomes ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "timestamp": r[0],
                "symbol": r[1],
                "direction": r[2],
                "entry_price": r[3],
                "exit_price": r[4],
                "size_usd": r[5],
                "pnl_usd": r[6],
                "pnl_pct": r[7],
                "roe_pct": r[8],
                "hold_time_seconds": r[9],
                "exit_reason": r[10],
            }
            for r in rows
        ]

    def count_recent_rejections(self, minutes: int = 30) -> int:
        cutoff = datetime.now(timezone.utc).replace(
            minute=datetime.now(timezone.utc).minute - minutes
            if datetime.now(timezone.utc).minute >= minutes
            else 0
        ).isoformat()
        # More reliable: use time comparison
        cutoff_time = time.time() - (minutes * 60)
        cutoff_iso = datetime.fromtimestamp(cutoff_time, tz=timezone.utc).isoformat()
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) FROM decisions
                   WHERE timestamp > ? AND safety_approved = 0 AND action != 'hold'""",
                (cutoff_iso,),
            ).fetchone()
        return row[0] if row else 0

    def get_daily_pnl(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_of_day = f"{today}T00:00:00+00:00"
        end_of_day = f"{today}T23:59:59.999999+00:00"
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM outcomes WHERE timestamp >= ? AND timestamp <= ?",
                (start_of_day, end_of_day),
            ).fetchone()
        return row[0] if row else 0.0

    def get_performance_stats(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            # Win rate
            total = self._conn.execute(
                "SELECT COUNT(*) FROM outcomes"
            ).fetchone()[0]
            wins = self._conn.execute(
                "SELECT COUNT(*) FROM outcomes WHERE pnl_usd > 0"
            ).fetchone()[0]
            # Avg win/loss
            avg_win = self._conn.execute(
                "SELECT COALESCE(AVG(pnl_usd), 0) FROM outcomes WHERE pnl_usd > 0"
            ).fetchone()[0]
            avg_loss = self._conn.execute(
                "SELECT COALESCE(AVG(pnl_usd), 0) FROM outcomes WHERE pnl_usd <= 0"
            ).fetchone()[0]
            # Total PnL
            total_pnl = self._conn.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM outcomes"
            ).fetchone()[0]
            # Max drawdown (simplified: worst single trade)
            max_dd = self._conn.execute(
                "SELECT COALESCE(MIN(pnl_usd), 0) FROM outcomes"
            ).fetchone()[0]
            # Trades today
            trades_today = self._conn.execute(
                "SELECT COUNT(*) FROM outcomes WHERE timestamp LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]
            # Today PnL (inlined to avoid reentrant lock)
            today_pnl = self._conn.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM outcomes WHERE timestamp LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]

        return {
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "total_trades": total,
            "wins": wins,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "total_pnl": total_pnl,
            "daily_pnl": today_pnl,
            "max_drawdown": max_dd,
            "trades_today": trades_today,
        }

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, timestamp, level, message, acknowledged FROM alerts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "level": r[2], "message": r[3], "acknowledged": bool(r[4])}
            for r in rows
        ]

    def purge_old_data(self, keep_days: int = 7):
        """Remove decisions and alerts older than keep_days. Outcomes are kept (they're small and useful)."""
        cutoff = datetime.now(timezone.utc).isoformat()[:10]  # today
        with self._lock:
            # Delete old decisions
            cur = self._conn.execute(
                "DELETE FROM decisions WHERE timestamp < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            decisions_deleted = cur.rowcount
            # Delete acknowledged alerts older than keep_days
            cur = self._conn.execute(
                "DELETE FROM alerts WHERE acknowledged = 1 AND timestamp < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            alerts_deleted = cur.rowcount
            # Delete unacknowledged alerts older than 30 days (stale)
            cur = self._conn.execute(
                "DELETE FROM alerts WHERE timestamp < datetime('now', '-30 days')",
            )
            stale_alerts = cur.rowcount
            self._conn.commit()
            # Vacuum to reclaim space
            self._conn.execute("VACUUM")
            total = decisions_deleted + alerts_deleted + stale_alerts
            if total > 0:
                log.info(f"Purged: {decisions_deleted} decisions, {alerts_deleted + stale_alerts} alerts")

    def close(self):
        with self._lock:
            self._conn.close()
