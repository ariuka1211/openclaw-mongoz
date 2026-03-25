"""
Lighter Copilot — Trailing TP/SL Bot

Monitors open positions on Lighter.xyz and manages
trailing take profit + stop loss orders.
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# ── Logging (MUST be before any imports that trigger logging calls) ─
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from dotenv import load_dotenv

# Load .env before anything else so ${ENV_VAR} placeholders resolve
load_dotenv("/root/.openclaw/workspace/.env")

import lighter
from auth_helper import LighterAuthManager
from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl

import api.proxy_patch  # SOCKS5 proxy monkey-patch (must run before lighter is used)

# ── AI Trader DB (for outcome logging) ──────────────────────────────

# Configurable via env var; also settable in config.yml (ai_trader_dir).
_AI_TRADER_DIR = os.environ.get("AI_TRADER_DIR", str(Path(__file__).parent.parent / "ai-decisions"))
if _AI_TRADER_DIR not in sys.path:
    sys.path.insert(0, _AI_TRADER_DIR)

try:
    from db import DecisionDB
    _db = DecisionDB(f"{_AI_TRADER_DIR}/state/trader.db")
    logging.info("✅ Outcome logging enabled (DecisionDB connected)")
except Exception as _e:
    logging.warning(f"⚠️ Could not init DecisionDB: {_e} — outcomes will not be logged")
    _db = None

# Add shared/ to path for IPC utilities
_shared_dir = Path(__file__).resolve().parent.parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
from ipc_utils import safe_read_json


# ── Config ───────────────────────────────────────────────────────────

from config import BotConfig


# ── Telegram Alerts ──────────────────────────────────────────────────

from alerts.telegram import TelegramAlerter


# ── Position Tracker ─────────────────────────────────────────────────

from core.models import TrackedPosition
from core.position_tracker import PositionTracker
from core.signal_processor import SignalProcessor
from core.state_manager import StateManager
from core.order_manager import OrderManager
from core.execution_engine import ExecutionEngine

# ── Lighter API Wrapper ──────────────────────────────────────────────

from api.lighter_api import LighterAPI

# ── Main Bot ─────────────────────────────────────────────────────────

class LighterCopilot:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.tracker = PositionTracker(cfg)
        self.alerts = TelegramAlerter(cfg.telegram_token, cfg.telegram_chat_id)
        self.running = True
        self.api: LighterAPI | None = None
        self._signals_file = cfg.signals_file
        self._last_signal_timestamp: str | None = None
        # MED-6: Content hash for signal dedup (not just timestamp)
        self._last_signal_hash: str | None = None
        self._opened_signals: set[int] = set()
        self._min_score = 60  # Only open positions for signals >= this score
        self._signal_processed_this_tick: bool = False
        # AI Autopilot
        self._ai_mode = cfg.ai_mode
        self._ai_decision_file = cfg.ai_decision_file
        self._ai_result_file = cfg.ai_result_file
        self._last_ai_decision_ts: str | None = None
        # AI close cooldown — prevent re-opening same symbol after AI closes it
        self._ai_close_cooldown: dict[str, float] = {}  # symbol → monotonic() deadline
        self._ai_cooldown_seconds = 300  # 5 minutes
        self._api_lag_warnings: dict[str, float] = {}  # symbol → last warning timestamp
        self._pending_sync: set[int] = set()  # market_ids opened this tick — skip in sync to avoid race conditions
        self._recently_closed: dict[int, float] = {}  # market_id → monotonic() expire time (bot-closed positions)
        self._verifying_close: set[int] = set()  # MED-25: market_ids being verified as closed — skip DSL/SL
        # Close attempt tracking — prevent infinite close loops
        self._close_attempts: dict[str, int] = {}  # symbol → consecutive close attempts
        self._close_attempt_cooldown: dict[str, float] = {}  # symbol → monotonic() deadline (skip LLM during this)
        self._max_close_attempts = 3  # after N failed close attempts, escalate and cooldown
        self._close_cooldown_seconds = 900  # 15 minutes cooldown after max attempts
        self._close_verify_delay = 5.0  # base delay for position closure verification (progressive delays used)
        self._close_verify_retries = 4  # number of verification attempts with progressive delays (5+10+15+20=50s total)
        # DSL close circuit breaker — mirror of AI close tracking
        self._dsl_close_attempts: dict[str, int] = {}  # symbol → consecutive DSL close attempts
        self._dsl_close_attempt_cooldown: dict[str, float] = {}  # symbol → monotonic() deadline
        self._sl_retry_delays: list[int] = [15, 60, 300, 900]  # 15s, 1min, 5min, 15min
        self._last_order_time: float = 0  # for pacing orders in 15s free tx window
        self._last_quota_alert_time: float = 0  # periodic quota status alert (20min)
        self._quota_alert_interval: float = 3600  # 1 hour in seconds
        self._last_quota_emergency_warn: float = 0  # rate-limit for quota emergency warnings
        # Kill switch — file-based emergency stop
        self._kill_switch_active = False
        self._kill_switch_path = Path(__file__).parent / "state" / "KILL_SWITCH"
        # Saved positions for DSL state restoration across restarts
        self._saved_positions: dict | None = None
        # BUG-07: Track which market_ids were opened by the bot
        self.bot_managed_market_ids: set[int] = set()
        # Orphaned position detection — track consecutive no-price ticks per market
        self._no_price_ticks: dict[int, int] = {}  # market_id → consecutive no-price tick count
        self._no_price_alert_threshold: int = 3  # alert after N consecutive no-price ticks
        # Position sync failure tracking (EDGE-03)
        self._position_sync_failures: int = 0  # consecutive failures
        self._position_sync_failure_threshold: int = 3  # alert after this many consecutive failures
        # Idle polling — reduce API calls when flat with no signals
        self._idle_tick_count: int = 0
        self._idle_threshold: int = 2   # consecutive ticks before extending interval
        self._idle_sleep_interval: int = 60  # seconds between polls when idle (vs normal)
        # HIGH-10: Result dirty flag — prevent refresh from overwriting fresh AI results
        self._result_dirty: bool = False

        # Initialize SignalProcessor (extracted module)
        self.signal_processor = SignalProcessor(cfg, self.api, self.tracker, self.alerts, self)
        # Initialize StateManager (extracted module)
        self.state_manager = StateManager(cfg, self.api, self.tracker, self.alerts, self)
        # Initialize OrderManager (extracted module)
        self.order_manager = OrderManager(cfg, self.api, self)
        # Initialize ExecutionEngine (extracted module)
        self.execution_engine = ExecutionEngine(cfg, self.api, self.tracker, self.alerts, self)


    async def start(self):
        logging.info("🚀 Lighter Copilot starting...")
        logging.info(f"   Account: {self.cfg.account_index}")
        if self.cfg.dsl_enabled:
            logging.info(f"   Mode: DSL (Dynamic Stop Loss)")
            logging.info(f"   Leverage: {self.cfg.default_leverage}x")
            logging.info(f"   Hard SL: {self.cfg.sl_pct}% from entry")
            logging.info(f"   Stagnation: {self.cfg.stagnation_roe_pct}% ROE, {self.cfg.stagnation_minutes}min")
            for t in self.tracker.dsl_cfg.tiers:
                logging.info(f"   Tier: +{t.trigger_pct}% → lock {t.lock_hw_pct}% HW ({t.consecutive_breaches} breaches)")
        else:
            logging.info(f"   Mode: Legacy trailing")
            logging.info(f"   TP trigger: +{self.cfg.trailing_tp_trigger_pct}%")
            logging.info(f"   TP delta: {self.cfg.trailing_tp_delta_pct}%")
            logging.info(f"   SL: trailing {self.cfg.sl_pct}%")

        logging.info("🔗 Initializing Lighter API...")
        self.api = LighterAPI(self.cfg)

        # Verify account tier on startup
        try:
            await self.api._ensure_client()
            await self.api._ensure_signer()
            self._auth_manager = LighterAuthManager(
                signer=self.api._signer,
                account_index=self.cfg.account_index
            )
            auth = self._auth_manager.get_auth_token()
            logging.debug(
                f"🔑 Auth token type={type(auth).__name__} "
                f"len={len(auth) if auth else 0} "
                f"preview={repr(auth[:20]) if auth else 'None'}"
            )
            if not auth:
                raise RuntimeError("LighterAuthManager.get_auth_token() returned empty/None")
            limits = await self.api._account_api.account_limits(
                account_index=self.cfg.account_index,
                auth=auth,
                _request_timeout=30,
            )
            logging.info(
                f"📊 Account tier: {limits.user_tier}, "
                f"effective_lit_stakes={limits.effective_lit_stakes}, "
                f"maker_fee_tick={limits.current_maker_fee_tick}, "
                f"taker_fee_tick={limits.current_taker_fee_tick}"
            )
            if limits.user_tier != "premium":
                logging.warning(f"⚠️ Account tier is '{limits.user_tier}' (not premium) — limits may apply")
        except Exception as e:
            logging.warning(f"⚠️ Account tier check failed: {e}")

        # Check balance — needed for effective leverage (cross margin ROE)
        logging.info("💰 Checking balance...")
        try:
            balance = await self._get_balance()
            if balance > 0:
                self.tracker.account_equity = balance
                logging.info(f"   Balance: ${balance:,.2f} USDC")
                self.state_manager._write_equity_file(balance)
            else:
                logging.warning(f"   Balance fetch returned 0 — using default leverage for ROE")
        except Exception as e:
            logging.warning(f"   Could not fetch balance: {e} — using default leverage for ROE")

        await self.alerts.send(
            "🟢 *Lighter Copilot* started\n"
            f"Account: {self.cfg.account_index}\n"
            f"TP: trail {self.cfg.trailing_tp_delta_pct}% after +{self.cfg.trailing_tp_trigger_pct}%\n"
            f"SL: trailing {self.cfg.sl_pct}%"
        )

        # Restore ephemeral state from disk
        self.state_manager._load_state()

        # Reconcile state with exchange (runs async, catches errors gracefully)
        await self.state_manager._reconcile_state_with_exchange()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        try:
            while self.running:
                await self.execution_engine._tick()
                # Dynamic sleep: fast when active, slow when idle with no positions
                num_positions = len(self.tracker.positions)
                if num_positions == 0 and self._idle_tick_count >= self._idle_threshold:
                    sleep_interval = self._idle_sleep_interval
                else:
                    sleep_interval = self.cfg.price_poll_interval
                logging.debug(f"⏱️ Sleep {sleep_interval}s (positions={num_positions}, idle_ticks={self._idle_tick_count})")
                await asyncio.sleep(sleep_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Bot loop error: {e}")
        finally:
            if self.api:
                try:
                    await self.api.close()
                except Exception as e:
                    logging.error(f"API cleanup error: {e}")
            if hasattr(self, '_http_session') and not self._http_session.closed:
                try:
                    await self._http_session.close()
                except Exception:
                    pass
            try:
                await self.alerts.send("🔴 *Lighter Copilot* stopped")
            except Exception:
                pass
            logging.info("Bot stopped.")

    async def _get_balance(self) -> float:
        """Fetch USDC collateral balance from Lighter."""
        try:
            result = await self.api._account_api.account(
                by="index", value=str(self.cfg.account_index),
                _request_timeout=30,
            )
            for acc in result.accounts:
                return float(acc.collateral) if acc.collateral else 0
        except Exception as e:
            logging.error(f"Failed to fetch balance: {e}")
        return 0

    def _shutdown(self):
        logging.info("Shutdown requested...")
        self.running = False
        self.state_manager._save_state()


# ── Entry Point ──────────────────────────────────────────────────────

def main():
    cfg_path = os.environ.get("BOT_CONFIG", "config.yml")
    if not Path(cfg_path).exists():
        logging.error(f"Config not found: {cfg_path}")
        logging.info("Copy config.example.yml → config.yml and fill in your values.")
        sys.exit(1)

    cfg = BotConfig.from_yaml(cfg_path)

    # Validate config before starting
    cfg_errors = cfg.validate()
    if cfg_errors:
        for err in cfg_errors:
            logging.error(f"Config error: {err}")
        logging.error(f"Fix these issues in {cfg_path} and restart.")
        sys.exit(1)

    bot = LighterCopilot(cfg)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
