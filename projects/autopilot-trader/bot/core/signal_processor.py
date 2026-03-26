"""
Compatibility wrapper — delegates to extracted modules.

This module maintains backward compatibility for code that accesses
self.bot.signal_processor.<method>. All actual logic lives in:
  - core.signal_handler   (process_signals)
  - core.decision_handler (process_ai_decision, validate_ai_decision)
  - core.executor         (execute_ai_open/close/close_all)
  - core.verifier         (verify_position_opened/closed, check_active_orders)
  - core.result_writer    (write_ai_result, refresh_position_context)
  - core.shared_utils     (pacing, quota, market_id, equity, fill_price, log_outcome)
"""

from config import BotConfig


class SignalProcessor:
    """Thin wrapper — all methods delegate to extracted module functions."""

    def __init__(self, cfg: BotConfig, api, tracker, alerter, bot):
        self.cfg = cfg
        self.api = api
        self.tracker = tracker
        self.alerter = alerter
        self.bot = bot

    # ── Scanner signal flow ────────────────────────────────────────

    async def _process_signals(self):
        from core.signal_handler import process_signals
        await process_signals(self.bot, self.cfg, self.api, self.tracker, self.alerter)

    # ── AI decision flow ───────────────────────────────────────────

    async def _process_ai_decision(self):
        from core.decision_handler import process_ai_decision
        await process_ai_decision(self.bot, self.cfg, self.api, self.tracker, self.alerter)

    def _validate_ai_decision(self, decision: dict) -> str | None:
        from core.decision_handler import validate_ai_decision
        return validate_ai_decision(decision)

    # ── Execution ──────────────────────────────────────────────────

    async def _execute_ai_open(self, decision: dict) -> bool:
        from core.executor import execute_ai_open
        return await execute_ai_open(self.bot, self.cfg, self.api, self.tracker, self.alerter, decision)

    async def _execute_ai_close(self, decision: dict) -> bool:
        from core.executor import execute_ai_close
        return await execute_ai_close(self.bot, self.cfg, self.api, self.tracker, self.alerter, decision)

    async def _execute_ai_close_all(self, decision: dict) -> bool:
        from core.executor import execute_ai_close_all
        return await execute_ai_close_all(self.bot, self.cfg, self.api, self.tracker, self.alerter, decision)

    # ── Verification ───────────────────────────────────────────────

    async def _verify_position_opened(self, market_id: int, expected_size: float, symbol: str) -> dict | None:
        from core.verifier import verify_position_opened
        return await verify_position_opened(self.api, market_id, expected_size, symbol)

    async def _verify_position_closed(self, market_id: int, symbol: str) -> bool:
        from core.verifier import verify_position_closed
        return await verify_position_closed(self.bot, self.api, market_id, symbol)

    async def _check_active_orders(self, market_id: int) -> list[dict]:
        from core.verifier import check_active_orders
        return await check_active_orders(self.bot, self.api, market_id)

    # ── Result/IPC writing ─────────────────────────────────────────

    def _write_ai_result(self, decision: dict, success: bool):
        from core.result_writer import write_ai_result
        write_ai_result(self.bot, self.cfg, self.api, self.tracker, decision, success)

    def _refresh_position_context(self):
        from core.result_writer import refresh_position_context
        refresh_position_context(self.bot, self.cfg, self.api, self.tracker)

    # ── Utilities ──────────────────────────────────────────────────

    def _resolve_market_id(self, symbol: str) -> int | None:
        from core.shared_utils import resolve_market_id
        return resolve_market_id(self.bot, self.tracker, symbol)

    def _log_outcome(self, pos, exit_price: float, exit_reason: str, estimated: bool = False):
        from core.shared_utils import log_outcome
        log_outcome(pos, exit_price, exit_reason, self.cfg, self.tracker, estimated=estimated)

    async def _get_fill_price(self, market_id: int, client_order_index: str | None) -> float | None:
        from core.shared_utils import get_fill_price
        return await get_fill_price(self.bot, self.cfg, market_id, client_order_index)

    def _write_equity_file(self, balance: float):
        from core.shared_utils import write_equity_file
        write_equity_file(self.bot, balance)

    def _should_pace_orders(self) -> bool:
        from core.shared_utils import should_pace_orders
        return should_pace_orders(self.bot)

    def _should_skip_open_for_quota(self) -> bool:
        from core.shared_utils import should_skip_open_for_quota
        return should_skip_open_for_quota(self.bot, self.api)

    def _mark_order_submitted(self):
        from core.shared_utils import mark_order_submitted
        mark_order_submitted(self.bot)
