"""Data reader — extracts signals and positions from JSON files and DB fallbacks."""

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("ai-trader.context.data_reader")

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json


class DataReader:
    """Reads signals and positions from JSON files and DB fallbacks."""

    def __init__(self, ai_trader):
        self.ai_trader = ai_trader
        config = ai_trader.config
        _config_dir = os.path.dirname(os.path.abspath(config["_config_path"]))
        self.signals_file = Path(_config_dir) / config["signals_file"]
        self.result_file = Path(_config_dir) / config.get("result_file", "../ai-result.json")

    def read_signals(self) -> tuple[list[dict], dict]:
        """Read signals.json. Returns (top_opportunities_list, config_dict).

        Filters to top N signals by compositeScore to keep prompt size manageable.
        """
        if not self.signals_file.exists():
            log.warning(f"Signals file not found: {self.signals_file}")
            return [], {}

        try:
            data = safe_read_json(self.signals_file)
            if data is None:
                log.error(f"Failed to read signals: {self.signals_file}")
                return [], {}

            opportunities = data.get("opportunities", [])
            if not opportunities:
                return [], data.get("config", {})

            max_signals = self.ai_trader.config.get("max_signals", 15)

            # Filter to top N by compositeScore descending
            top_opportunities = sorted(
                opportunities,
                key=lambda x: x.get("compositeScore", 0),
                reverse=True,
            )[:max_signals]

            # Remove symbols that were recently traded (last 2 hours)
            top_opportunities = self._filter_traded_symbols(top_opportunities)

            log.info(
                f"Filtered {len(opportunities)} signals to top {len(top_opportunities)} "
                f"(max_signals={max_signals})"
            )
            return top_opportunities, data.get("config", {})

        except (json.JSONDecodeError, OSError) as e:
            log.error(f"Failed to read signals: {e}")
            return [], {}

    def _filter_traded_symbols(self, opportunities: list[dict]) -> list[dict]:
        """Remove opportunities for symbols that were recently traded.

        Allows re-entry in the opposite direction — only blocks same-direction trades
        within the cooldown window.
        """
        try:
            recent_outcomes = self.ai_trader.db.get_recently_traded_symbols(hours=2)

            if not recent_outcomes:
                return opportunities

            filtered = []
            for o in opportunities:
                symbol = o.get('symbol')
                if symbol not in recent_outcomes:
                    filtered.append(o)
                    continue
                # Same direction — block (cooldown). Opposite direction — allow re-entry
                outcome_dir = recent_outcomes[symbol]
                opp_dir = o.get('direction')
                if opp_dir and outcome_dir and opp_dir != outcome_dir:
                    filtered.append(o)  # Opposite direction, allow re-entry

            removed = len(opportunities) - len(filtered)
            if removed > 0:
                log.info(f"Filtered out {removed} recently traded symbols (same direction cooldown)")

            return filtered
        except Exception as e:
            log.warning(f"Failed to filter traded symbols: {e}")
            return opportunities

    def read_positions(self) -> list[dict]:
        """Read current positions from bot's result file, falling back to DB if missing."""
        if self.result_file.exists():
            data = safe_read_json(self.result_file)
            if data and data.get("processed_decision_id"):
                # Bot has processed a decision — positions are fresh
                # MED-26: Canonical position size field is 'position_size_usd' (written by bot's _write_ai_result).
                # Both 'position_size_usd' and 'size_usd' are set by the bot for compatibility.
                return data.get("positions", [])

        # HIGH-9: Result file missing/empty — reconstruct from DB.
        # Find the most recent outcome per symbol. Open positions are those
        # whose last entry has no exit (exit_price is NULL) or was an "open" event.
        try:
            cursor = self.ai_trader.db.conn.cursor()
            cursor.execute('''
                SELECT o.symbol, o.direction, o.entry_price, o.size_usd, o.timestamp
                FROM outcomes o
                INNER JOIN (
                    SELECT symbol, MAX(id) as max_id
                    FROM outcomes
                    GROUP BY symbol
                ) latest ON o.id = latest.max_id
                WHERE o.exit_price IS NULL
                   OR o.exit_reason IN ('opened', 'new_position')
                ORDER BY o.timestamp DESC
            ''')
            rows = cursor.fetchall()
            if rows:
                positions = []
                for row in rows:
                    positions.append({
                        "symbol": row[0],
                        "side": row[1] if row[1] else "long",
                        "entry_price": row[2] or 0,
                        "size_usd": row[3] or 0,
                        "size": row[3] or 0,
                        "position_size_usd": row[3] or 0,
                        "current_price": row[2] or 0,  # No live price from DB
                        "source": "db_fallback",
                    })
                log.info(f"HIGH-9: Reconstructed {len(positions)} open positions from DB (result file unavailable)")
                return positions
        except Exception as e:
            log.warning(f"Failed to read positions from DB fallback: {e}")

        return []

    def read_equity(self) -> float:
        """Read current equity from bot's shared state file.

        Falls back to 0 if file doesn't exist or is stale.
        """
        equity_path = Path(self.ai_trader.config.get("ai_trader_dir", ".")) / "state" / "equity.json"
        try:
            if equity_path.exists():
                data = safe_read_json(equity_path)
                if data and "equity" in data:
                    equity = float(data["equity"])
                    if equity > 0:
                        return equity
        except Exception as e:
            log.warning(f"Failed to read equity file: {e}")
        return 0
