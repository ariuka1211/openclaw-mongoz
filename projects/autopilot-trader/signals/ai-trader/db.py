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
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
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
            # Migration: add token columns if missing
            decision_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(decisions)").fetchall()}
            if "tokens_in" not in decision_columns:
                self._conn.execute("ALTER TABLE decisions ADD COLUMN tokens_in INTEGER DEFAULT 0")
            if "tokens_out" not in decision_columns:
                self._conn.execute("ALTER TABLE decisions ADD COLUMN tokens_out INTEGER DEFAULT 0")
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
        tokens_in: int = 0,
        tokens_out: int = 0,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO decisions
                   (timestamp, cycle_id, action, symbol, direction, reasoning, confidence,
                    safety_approved, safety_reasons, executed, latency_ms,
                    tokens_in, tokens_out,
                    positions_snapshot, signals_snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    tokens_in,
                    tokens_out,
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
            try:
                self._conn.commit()
            except sqlite3.Error as e:
                log.error(f"Failed to commit outcome for {outcome.get('symbol')}: {e}")
                raise

    def update_latest_outcome(self, symbol: str, exit_price: float, pnl_usd: float,
                              pnl_pct: float, roe_pct: float, exit_reason: str) -> bool:
        """Update the most recent outcome for a symbol with actual fill price.

        Called after verification confirms the fill price. If the bot crashes
        before this, the estimated outcome still exists with the best available price.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """UPDATE outcomes SET
                    exit_price = ?,
                    pnl_usd = ?,
                    pnl_pct = ?,
                    roe_pct = ?,
                    exit_reason = ?,
                    timestamp = ?
                WHERE id = (
                    SELECT id FROM outcomes WHERE symbol = ? ORDER BY id DESC LIMIT 1
                )""",
                (exit_price, pnl_usd, pnl_pct, roe_pct, exit_reason, now, symbol),
            )
            self._conn.commit()
            return cur.rowcount > 0

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
        """Get today's realized PnL from closed outcomes.

        Uses UTC midnight boundaries — 'today' is the current UTC calendar day.
        All outcome timestamps are stored in UTC ISO format.
        """
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
        start_of_day = f"{today}T00:00:00+00:00"
        end_of_day = f"{today}T23:59:59.999999+00:00"
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
                "SELECT COUNT(*) FROM outcomes WHERE timestamp >= ? AND timestamp <= ?",
                (start_of_day, end_of_day),
            ).fetchone()[0]
            # Today PnL (inlined to avoid reentrant lock)
            today_pnl = self._conn.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM outcomes WHERE timestamp >= ? AND timestamp <= ?",
                (start_of_day, end_of_day),
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
        """Remove decisions, alerts, and outcomes older than keep_days."""
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
            # Delete outcomes older than 30 days
            cur = self._conn.execute(
                "DELETE FROM outcomes WHERE timestamp < datetime('now', '-30 days')",
            )
            outcomes_deleted = cur.rowcount
            self._conn.commit()
            total = decisions_deleted + alerts_deleted + stale_alerts + outcomes_deleted
            if total > 0:
                log.info(f"Purged: {decisions_deleted} decisions, {alerts_deleted + stale_alerts} alerts, {outcomes_deleted} outcomes")
        # WAL checkpoint + vacuum outside lock to avoid blocking
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.execute("VACUUM")

    def get_recently_traded_symbols(self, hours: int = 2) -> dict[str, str]:
        """Get symbols and their most recent trade direction within the time window.

        Returns a dict mapping symbol -> direction for the latest outcome per symbol
        within the given number of hours.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT o.symbol, o.direction "
                "FROM outcomes o "
                "INNER JOIN ("
                "  SELECT symbol, MAX(timestamp) as max_ts "
                "  FROM outcomes "
                "  WHERE timestamp > datetime('now', ?) "
                "  GROUP BY symbol"
                ") latest ON o.symbol = latest.symbol AND o.timestamp = latest.max_ts",
                (f"-{hours} hours",),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_confidence_bracket_stats(self, hours: int = 72) -> dict:
        """Get statistical breakdowns for reflection analysis.

        Returns dict with keys: direction_stats, hold_time_stats,
        confidence_stats, loss_patterns, overall_stats.
        """
        with self._lock:
            direction_rows = self._conn.execute(
                "SELECT direction, COUNT(*) as total, "
                "SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, "
                "COALESCE(AVG(pnl_usd), 0) as avg_pnl, "
                "COALESCE(SUM(pnl_usd), 0) as total_pnl "
                "FROM outcomes GROUP BY direction"
            ).fetchall()

            hold_time_rows = self._conn.execute(
                "SELECT "
                "CASE WHEN hold_time_seconds < 1800 THEN '<30min' "
                "WHEN hold_time_seconds < 7200 THEN '30min-2h' "
                "ELSE '2h+' END as bracket, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, "
                "COALESCE(AVG(pnl_usd), 0) as avg_pnl "
                "FROM outcomes GROUP BY bracket ORDER BY MIN(hold_time_seconds)"
            ).fetchall()

            confidence_rows = self._conn.execute(
                "SELECT "
                "CASE WHEN d.confidence >= 0.7 THEN 'high' "
                "WHEN d.confidence >= 0.4 THEN 'medium' "
                "ELSE 'low' END as conf_bracket, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN o.pnl_usd > 0 THEN 1 ELSE 0 END) as wins, "
                "COALESCE(AVG(o.pnl_usd), 0) as avg_pnl "
                "FROM outcomes o "
                "LEFT JOIN decisions d ON d.id = ("
                "  SELECT d2.id FROM decisions d2 "
                "  WHERE d2.symbol = o.symbol "
                "  AND d2.timestamp <= o.timestamp "
                "  AND d2.timestamp > datetime(o.timestamp, '-4 hours') "
                "  AND d2.action = 'open' "
                "  AND d2.executed = 1 "
                "  ORDER BY d2.timestamp DESC LIMIT 1"
                ") WHERE d.confidence IS NOT NULL GROUP BY conf_bracket"
            ).fetchall()

            loss_rows = self._conn.execute(
                "SELECT exit_reason, COUNT(*) as total_losses, "
                "COALESCE(SUM(pnl_usd), 0) as total_loss_usd, "
                "COALESCE(AVG(pnl_usd), 0) as avg_loss "
                "FROM outcomes WHERE pnl_usd <= 0 "
                "GROUP BY exit_reason ORDER BY total_loss_usd ASC LIMIT 5"
            ).fetchall()

            total = self._conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            wins = self._conn.execute("SELECT COUNT(*) FROM outcomes WHERE pnl_usd > 0").fetchone()[0]
            avg_win = self._conn.execute("SELECT COALESCE(AVG(pnl_usd), 0) FROM outcomes WHERE pnl_usd > 0").fetchone()[0]
            avg_loss = self._conn.execute("SELECT COALESCE(AVG(pnl_usd), 0) FROM outcomes WHERE pnl_usd <= 0").fetchone()[0]
            total_pnl = self._conn.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM outcomes").fetchone()[0]
            max_dd = self._conn.execute("SELECT COALESCE(MIN(pnl_usd), 0) FROM outcomes").fetchone()[0]

        return {
            "direction_stats": [
                {"direction": r[0], "total": r[1], "wins": r[2], "avg_pnl": r[3], "total_pnl": r[4]}
                for r in direction_rows
            ],
            "hold_time_stats": [
                {"bracket": r[0], "total": r[1], "wins": r[2], "avg_pnl": r[3]}
                for r in hold_time_rows
            ],
            "confidence_stats": [
                {"bracket": r[0], "total": r[1], "wins": r[2], "avg_pnl": r[3]}
                for r in confidence_rows
            ],
            "loss_patterns": [
                {"exit_reason": r[0], "count": r[1], "total_loss": r[2], "avg_loss": r[3]}
                for r in loss_rows
            ],
            "overall_stats": {
                "win_rate": (wins / total * 100) if total > 0 else 0,
                "wins": wins, "total_trades": total,
                "avg_win": avg_win, "avg_loss": avg_loss,
                "total_pnl": total_pnl, "max_drawdown": max_dd,
            },
        }

    def close(self):
        with self._lock:
            self._conn.close()
