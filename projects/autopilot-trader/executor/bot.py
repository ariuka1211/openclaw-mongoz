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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── Logging (MUST be before any imports that trigger logging calls) ─
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

import yaml
from dotenv import load_dotenv

# Load .env before anything else so ${ENV_VAR} placeholders resolve
load_dotenv("/root/.openclaw/workspace/.env")

import lighter
from auth_helper import LighterAuthManager
from dsl import DSLConfig, DSLState, DSLTier, evaluate_dsl

# ── SOCKS5 Proxy Patch ──────────────────────────────────────────────
# Patch lighter.rest.RESTClientObject to support SOCKS5 proxies via aiohttp-socks.
# aiohttp only supports HTTP proxies natively; SOCKS5 requires ProxyConnector.
# This must be applied once at module level before any ApiClient is created.

import lighter.rest as _lighter_rest
import aiohttp_socks as _aiohttp_socks
import aiohttp as _aiohttp

_original_rest_client_init = _lighter_rest.RESTClientObject.__init__


def _patched_rest_client_init(self, configuration):
    """Patched __init__ that uses ProxyConnector for socks5:// proxy URLs."""
    import ssl as _ssl

    proxy = getattr(configuration, "proxy", None)

    if proxy and proxy.startswith("socks5://"):
        # Build SSL context (same logic as original)
        ssl_context = _ssl.create_default_context(
            cafile=configuration.ssl_ca_cert
        )
        if configuration.cert_file:
            ssl_context.load_cert_chain(
                configuration.cert_file, keyfile=configuration.key_file
            )
        if not configuration.verify_ssl:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = _ssl.CERT_NONE

        # Use ProxyConnector for SOCKS5 — it handles the proxy at connector level
        connector = _aiohttp_socks.ProxyConnector.from_url(
            proxy, ssl=ssl_context
        )

        # Store proxy info — clear self.proxy since connector handles it
        self.proxy = None
        self.proxy_headers = configuration.proxy_headers

        # Create pool manager
        self.pool_manager = _aiohttp.ClientSession(
            connector=connector, trust_env=True
        )

        # Set up retry client if configured
        retries = configuration.retries
        if retries is not None:
            import aiohttp_retry as _aiohttp_retry
            self.retry_client = _aiohttp_retry.RetryClient(
                client_session=self.pool_manager,
                retry_options=_aiohttp_retry.ExponentialRetry(
                    attempts=retries,
                    factor=0.0,
                    start_timeout=0.0,
                    max_timeout=120.0,
                ),
            )
        else:
            self.retry_client = None
    else:
        # HTTP proxy or no proxy — use original behavior
        _original_rest_client_init(self, configuration)


_lighter_rest.RESTClientObject.__init__ = _patched_rest_client_init

# ── AI Trader DB (for outcome logging) ──────────────────────────────

# Configurable via env var; also settable in config.yml (ai_trader_dir).
_AI_TRADER_DIR = os.environ.get("AI_TRADER_DIR", str(Path(__file__).parent.parent / "signals" / "ai-trader"))
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

@dataclass
class BotConfig:
    lighter_url: str = "https://mainnet.zklighter.elliot.ai"
    account_index: int = 0
    api_key_index: int = 3
    api_key_private: str = ""

    # Trailing take profit
    trailing_tp_trigger_pct: float = 3.0   # Start trailing after +3%
    trailing_tp_delta_pct: float = 1.0     # Trail by 1% from peak

    # Stop loss
    sl_pct: float = 2.0                    # Stop loss at -2% from entry

    # Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Proxy (for geo-restricted servers)
    proxy_url: str = ""

    # Polling
    price_poll_interval: int = 60
    price_call_delay: float = 5.0  # seconds between sequential get_price() calls within loops

    # AI Autopilot mode
    ai_mode: bool = False
    ai_decision_file: str = "../signals/ai-decision.json"
    ai_result_file: str = "../signals/ai-result.json"
    ai_trader_dir: str = "../signals/ai-trader"
    signals_file: str = "../signals/signals.json"

    # DSL (Dynamic Stop Loss)
    dsl_enabled: bool = True
    default_leverage: float = 10.0
    stagnation_roe_pct: float = 8.0
    stagnation_minutes: int = 60
    dsl_tiers: list = field(default_factory=list)

    # Position management scope
    track_manual_positions: bool = False

    def validate(self) -> list[str]:
        """Validate config values. Returns list of error strings (empty = valid)."""
        errors = []

        # Required non-empty string fields
        for field_name in ("lighter_url", "api_key_private"):
            val = getattr(self, field_name)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"Required field '{field_name}' is missing or empty")

        # Required integer fields (non-negative)
        for field_name in ("account_index", "api_key_index"):
            val = getattr(self, field_name)
            if not isinstance(val, int) or val < 0:
                errors.append(f"Required field '{field_name}' must be a non-negative integer")

        # Positive numbers
        if not isinstance(self.sl_pct, (int, float)) or self.sl_pct <= 0:
            errors.append(f"'sl_pct' must be a positive number, got {self.sl_pct}")

        # Non-negative (0 = immediate trigger)
        for field_name in ("trailing_tp_trigger_pct", "trailing_tp_delta_pct"):
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)) or val < 0:
                errors.append(f"'{field_name}' must be a non-negative number, got {val}")

        # Minimum intervals
        if not isinstance(self.price_poll_interval, (int, float)) or self.price_poll_interval < 1:
            errors.append(f"'price_poll_interval' must be >= 1, got {self.price_poll_interval}")

        if not isinstance(self.default_leverage, (int, float)) or self.default_leverage < 1:
            errors.append(f"'default_leverage' must be >= 1, got {self.default_leverage}")

        # DSL tier validation
        if self.dsl_enabled:
            if not self.dsl_tiers:
                errors.append("'dsl_enabled' is true but 'dsl_tiers' is empty — add at least one tier")
            else:
                prev_trigger = -1
                for i, tier in enumerate(self.dsl_tiers):
                    trigger = tier.get("trigger_pct")
                    lock_hw = tier.get("lock_hw_pct")
                    breaches = tier.get("consecutive_breaches")

                    if not isinstance(trigger, (int, float)) or trigger <= 0:
                        errors.append(f"dsl_tiers[{i}].trigger_pct must be positive, got {trigger}")
                    if not isinstance(lock_hw, (int, float)) or not (0 < lock_hw <= 100):
                        errors.append(f"dsl_tiers[{i}].lock_hw_pct must be in (0, 100], got {lock_hw}")
                    if not isinstance(breaches, int) or breaches < 1:
                        errors.append(f"dsl_tiers[{i}].consecutive_breaches must be >= 1, got {breaches}")

                    buf = tier.get("trailing_buffer_roe")
                    if buf is not None and not isinstance(buf, (int, float)):
                        errors.append(f"dsl_tiers[{i}].trailing_buffer_roe must be numeric or null, got {buf!r}")

                    # Check ascending order
                    if trigger is not None and trigger <= prev_trigger:
                        errors.append(f"dsl_tiers[{i}].trigger_pct ({trigger}) must be > previous ({prev_trigger}) — tiers must be sorted ascending")
                    if trigger is not None:
                        prev_trigger = trigger

        return errors

    @classmethod
    def from_yaml(cls, path: str) -> "BotConfig":
        with open(path) as f:
            raw_text = f.read()
        # Expand ${ENV_VAR} placeholders from environment
        expanded_text = os.path.expandvars(raw_text)
        raw = yaml.safe_load(expanded_text) or {}
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in fields}
        # Coerce numeric string fields (e.g. from env var expansion)
        for key in ("account_index", "api_key_index", "price_poll_interval",
                     "stagnation_minutes", "dsl_enabled"):
            if key in filtered and isinstance(filtered[key], str):
                if key == "dsl_enabled":
                    filtered[key] = filtered[key].lower() in ("true", "1", "yes")
                else:
                    filtered[key] = int(filtered[key])
        for key in ("trailing_tp_trigger_pct", "trailing_tp_delta_pct", "sl_pct",
                     "default_leverage", "stagnation_roe_pct", "price_call_delay"):
            if key in filtered and isinstance(filtered[key], str):
                filtered[key] = float(filtered[key])
        # Coerce nested dsl_tiers numeric fields (may be strings from env var expansion)
        if "dsl_tiers" in filtered and isinstance(filtered["dsl_tiers"], list):
            for tier in filtered["dsl_tiers"]:
                for tkey in ("trigger_pct", "lock_hw_pct", "consecutive_breaches"):
                    if tkey in tier and isinstance(tier[tkey], str):
                        tier[tkey] = float(tier[tkey]) if tkey != "consecutive_breaches" else int(tier[tkey])
                if "trailing_buffer_roe" in tier and isinstance(tier["trailing_buffer_roe"], str):
                    tier["trailing_buffer_roe"] = float(tier["trailing_buffer_roe"])
        return cls(**filtered)


# ── Telegram Alerts ──────────────────────────────────────────────────

class TelegramAlerter:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

    async def send(self, text: str):
        if not self.enabled:
            logging.debug(f"[alert] {text}")
            return
        import aiohttp
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                }
                async with session.post(url, json=payload, timeout=timeout) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get('Retry-After', 'unknown')
                        logging.warning(f"Telegram rate limited (429), retry after {retry_after}s — alert dropped")
                    elif resp.status != 200:
                        body = await resp.text()
                        logging.warning(f"Telegram API error {resp.status}: {body}")
                    else:
                        logging.debug(f"Alert sent to Telegram ({len(text)} chars)")
        except Exception as e:
            logging.warning(f"Telegram send failed: {e}")


# ── Position Tracker ─────────────────────────────────────────────────

@dataclass
class TrackedPosition:
    market_id: int
    symbol: str
    side: str               # "long" or "short"
    entry_price: float
    size: float
    high_water_mark: float
    trailing_active: bool = False
    trailing_sl_level: float | None = None  # legacy: flat trailing stop loss level
    dsl_state: DSLState | None = None       # DSL state (when dsl_enabled)
    sl_pct: float | None = None             # per-position stop loss % (from AI), None = use config default
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # CRITICAL-2: Unverified position tracking — set when open_order succeeds but verification fails
    unverified_at: float | None = None      # time.time() when marked unverified
    unverified_ticks: int = 0               # consecutive ticks in unverified state
    active_sl_order_id: str | None = None   # MED-18: cancel API tracking


class PositionTracker:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.positions: dict[int, TrackedPosition] = {}  # key: market_id
        # Build DSL config from bot config
        self.dsl_cfg = DSLConfig(
            stagnation_roe_pct=cfg.stagnation_roe_pct,
            stagnation_minutes=cfg.stagnation_minutes,
            hard_sl_pct=cfg.sl_pct,
        )
        # Parse custom tiers from config if present
        if cfg.dsl_tiers:
            self.dsl_cfg.tiers = [
                DSLTier(
                    trigger_pct=t.get("trigger_pct", 7),
                    lock_hw_pct=t.get("lock_hw_pct", 40),
                    trailing_buffer_roe=t.get("trailing_buffer_roe"),
                    consecutive_breaches=t.get("consecutive_breaches", 3),
                )
                for t in cfg.dsl_tiers
            ]

    def compute_tp_price(self, pos: TrackedPosition) -> float | None:
        """Calculate trailing take-profit price based on high-water mark.
        
        Returns None if:
        - For longs: high-water mark hasn't exceeded trigger level yet
        - For shorts: high-water mark hasn't dropped below trigger level yet (at entry, returns None)
        """
        if pos.side == "long":
            trigger = pos.entry_price * (1 + self.cfg.trailing_tp_trigger_pct / 100)
            if pos.high_water_mark < trigger:
                return None
            return pos.high_water_mark * (1 - self.cfg.trailing_tp_delta_pct / 100)
        else:
            trigger = pos.entry_price * (1 - self.cfg.trailing_tp_trigger_pct / 100)
            if pos.high_water_mark > trigger:
                return None
            return pos.high_water_mark * (1 + self.cfg.trailing_tp_delta_pct / 100)

    def compute_sl_price(self, pos: TrackedPosition) -> float:
        """Return trailing stop loss level. Trails upward on longs, downward on shorts."""
        sl_pct = self._get_sl_pct(pos)
        return pos.trailing_sl_level or pos.entry_price * (1 - sl_pct / 100 if pos.side == "long" else 1 + sl_pct / 100)

    def _get_sl_pct(self, pos: TrackedPosition) -> float:
        """Get effective stop loss % — per-position (AI) or config default."""
        return pos.sl_pct if pos.sl_pct is not None else self.cfg.sl_pct

    def update_price(self, market_id: int, price: float) -> str | None:
        pos = self.positions.get(market_id)
        if not pos:
            return None

        # ── DSL mode: tiered trailing stop loss ──
        if self.cfg.dsl_enabled and pos.dsl_state:
            result = evaluate_dsl(pos.dsl_state, price, self.dsl_cfg)
            roe = pos.dsl_state.current_roe(price)
            if result:
                logging.info(
                    f"🛑 {pos.symbol} DSL {result} | "
                    f"ROE: {roe:+.1f}% | HW: {pos.dsl_state.high_water_roe:+.1f}% | "
                    f"Tier: {pos.dsl_state.current_tier.trigger_pct if pos.dsl_state.current_tier else 'none'} | "
                    f"Floor: {pos.dsl_state.locked_floor_roe:+.1f}%" if pos.dsl_state.locked_floor_roe else ""
                )
                return result

            # Log DSL state periodically (when tier changes or HW updates)
            if pos.dsl_state.current_tier:
                logging.debug(
                    f"📊 {pos.symbol} DSL | ROE: {roe:+.1f}% | "
                    f"HW: {pos.dsl_state.high_water_roe:+.1f}% | "
                    f"Tier: {pos.dsl_state.current_tier.trigger_pct}% | "
                    f"Breaches: {pos.dsl_state.breach_count}/{pos.dsl_state.current_tier.consecutive_breaches}"
                )

            # Alert on tier lock
            if pos.dsl_state.locked_floor_roe is not None and not getattr(pos, '_tier_lock_alerted', False):
                pos._tier_lock_alerted = True
                floor_price = pos.entry_price * (1 + pos.dsl_state.locked_floor_roe / 100 / self.cfg.default_leverage) if pos.side == "long" \
                    else pos.entry_price * (1 - pos.dsl_state.locked_floor_roe / 100 / self.cfg.default_leverage)
                return ("dsl_tier_lock", {
                    "roe": roe,
                    "floor_roe": pos.dsl_state.locked_floor_roe,
                    "floor_price": floor_price,
                    "tier": pos.dsl_state.current_tier.trigger_pct if pos.dsl_state.current_tier else 0,
                    "breaches": pos.dsl_state.breach_count,
                })

            # Alert on stagnation timer start
            if pos.dsl_state.stagnation_started and not getattr(pos, '_stagnation_alerted', False):
                pos._stagnation_alerted = True
                return ("dsl_stagnation_timer", {
                    "roe": roe,
                    "since": pos.dsl_state.stagnation_started,
                })

            return None

        # ── Legacy mode: flat trailing TP/SL ──
        # Update high water mark (for trailing TP)
        trailing_just_activated = False
        if pos.side == "long" and price > pos.high_water_mark:
            pos.high_water_mark = price
            # Keep DSL state in sync for potential mode switch
            if pos.dsl_state:
                pos.dsl_state.high_water_price = pos.high_water_mark
                pos.dsl_state.high_water_time = datetime.now(timezone.utc)
            if not pos.trailing_active:
                trigger = pos.entry_price * (1 + self.cfg.trailing_tp_trigger_pct / 100)
                if price >= trigger:
                    pos.trailing_active = True
                    trailing_just_activated = True
                    logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${price:,.2f}")
        elif pos.side == "short" and price < pos.high_water_mark:
            pos.high_water_mark = price
            # Keep DSL state in sync for potential mode switch
            if pos.dsl_state:
                pos.dsl_state.high_water_price = pos.high_water_mark
                pos.dsl_state.high_water_time = datetime.now(timezone.utc)
            if not pos.trailing_active:
                trigger = pos.entry_price * (1 - self.cfg.trailing_tp_trigger_pct / 100)
                if price <= trigger:
                    pos.trailing_active = True
                    trailing_just_activated = True
                    logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${price:,.2f}")

        # Update trailing stop loss (ratchets up on longs, down on shorts — never reverses)
        sl_pct = self._get_sl_pct(pos)
        if pos.side == "long":
            candidate = price * (1 - sl_pct / 100)
            if pos.trailing_sl_level is None or candidate > pos.trailing_sl_level:
                old = pos.trailing_sl_level
                pos.trailing_sl_level = candidate
                if old is not None:
                    logging.info(f"🛡️ {pos.symbol} trailing SL advanced: ${old:,.2f} → ${candidate:,.2f}")
        else:
            candidate = price * (1 + sl_pct / 100)
            if pos.trailing_sl_level is None or candidate < pos.trailing_sl_level:
                old = pos.trailing_sl_level
                pos.trailing_sl_level = candidate
                if old is not None:
                    logging.info(f"🛡️ {pos.symbol} trailing SL advanced: ${old:,.2f} → ${candidate:,.2f}")

        # Alert on trailing TP activation
        if trailing_just_activated:
            pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100) if pos.side == "long" \
                else ((pos.entry_price - price) / pos.entry_price * 100)
            return ("trailing_activated", {
                "price": price,
                "roe": pnl_pct * self.cfg.default_leverage,
                "pnl": pnl_pct,
            })

        # Check triggers
        sl_price = self.compute_sl_price(pos)
        tp_price = self.compute_tp_price(pos)
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100 if pos.side == "long" \
            else (pos.entry_price - price) / pos.entry_price * 100

        if pos.side == "long":
            if price <= sl_price:
                return "stop_loss"
            if pos.trailing_active and tp_price and price <= tp_price and pnl_pct > 0:
                return "trailing_take_profit"
        else:
            if price >= sl_price:
                return "stop_loss"
            if pos.trailing_active and tp_price and price >= tp_price and pnl_pct > 0:
                return "trailing_take_profit"

        return None

    def add_position(self, market_id: int, symbol: str, side: str, entry: float, size: float, leverage: float = None, sl_pct: float = None):
        lev = leverage or self.cfg.default_leverage
        dsl_state = None
        if self.cfg.dsl_enabled:
            dsl_state = DSLState(
                side=side,
                entry_price=entry,
                leverage=lev,
                high_water_price=entry,
                high_water_time=datetime.now(timezone.utc),
            )
        pos = TrackedPosition(
            market_id=market_id,
            symbol=symbol,
            side=side,
            entry_price=entry,
            size=size,
            high_water_mark=entry,
            dsl_state=dsl_state,
            sl_pct=sl_pct,
        )
        self.positions[market_id] = pos
        sl_source = f"AI={sl_pct}%" if sl_pct is not None else f"config={self.cfg.sl_pct}%"
        mode = f"DSL (lev={lev}x)" if self.cfg.dsl_enabled else "legacy trailing"
        logging.info(f"📌 Tracking: {side.upper()} {symbol} @ ${entry:,.2f}, size={size}, mode={mode}, SL={sl_source}")

    def remove_position(self, market_id: int):
        self.positions.pop(market_id, None)


class VolumeQuotaError(Exception):
    """Raised when an order is rejected due to volume quota exhaustion."""
    pass


# ── Lighter API Wrapper ──────────────────────────────────────────────

class LighterAPI:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._market_decimals: dict[int, tuple[int, int]] = {}  # market_id → (size_decimals, price_decimals)
        # Symbol cache: market_id → (timestamp, symbol) — TTL 5 seconds
        self._symbol_cache: dict[int, tuple[float, str]] = {}
        self._symbol_cache_ttl = 5.0  # seconds
        self._config = lighter.Configuration(host=cfg.lighter_url)
        if cfg.proxy_url:
            self._config.proxy = cfg.proxy_url
            logging.info(f"   Proxy: {cfg.proxy_url.split('@')[-1]}")
        
        # Defer client creation to async context (aiohttp needs event loop)
        self._client = None
        self._account_api = None
        self._order_api = None

        # Monotonic counter seeded from timestamp — avoids collisions across restarts
        self._client_order_index = int(time.time() * 1000)

        # ── SignerClient — created lazily in _ensure_signer() ──
        self._signer = None  # Created lazily in async context (see _ensure_signer)
        self._signer_has_own_client = bool(cfg.proxy_url)

        # Persisted tracked market IDs
        self._state_dir = Path(__file__).parent / "state"
        self._tracked_markets_file = self._state_dir / "tracked_markets.json"
        self.tracked_market_ids: list[int] = self._load_tracked_markets()

        # Mark price cache — derived from unrealized_pnl during position sync
        # More accurate than recent_trades for ROE/stop-loss calculations
        self._mark_prices: dict[int, dict] = {}  # market_id → {"price": float, "time": float}

        # Volume quota tracking — must exist before any async calls
        self._volume_quota_remaining: int | None = None
        self._last_known_quota: int | None = None  # last non-None quota value
        self._last_quota_time: float = 0  # when _last_known_quota was last updated

        # Load persisted quota cache from state file (survives restarts)
        self._quota_state_file = self._state_dir / "quota_state.json"
        self._load_quota_cache()

    async def _ensure_client(self):
        """Lazy initialization of API clients in async context."""
        if self._client is not None:
            return
        self._client = lighter.ApiClient(self._config)
        self._account_api = lighter.AccountApi(self._client)
        self._order_api = lighter.OrderApi(self._client)

    async def _ensure_signer(self):
        """Lazy initialization of signer in async context."""
        if self._signer is not None:
            return
            
        if self.cfg.proxy_url:
            signer_config = lighter.Configuration(host=self.cfg.lighter_url)
            signer_config.proxy = self.cfg.proxy_url
            from lighter.signer_client import get_signer

            class ProxySignerClient(lighter.SignerClient):
                """SignerClient subclass that uses a proxy-configured ApiClient."""
                def __init__(self_inner, url, account_index, api_private_keys, nonce_manager_type=lighter.nonce_manager.NonceManagerType.OPTIMISTIC):
                    # Replicate parent init but use proxy config for ApiClient
                    self_inner.url = url
                    self_inner.chain_id = 304 if ("mainnet" in url or "api" in url) else 300
                    self_inner.validate_api_private_keys(api_private_keys)
                    self_inner.api_key_dict = api_private_keys
                    self_inner.account_index = account_index
                    self_inner.signer = get_signer()
                    self_inner.api_client = lighter.ApiClient(configuration=signer_config)
                    self_inner.tx_api = lighter.TransactionApi(self_inner.api_client)
                    self_inner.order_api = lighter.OrderApi(self_inner.api_client)
                    self_inner.nonce_manager = lighter.nonce_manager.nonce_manager_factory(
                        nonce_manager_type=nonce_manager_type,
                        account_index=account_index,
                        api_client=self_inner.api_client,
                        api_keys_list=list(api_private_keys.keys()),
                    )
                    for api_key_index in api_private_keys.keys():
                        self_inner.create_client(api_key_index)

            self._signer = ProxySignerClient(
                url=self.cfg.lighter_url,
                account_index=self.cfg.account_index,
                api_private_keys={self.cfg.api_key_index: self.cfg.api_key_private},
            )
        else:
            self._signer = lighter.SignerClient(
                url=self.cfg.lighter_url,
                account_index=self.cfg.account_index,
                api_private_keys={self.cfg.api_key_index: self.cfg.api_key_private},
            )

        # Volume quota tracking
        self._volume_quota_remaining = None

    def _extract_quota_from_response(self, resp) -> tuple[int | None, str]:
        """Extract quota with detailed field analysis."""
        if resp is None:
            return None, "no_response_object"
        
        # Standard field
        if hasattr(resp, 'volume_quota_remaining'):
            val = getattr(resp, 'volume_quota_remaining')
            return val, f"standard_field_value:{val}"
        
        # Try alternative field names
        for field in ['volumeQuotaRemaining', 'quota_remaining', 'quota', 'volume_quota']:
            if hasattr(resp, field):
                val = getattr(resp, field)
                return val, f"alt_field_{field}_value:{val}"
        
        # Check if it's in a nested object
        if hasattr(resp, 'additional_properties'):
            props = getattr(resp, 'additional_properties')
            if props and 'volume_quota_remaining' in props:
                val = props['volume_quota_remaining']
                return val, f"additional_properties_value:{val}"
        
        return None, f"not_found_in_response_type:{type(resp).__name__}"

    @property
    def volume_quota_remaining(self) -> int | None:
        return self._volume_quota_remaining

    def _update_quota_cache(self, quota: int | None) -> None:
        """Update volume quota and cache last-known value for alerting."""
        if quota is not None:
            self._volume_quota_remaining = quota
            self._last_known_quota = quota
            self._last_quota_time = time.time()
            self._save_quota_cache()
            if quota == 0:
                logging.warning("⚠️ Volume quota depleted — next orders need free window")
        else:
            self._volume_quota_remaining = None

    async def get_positions(self) -> list[dict] | None:
        """Fetch open positions from Lighter.

        Returns list of positions on success, None on API/network failure.
        This distinction is critical: [] means "no positions" (API worked),
        None means "can't reach the API" (don't change anything).
        """
        try:
            await self._ensure_client()
            result = await self._account_api.account(
                by="index", value=str(self.cfg.account_index),
                _request_timeout=30,
            )
            positions = []
            for account in result.accounts:
                for pos in account.positions:
                    # pos is a Position object with size and avg_entry_price
                    if hasattr(pos, 'position') and pos.position:
                        size = float(pos.position)
                        if size == 0:
                            continue
                        entry = float(pos.avg_entry_price) if hasattr(pos, 'avg_entry_price') and pos.avg_entry_price else 0
                        market_id = pos.market_id if hasattr(pos, 'market_id') else (pos.market_index if hasattr(pos, 'market_index') else 0)
                        # Get symbol from market_id
                        symbol = await self._get_symbol(market_id)
                        # Extract unrealized_pnl for mark price derivation
                        unrealized_pnl = float(pos.unrealized_pnl) if hasattr(pos, 'unrealized_pnl') and pos.unrealized_pnl else 0.0
                        # Extract initial_margin_fraction and compute effective leverage
                        margin_fraction = float(pos.initial_margin_fraction) if hasattr(pos, 'initial_margin_fraction') and pos.initial_margin_fraction else 0
                        if margin_fraction > 0:
                            effective_leverage = round(min(100.0 / margin_fraction, self.cfg.default_leverage), 1)
                        else:
                            effective_leverage = self.cfg.default_leverage
                        sign = int(pos.sign) if hasattr(pos, 'sign') else 1
                        side = "long" if sign > 0 else "short"
                        positions.append({
                            "market_id": market_id,
                            "symbol": symbol,
                            "size": size,
                            "side": side,
                            "entry_price": entry,
                            "unrealized_pnl": unrealized_pnl,
                            "leverage": effective_leverage,
                        })
            return positions
        except Exception as e:
            logging.error(f"Failed to fetch positions (API/network error): {e}")
            return None

    async def _get_symbol(self, market_id: int) -> str:
        """Get symbol for a market ID with 5-second TTL cache."""
        now = time.monotonic()
        cached = self._symbol_cache.get(market_id)
        if cached and (now - cached[0]) < self._symbol_cache_ttl:
            return cached[1]

        try:
            await self._ensure_client()
            books = await self._order_api.order_books()
            for b in books.order_books:
                self._symbol_cache[b.market_id] = (now, b.symbol)
                self._market_decimals[b.market_id] = (
                    b.supported_size_decimals,
                    b.supported_price_decimals,
                )
            # Return the requested symbol from the freshly populated cache
            if market_id in self._symbol_cache:
                return self._symbol_cache[market_id][1]
        except Exception:
            pass
        return f"MKT{market_id}"

    async def _ensure_decimals(self, market_id: int) -> tuple[int, int]:
        """Get (size_decimals, price_decimals) for a market, fetching if needed."""
        if market_id not in self._market_decimals:
            await self._get_symbol(market_id)  # populates cache
        return self._market_decimals.get(market_id, (4, 2))  # safe defaults

    @staticmethod
    def _to_lighter_amount(value: float, decimals: int) -> int:
        """Convert a float amount/price to Lighter's integer representation."""
        return int(round(abs(value) * (10 ** decimals)))

    def _next_client_order_index(self) -> int:
        """Generate a unique client order index using monotonic counter."""
        self._client_order_index += 1
        return self._client_order_index % (2**32)

    async def get_price(self, market_id: int) -> float | None:
        """Get latest price for a market from recent trades. Retries up to 3 times on failure."""
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                await self._ensure_client()
                trades = await self._order_api.recent_trades(market_id=market_id, limit=1)
                if trades.trades:
                    return float(trades.trades[0].price)
                return None
            except Exception as e:
                if attempt < max_retries:
                    logging.warning(f"get_price(market={market_id}) attempt {attempt}/{max_retries} failed: {e} — retrying in 1s")
                    await asyncio.sleep(1)
                else:
                    logging.error(f"Failed to get price for market {market_id} after {max_retries} attempts: {e}")
        return None

    async def get_all_prices(self) -> dict[int, float]:
        """Get prices for all tracked position markets."""
        prices = {}
        for i, market_id in enumerate(self.tracked_market_ids):
            price = await self.get_price(market_id)
            if price:
                prices[market_id] = price
            if i < len(self.tracked_market_ids) - 1:
                await asyncio.sleep(self.cfg.price_call_delay)
        return prices

    def update_mark_prices_from_positions(self, positions: list[dict]):
        """Cache mark prices derived from unrealized_pnl (from account API).

        This is the authoritative price the exchange uses for PnL calculations.
        More accurate than recent_trades for ROE and stop-loss evaluation.
        """
        for pos in positions:
            mid = pos["market_id"]
            entry = pos.get("entry_price", 0)
            size = pos.get("size", 0)
            pnl = pos.get("unrealized_pnl", 0)
            if entry <= 0 or size == 0:
                continue
            # Reverse-engineer mark price from unrealized PnL
            # For longs: pnl = size * (mark - entry) → mark = entry + pnl/size
            # For shorts: pnl = abs(size) * (entry - mark) → mark = entry - pnl/abs(size)
            abs_size = abs(size)
            if pos.get("side") == "short":
                mark_price = entry - (pnl / abs_size)
            else:
                mark_price = entry + (pnl / abs_size)
            if mark_price > 0:
                self._mark_prices[mid] = {"price": mark_price, "time": time.time()}

    def get_mark_price(self, market_id: int) -> float | None:
        """Get mark price for a position. Uses cached mark price from account API
        (derived from unrealized_pnl), falls back to recent_trades.
        Returns None if mark price is stale (>30s old)."""
        cached = self._mark_prices.get(market_id)
        if cached and cached["price"] > 0:
            age = time.time() - cached["time"]
            if age > 30:
                logging.debug(f"Mark price for market {market_id} is stale ({age:.1f}s), skipping")
                return None
            return cached["price"]
        return None

    async def get_price_with_mark_fallback(self, market_id: int) -> float | None:
        """Get price for ROE evaluation. Tries mark price first, falls back to recent_trades."""
        mark = self.get_mark_price(market_id)
        if mark:
            return mark
        return await self.get_price(market_id)

    def set_tracked_markets(self, market_ids: list[int]):
        """Set which markets to poll prices for."""
        self.tracked_market_ids = market_ids

    def _load_tracked_markets(self) -> list[int]:
        """Load tracked market IDs from state file."""
        try:
            if self._tracked_markets_file.exists():
                with open(self._tracked_markets_file) as f:
                    ids = json.load(f)
                if isinstance(ids, list):
                    logging.info(f"📂 Loaded {len(ids)} tracked market IDs from state")
                    return ids
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Failed to load tracked markets: {e}")
        return []

    def _save_tracked_markets(self):
        """Persist tracked market IDs to state file."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with open(self._tracked_markets_file, "w") as f:
                json.dump(self.tracked_market_ids, f)
        except OSError as e:
            logging.warning(f"Failed to save tracked markets: {e}")

    def _load_quota_cache(self):
        """Load persisted quota from state file."""
        try:
            if self._quota_state_file.exists():
                with open(self._quota_state_file) as f:
                    data = json.load(f)
                quota = data.get("quota")
                ts = data.get("timestamp", 0)
                if isinstance(quota, int) and quota >= 0:
                    self._last_known_quota = quota
                    self._last_quota_time = float(ts)
                    logging.info(f"📂 Loaded quota cache: {quota} TX (from {time.strftime('%H:%M:%S', time.localtime(ts))})")
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Failed to load quota cache: {e}")

    def _save_quota_cache(self):
        """Persist quota to state file."""
        if self._last_known_quota is None:
            return
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with open(self._quota_state_file, "w") as f:
                json.dump({"quota": self._last_known_quota, "timestamp": self._last_quota_time}, f)
        except OSError as e:
            logging.warning(f"Failed to save quota cache: {e}")

    async def execute_tp(self, market_id: int, size: float, trigger_price: float, is_long: bool) -> bool:
        """Execute a take profit order (reduce-only, IOC)."""
        try:
            await self._ensure_signer()
            size_dec, price_dec = await self._ensure_decimals(market_id)
            base_amount = self._to_lighter_amount(size, size_dec)
            price = self._to_lighter_amount(trigger_price, price_dec)
            result = await self._signer.create_tp_order(
                market_index=market_id,
                client_order_index=self._next_client_order_index(),
                base_amount=base_amount,
                trigger_price=price,
                price=price,
                is_ask=is_long,      # close long = sell = ask
                reduce_only=True,
            )
            # SDK returns Union[Tuple[CreateOrder, RespSendTx, None], Tuple[None, None, str]]
            if isinstance(result, tuple):
                if len(result) >= 3 and result[2] is not None:
                    logging.error(f"❌ TP order rejected by exchange: {result[2]}")
                    return False
                if result[0] is None:
                    logging.error("❌ TP order returned None (no order created)")
                    return False
                # Log response details
                tx = result[0]
                resp = result[1] if len(result) > 1 else None
                if resp is not None:
                    # 🔍 DEBUG: Raw response structure analysis
                    quota_val, quota_detail = self._extract_quota_from_response(resp)
                    logging.debug(f"🔍 TP response quota extraction: {quota_detail}")
                    
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    resp_quota = quota_val  # Use enhanced extraction result
                    self._update_quota_cache(resp_quota)
                    if resp_msg and "didn't use volume quota" in str(resp_msg):
                        logging.info(f"✅ TP order submitted (free slot): tx={tx}, msg={resp_msg}")
                    else:
                        logging.info(f"✅ TP order submitted: tx={tx}, resp_code={resp_code}, resp_msg={resp_msg}, vol_quota={resp_quota}")
                else:
                    logging.info(f"✅ TP order submitted: {tx}")
                return True
            # Fallback: if SDK returns something unexpected, treat as success with warning
            logging.warning(f"⚠️ TP order unexpected return type: {type(result)} — {result}")
            return True
        except Exception as e:
            logging.error(f"Failed to execute TP: {e}")
            return False

    async def open_position(self, market_id: int, size_usd: float, is_long: bool, current_price: float) -> bool:
        """Open a market position."""
        try:
            size_dec, price_dec = await self._ensure_decimals(market_id)
            # Convert USD size to base amount
            base_amount = self._to_lighter_amount(size_usd / current_price, size_dec)
            # Get best price and add slippage
            await self._ensure_signer()
            best_price_int = await self._signer.get_best_price(market_id, is_ask=not is_long)
            slippage = int(best_price_int * 0.05)
            if is_long:
                worst_price = best_price_int + slippage  # buy: accept higher price
            else:
                worst_price = best_price_int - slippage  # sell: accept lower price

            result = await self._signer.create_market_order(
                market_index=market_id,
                client_order_index=self._next_client_order_index(),
                base_amount=base_amount,
                avg_execution_price=worst_price,
                is_ask=not is_long,   # buy = not ask (long), sell = ask (short)
            )
            # SDK returns Union[Tuple[CreateOrder, RespSendTx, None], Tuple[None, None, str]]
            if isinstance(result, tuple):
                if len(result) >= 3 and result[2] is not None:
                    logging.error(f"❌ Open order rejected by exchange: {result[2]}")
                    return False
                if result[0] is None:
                    logging.error("❌ Open order returned None (no order created)")
                    return False
                tx = result[0]
                resp = result[1] if len(result) > 1 else None
                if resp is not None:
                    # 🔍 DEBUG: Raw response structure analysis
                    quota_val, quota_detail = self._extract_quota_from_response(resp)
                    logging.debug(f"🔍 Open response quota extraction: {quota_detail}")
                    
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    resp_quota = quota_val  # Use enhanced extraction result
                    self._update_quota_cache(resp_quota)
                    if resp_msg and "didn't use volume quota" in str(resp_msg):
                        logging.info(f"✅ Open order submitted (free slot): tx={tx}, msg={resp_msg}")
                    logging.info(f"✅ Position opened: {'LONG' if is_long else 'SHORT'} {size_usd:.2f} USD -> tx={tx}, resp_code={resp_code}, resp_msg={resp_msg}, vol_quota={resp_quota}")
                else:
                    logging.info(f"✅ Position opened: {'LONG' if is_long else 'SHORT'} {size_usd:.2f} USD -> {tx}")
                return True
            logging.warning(f"⚠️ Open order unexpected return type: {type(result)} — {result}")
            return True
        except Exception as e:
            logging.error(f"Failed to open position: {e}")
            return False

    async def execute_sl(self, market_id: int, size: float, trigger_price: float, is_long: bool) -> tuple[bool, str | None]:
        """Execute a stop loss as a market order (reduce-only, IOC).
        Returns (success, client_order_index_or_none).
        Uses create_market_order_if_slippage which pre-checks the order book
        to ensure the order will actually fill at acceptable slippage.
        """
        try:
            size_dec, price_dec = await self._ensure_decimals(market_id)
            base_amount = self._to_lighter_amount(size, size_dec)

            # Get best price for reference
            await self._ensure_signer()
            best_price_int = await self._signer.get_best_price(market_id, is_ask=is_long)
            client_order_index = self._next_client_order_index()

            # Use create_market_order_if_slippage — pre-checks order book before submitting
            result = await self._signer.create_market_order_if_slippage(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=base_amount,
                max_slippage=0.05,
                is_ask=is_long,      # close long = sell = ask
                reduce_only=True,
                ideal_price=best_price_int,
            )
            logging.info(
                f"🔍 SL order: market={market_id}, size={size}, base_amount={base_amount}, "
                f"best_price={best_price_int}, max_slippage=0.05, is_ask={is_long}, "
                f"reduce_only=True, coi={client_order_index}"
            )
            # SDK returns Union[Tuple[CreateOrder, RespSendTx, None], Tuple[None, None, str]]
            if isinstance(result, tuple):
                if len(result) >= 3 and result[2] is not None:
                    error_msg = result[2]
                    logging.error(f"❌ SL order rejected by exchange: {error_msg}")
                    # Check for specific error types
                    if "slippage" in str(error_msg).lower():
                        logging.error(f"❌ SL order rejected: excessive slippage — market may have moved beyond 5%")
                    return False, None
                if result[0] is None:
                    logging.error("❌ SL order returned None (no order created)")
                    return False, None
                # Log full response details for debugging
                tx = result[0]
                resp = result[1] if len(result) > 1 else None
                if resp is not None:
                    # 🔍 DEBUG: Raw response structure analysis
                    quota_val, quota_detail = self._extract_quota_from_response(resp)
                    logging.debug(f"🔍 SL response quota extraction: {quota_detail}")
                    
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    resp_tx_hash = getattr(resp, 'tx_hash', None)
                    resp_pred_ms = getattr(resp, 'predicted_execution_time_ms', None)
                    resp_quota = quota_val  # Use enhanced extraction result
                    self._update_quota_cache(resp_quota)
                    if resp_msg and "didn't use volume quota" in str(resp_msg):
                        logging.info(f"✅ SL order submitted (free slot): tx={resp_tx_hash}, msg={resp_msg}")
                    else:
                        logging.info(
                            f"✅ SL order submitted: code={resp_code}, msg={resp_msg}, "
                            f"tx_hash={resp_tx_hash}, predicted_exec_ms={resp_pred_ms}, "
                            f"vol_quota={resp_quota}, coi={client_order_index}"
                        )
                    # Warn if code is not the expected OK value
                    if resp_code is not None and resp_code != 200:
                        logging.warning(f"⚠️ SL order response code is {resp_code} (expected 200)")
                else:
                    logging.info(f"✅ SL order submitted (no response object): coi={client_order_index}")
                return True, str(client_order_index)
            # Fallback: if SDK returns something unexpected, treat as success with warning
            logging.warning(f"⚠️ SL order unexpected return type: {type(result)} — {result}")
            return True, str(client_order_index)
        except Exception as e:
            logging.error(f"Failed to execute SL: {e}")
            return False, None

    async def _cancel_order(self, market_index: int, order_index: int) -> bool:
        """Cancel an active order by its order_index.

        Uses the signer's on-chain cancel_order transaction.
        Returns True if cancellation succeeded, False otherwise.
        """
        try:
            await self._ensure_signer()
            result = await self._signer.cancel_order(
                market_index=market_index,
                order_index=order_index,
            )
            if isinstance(result, tuple):
                if len(result) >= 3 and result[2] is not None:
                    logging.warning(f"⚠️ Cancel order rejected: {result[2]}")
                    return False
                tx = result[0]
                resp = result[1] if len(result) > 1 else None
                if resp is not None:
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    logging.info(f"✅ Cancelled order {order_index} (code={resp_code}, msg={resp_msg})")
                else:
                    logging.info(f"✅ Cancelled order {order_index}: tx={tx}")
                return True
            logging.warning(f"⚠️ Cancel order unexpected return type: {type(result)}")
            return True
        except Exception as e:
            logging.warning(f"⚠️ Failed to cancel order {order_index}: {e}")
            return False

    async def close(self):
        """Close all aiohttp sessions."""
        errors = []
        # Close main API client
        try:
            if self._client is not None:
                await self._client.close()
        except Exception as e:
            errors.append(f"main client: {e}")
        # Close signer's API client if it has its own proxy-configured client
        try:
            if self._signer and self._signer_has_own_client and hasattr(self._signer, 'api_client') and self._signer.api_client is not self._client:
                await self._signer.api_client.close()
        except Exception as e:
            errors.append(f"signer client: {e}")
        if errors:
            logging.warning(f"API close errors: {'; '.join(errors)}")


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
        # Volume quota cooldown
        self._volume_quota_cooldown_until: float = 0  # timestamp when cooldown expires
        self._volume_quota_backoff_seconds: int = 60  # current backoff duration
        self._volume_quota_max_backoff: int = 300  # 5 minutes max
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

        # Check balance
        logging.info("💰 Checking balance...")
        # TODO: Re-enable once API rate limits are resolved
        # try:
        #     result = await self.api._account_api.account(
        #         by="index", value=str(self.cfg.account_index)
        #     )
        #     for acc in result.accounts:
        #         balance = float(acc.collateral) if acc.collateral else 0
        #         logging.info(f"   Balance: ${balance:,.2f} USDC")
        # except Exception as e:
        #     logging.warning(f"Could not fetch balance: {e}")
        logging.info("   Balance check skipped due to API rate limits")

        await self.alerts.send(
            "🟢 *Lighter Copilot* started\n"
            f"Account: {self.cfg.account_index}\n"
            f"TP: trail {self.cfg.trailing_tp_delta_pct}% after +{self.cfg.trailing_tp_trigger_pct}%\n"
            f"SL: trailing {self.cfg.sl_pct}%"
        )

        # Restore ephemeral state from disk
        self._load_state()

        # Reconcile state with exchange (runs async, catches errors gracefully)
        await self._reconcile_state_with_exchange()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        try:
            while self.running:
                await self._tick()
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

    async def _process_signals(self):
        """Read signals.json and open positions for new, unopened signals."""
        if self._kill_switch_active:
            logging.warning("🚫 Kill switch active — _process_signals() skipping new opens")
            return

        signals_path = Path(self._signals_file)
        if not signals_path.exists():
            return

        data = safe_read_json(signals_path)
        if data is None:
            logging.warning(f"Failed to read signals file: {signals_path}")
            return

        # MED-6: Content-based dedup — hash opportunities (timestamp+symbol+score) so
        # same-second signals with different content are not silently dropped
        opp_hash = hashlib.sha256(json.dumps(
            data.get("opportunities", []),
            sort_keys=True, default=str
        ).encode()).hexdigest()[:16]
        if opp_hash == self._last_signal_hash:
            return
        self._last_signal_hash = opp_hash
        self._last_signal_timestamp = data.get("timestamp")
        self._signal_processed_this_tick = True

        # Auto-detect balance and scale positions proportionally
        balance = await self._get_balance()
        scanner_equity = data.get("config", {}).get("accountEquity", balance)
        if balance <= 0:
            logging.warning("⚠️ Zero or negative balance, cannot open positions")
            return
        scale = balance / scanner_equity
        if abs(scale - 1.0) > 0.01:
            logging.info(f"📐 Scaling positions: balance=${balance:.2f} / scanner_equity=${scanner_equity:.2f} = {scale:.4f}×")

        # HIGH-12: Write equity to shared state file for dashboard
        self._write_equity_file(balance)

        for opp in data.get("opportunities", []):
            mid = opp["marketId"]
            symbol = opp["symbol"]
            direction = opp.get("direction", "long")

            # Filter by minimum score
            score = opp.get("compositeScore", 0)
            if score < self._min_score:
                continue

            # Only open if safety checks passed
            if not opp.get("safetyPass", False):
                logging.debug(f"⚠️ {symbol}: safety check failed — {opp.get('safetyReason', 'unknown')}")
                continue

            # Cap concurrent positions from signals
            signal_positions = sum(1 for m in self.tracker.positions.keys() if m in self._opened_signals)
            if signal_positions >= 3:
                logging.info(f"🛑 Max concurrent signal positions (3) reached, stopping")
                break

            # Skip if already have position in this market
            if mid in self.tracker.positions:
                logging.debug(f"⏭️ {symbol}: already have position, skipping")
                continue

            # Skip if AI recently closed this symbol (cooldown)
            cooldown_until = self._ai_close_cooldown.get(symbol)
            if cooldown_until and time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                logging.info(f"🧊 {symbol}: AI close cooldown ({remaining}s remaining) - SKIPPING")
                continue

            # Skip if already acted on this signal this round
            if mid in self._opened_signals:
                continue

            is_long = direction == "long"

            # Check quota cooldown and pacing BEFORE fetching price (saves API calls)
            if self._is_volume_quota_cooldown():
                logging.debug(f"⏳ {symbol}: volume quota cooldown active, skipping open")
                continue
            if self._should_pace_orders():
                logging.debug(f"⏳ {symbol}: pacing orders (low quota), skipping open")
                continue

            # Quota prioritization: skip new opens when quota < 35 to preserve for SL orders
            if self._should_skip_open_for_quota():
                logging.warning(f"🚫 {symbol}: new opens paused (quota={self.api.volume_quota_remaining} < 35, SL protection prioritized)")
                continue

            # Always fetch live price — signal data can be stale (up to 5 min old)
            current_price = None
            if self.api:
                current_price = await self.api.get_price(mid)
                await asyncio.sleep(self.cfg.price_call_delay)
            if not current_price:
                logging.warning(f"⚠️ {symbol}: no live price available, skipping")
                continue

            # Scale position size to actual balance
            size_usd = opp.get("positionSizeUsd", 0) * scale
            if size_usd <= 0:
                logging.warning(f"⚠️ {symbol}: invalid position size, skipping")
                continue

            # Open the position
            logging.info(f"📡 Signal: {direction.upper()} {symbol} score={opp['compositeScore']} size=${size_usd:.2f}")
            if self.api:
                try:
                    success = await self.api.open_position(mid, size_usd, is_long, current_price)
                except VolumeQuotaError:
                    self._start_volume_quota_cooldown()
                    continue
                if success:
                    self._mark_order_submitted()
                    self._reset_volume_quota_backoff()

                    # Fix #13: Add to pending_sync and bot_managed BEFORE verification
                    # to prevent race conditions if _tick() sync cycle runs mid-verification
                    self._pending_sync.add(mid)
                    self.bot_managed_market_ids.add(mid)

                    # Verify position exists on exchange (BUG-03 fix)
                    expected_size = size_usd / current_price
                    verified_pos = await self._verify_position_opened(mid, expected_size, symbol)
                    if verified_pos is None:
                        # CRITICAL-2: Don't discard — add as unverified so we can re-verify on next ticks
                        logging.error(f"❌ Signal open: {symbol} verification failed — tracking as unverified (will re-verify)")
                        self.tracker.add_position(mid, symbol, direction, current_price, expected_size, leverage=min(self.cfg.default_leverage, 10))
                        pos = self.tracker.positions.get(mid)
                        if pos:
                            pos.unverified_at = time.time()
                            pos.unverified_ticks = 1
                        self._save_state()
                        self._opened_signals.add(mid)
                        await self.alerts.send(
                            f"⚠️ *POSITION UNVERIFIED*\n"
                            f"{direction.upper()} {symbol}\n"
                            f"Order submitted but verification failed.\n"
                            f"Will re-verify on next ticks."
                        )
                        continue  # Skip to next signal

                    self._opened_signals.add(mid)
                    # Use actual filled size from exchange (handles partial fills)
                    actual_size = verified_pos["size"]
                    self.tracker.add_position(mid, symbol, direction, current_price, actual_size, leverage=min(self.cfg.default_leverage, 10))

                    # Update DSL state with effective leverage from exchange (L3 fix)
                    pos = self.tracker.positions.get(mid)
                    if pos and pos.dsl_state and verified_pos.get("leverage"):
                        pos.dsl_state.leverage = verified_pos["leverage"]

                    # Persist state immediately after opening to prevent crash data loss
                    self._save_state()

                    # BUG-06: Verify we can actually fetch price for this position after open
                    price_ok = False
                    for attempt in range(1, 4):
                        verify_price = await self.api.get_price_with_mark_fallback(mid)
                        if verify_price:
                            price_ok = True
                            if attempt > 1:
                                logging.info(f"✅ {symbol}: price verified on retry {attempt}/3 = ${verify_price:,.2f}")
                            break
                        if attempt < 3:
                            await asyncio.sleep(1)
                    if not price_ok:
                        logging.error(f"❌ Signal open: {symbol} — no price after 3 attempts, removing orphaned position")
                        self.tracker.remove_position(mid)
                        await self.alerts.send(
                            f"❌ *SIGNAL OPEN FAILED*\n"
                            f"{direction.upper()} {symbol}\n"
                            f"Order filled but price unavailable — position removed.\n"
                            f"Order may need manual cleanup on exchange."
                        )
                        continue

                    logging.info(f"📊 Quota remaining: {self.api.volume_quota_remaining}")
                    await self.alerts.send(
                        f"📡 *SIGNAL → OPENED*\n"
                        f"{direction.upper()} {symbol}\n"
                        f"Score: {opp['compositeScore']}\n"
                        f"Price: ${current_price:,.2f}\n"
                        f"Size: ${size_usd:.2f} (scaled {scale:.2f}×)\n"
                        f"SL dist: {opp.get('stopLossDistancePct', 0):.2f}%"
                    )

    def _validate_ai_decision(self, decision: dict) -> str | None:
        """Validate AI decision fields. Returns error string if invalid, None if OK."""
        action = decision.get("action")
        if action not in ("open", "close", "close_all", "hold"):
            return f"Invalid action: {action!r}"

        symbol = decision.get("symbol")
        if action in ("open", "close"):
            if not symbol or not isinstance(symbol, str) or not symbol.strip():
                return f"Missing or invalid symbol: {symbol!r}"

        if action == "open":
            # IPC-03: use requested_size_usd (new) with fallback to size_usd (legacy)
            size_usd = decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)
            if not isinstance(size_usd, (int, float)) or size_usd <= 0:
                return f"Invalid size_usd: {size_usd!r}"
            direction = decision.get("direction")
            if direction not in ("long", "short"):
                return f"Invalid direction: {direction!r}"
            confidence = decision.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                    return f"Invalid confidence: {confidence!r} (expected 0.0-1.0)"

        if action == "close":
            confidence = decision.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                    return f"Invalid confidence: {confidence!r} (expected 0.0-1.0)"

        return None

    async def _process_ai_decision(self):
        """Read AI decision file and execute if valid."""
        path = Path(self._ai_decision_file)
        if not path.exists():
            return

        decision = safe_read_json(path)
        if decision is None:
            # MED-5: File exists but read returned None (atomic write in progress).
            # Retry once after 0.5s to avoid dropping decisions on first tick post-restart.
            if path.exists():
                await asyncio.sleep(0.5)
                decision = safe_read_json(path)
            if decision is None:
                return

        # Only process new decisions
        ts = decision.get("timestamp", "")
        if ts == self._last_ai_decision_ts:
            return
        self._last_ai_decision_ts = ts
        self._signal_processed_this_tick = True

        # HIGH-7: Reject stale AI decisions (>10 minutes old)
        try:
            decision_time = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        except (ValueError, AttributeError):
            decision_time = None
        if decision_time:
            age_seconds = (datetime.now(timezone.utc) - decision_time).total_seconds()
            if age_seconds > 600:
                logging.warning(f"⚠️ AI decision rejected: stale (age={age_seconds:.0f}s, max=600s)")
                self._write_ai_result(decision, success=False)
                return

        # Validate decision
        validation_error = self._validate_ai_decision(decision)
        if validation_error:
            logging.warning(f"⚠️ AI decision rejected: {validation_error}")
            self._write_ai_result(decision, success=False)
            return

        action = decision.get("action")
        if action not in ("open", "close", "close_all"):
            return  # hold or unknown — do nothing

        # BUG 2: Check prev_decision_id for gap detection
        prev_id = decision.get("prev_decision_id")
        if prev_id:
            ack_path = str(path) + ".ack"
            try:
                acked_id = Path(ack_path).read_text().strip() if Path(ack_path).exists() else ""
            except Exception:
                acked_id = ""
            if acked_id and prev_id != acked_id:
                logging.warning(
                    f"⚠️ AI decision prev_decision_id={prev_id} doesn't match last ACKed={acked_id} "
                    f"— potential decision gap, processing anyway"
                )

        # BUG 3: Check if we already processed this decision (duplicate execution guard)
        decision_id = decision.get("decision_id", "")
        result_path = Path(self._ai_result_file)
        if result_path.exists() and decision_id:
            existing_result = safe_read_json(result_path)
            if existing_result and existing_result.get("processed_decision_id") == decision_id:
                logging.info(f"⏩ Decision {decision_id} already processed (result file exists), skipping execution")
                return

        # Execute inside try/except — ACK + result are written AFTER execution
        # completes (success or failure), NOT if an uncaught exception occurs.
        try:
            if action == "close_all":
                success = await self._execute_ai_close_all(decision)
            elif action == "open":
                success = await self._execute_ai_open(decision)
            elif action == "close":
                success = await self._execute_ai_close(decision)
            else:
                success = True

            # HIGH-3: Write ACK BEFORE result — ACK = "I consumed this decision."
            # If bot crashes between ACK and result write, AI trader won't re-send.
            # Result is supplementary (positions context). ACK is essential.
            ack_path = str(path) + ".ack"
            with open(ack_path, "w") as f:
                f.write(decision.get("decision_id", ""))
            self._write_ai_result(decision, success=success)
            # BUG 3: Save state immediately after ACK so _last_ai_decision_ts persists before ACK
            self._save_state()
        except Exception as e:
            logging.error(f"❌ AI decision execution crashed — NOT writing ACK: {e}", exc_info=True)
            # Do NOT write result or ACK — the AI trader will re-deliver the decision

    async def _execute_ai_open(self, decision: dict) -> bool:
        """Execute an AI-recommended open. Returns True on success."""
        if self._kill_switch_active:
            logging.warning(f"🚫 Kill switch active — AI open blocked for {decision.get('symbol', '?')}")
            return False

        symbol = decision.get("symbol")
        direction = decision.get("direction")
        # IPC-03: use requested_size_usd (new) with fallback to size_usd (legacy)
        size_usd = decision.get("requested_size_usd", 0) or decision.get("size_usd", 0)

        if not symbol or not direction or size_usd <= 0:
            logging.warning(f"AI open: invalid decision fields")
            return False

        # Cap size_usd at 10x equity
        balance = await self._get_balance()
        if balance > 0:
            max_size = balance * 10
            if size_usd > max_size:
                logging.warning(f"⚠️ AI open: size_usd=${size_usd:.2f} capped to ${max_size:.2f} (10x equity)")
                size_usd = max_size

        # Resolve market ID
        market_id = self._resolve_market_id(symbol)
        if market_id is None:
            logging.warning(f"AI open: unknown symbol {symbol}")
            return False

        # Check AI close cooldown — prevent reopening recently closed symbols
        cooldown_until = self._ai_close_cooldown.get(symbol)
        if cooldown_until and time.monotonic() < cooldown_until:
            remaining = int(cooldown_until - time.monotonic())
            logging.info(f"🧊 AI open: {symbol} in close cooldown ({remaining}s remaining) - SKIPPING")
            return False

        if market_id in self.tracker.positions:
            logging.info(f"AI open: already in {symbol}, skipping")
            return False

        # Cap at 3 concurrent positions
        if len(self.tracker.positions) >= 3:
            logging.info(f"AI open: max positions reached, skipping {symbol}")
            return False

        # Check quota cooldown and pacing
        if self._is_volume_quota_cooldown():
            logging.info(f"⏳ AI open: {symbol} in volume quota cooldown — skipping")
            return False
        if self._should_pace_orders():
            logging.info(f"⏱️ AI open: {symbol} pacing orders (low quota) — skipping")
            return False

        # Quota prioritization: skip new opens when quota < 35 to preserve for SL orders
        if self._should_skip_open_for_quota():
            quota = self.api.volume_quota_remaining if self.api else None
            logging.warning(f"🚫 {symbol}: new opens paused (quota={quota} < 35, SL protection prioritized)")
            return False

        is_long = direction == "long"
        current_price = await self.api.get_price(market_id)
        if not current_price:
            logging.warning(f"AI open: no price for {symbol}")
            return False

        try:
            success = await self.api.open_position(market_id, size_usd, is_long, current_price)
        except VolumeQuotaError:
            self._start_volume_quota_cooldown()
            return False
        if success:
            self._mark_order_submitted()
            self._reset_volume_quota_backoff()

            # Fix #13: Add to pending_sync and bot_managed BEFORE verification
            # to prevent race conditions if _tick() sync cycle runs mid-verification
            self._pending_sync.add(market_id)
            self.bot_managed_market_ids.add(market_id)

            # Verify position exists on exchange (BUG-03 fix)
            expected_size = size_usd / current_price
            verified_pos = await self._verify_position_opened(market_id, expected_size, symbol)
            if verified_pos is None:
                # CRITICAL-2: Don't discard — add as unverified so we can re-verify on next ticks
                # Position may be on exchange but API is slow to reflect it
                logging.error(f"❌ AI open: {symbol} verification failed — tracking as unverified (will re-verify)")
                ai_sl_pct = decision.get("stop_loss_pct")
                ai_leverage = min(float(decision.get("leverage", self.cfg.default_leverage)), 10)
                self.tracker.add_position(market_id, symbol, direction, current_price, expected_size, leverage=ai_leverage, sl_pct=ai_sl_pct)
                pos = self.tracker.positions.get(market_id)
                if pos:
                    pos.unverified_at = time.time()
                    pos.unverified_ticks = 1
                self._save_state()
                await self.alerts.send(
                    f"⚠️ *POSITION UNVERIFIED*\n"
                    f"{direction.upper()} {symbol}\n"
                    f"Order submitted but verification failed.\n"
                    f"Will re-verify on next ticks."
                )
                return True  # Order was submitted, we're tracking it as unverified
            # Use actual filled size from exchange (handles partial fills)
            actual_size = verified_pos["size"]
            ai_sl_pct = decision.get("stop_loss_pct")
            ai_leverage = min(float(decision.get("leverage", self.cfg.default_leverage)), 10)
            self.tracker.add_position(market_id, symbol, direction, current_price, actual_size, leverage=ai_leverage, sl_pct=ai_sl_pct)

            # Update DSL state with effective leverage from exchange (L3 fix)
            pos = self.tracker.positions.get(market_id)
            if pos and pos.dsl_state and verified_pos.get("leverage"):
                pos.dsl_state.leverage = verified_pos["leverage"]

            # Persist state immediately after opening to prevent crash data loss
            self._save_state()

            # BUG-06: Verify we can actually fetch price for this position after open
            # If price is unavailable, the position becomes "orphaned" — DSL can't compute ROE
            price_ok = False
            for attempt in range(1, 4):
                verify_price = await self.api.get_price_with_mark_fallback(market_id)
                if verify_price:
                    price_ok = True
                    if attempt > 1:
                        logging.info(f"✅ {symbol}: price verified on retry {attempt}/3 = ${verify_price:,.2f}")
                    break
                if attempt < 3:
                    await asyncio.sleep(1)
            if not price_ok:
                logging.error(f"❌ AI open: {symbol} — no price after 3 attempts, removing orphaned position")
                self.tracker.remove_position(market_id)
                self._pending_sync.discard(market_id)
                await self.alerts.send(
                    f"❌ *AI OPEN FAILED*\n"
                    f"{direction.upper()} {symbol}\n"
                    f"Order filled but price unavailable — position removed.\n"
                    f"Order may need manual cleanup on exchange."
                )
                return False

            logging.info(f"📊 Quota remaining: {self.api.volume_quota_remaining}")
            # Reset close attempt tracking for this symbol
            self._close_attempts.pop(symbol, None)
            self._close_attempt_cooldown.pop(symbol, None)
            await self.alerts.send(
                f"🤖 *AI → OPENED*\n"
                f"{direction.upper()} {symbol}\n"
                f"Size: ${size_usd:.2f}\n"
                f"Reason: {decision.get('reasoning', '?')[:200]}"
            )
            logging.info(f"AI opened: {direction} {symbol} ${size_usd:.2f}")
        return success

    async def _check_active_orders(self, market_id: int) -> list[dict]:
        """Check if there are any active (unfilled) orders for this market on our account.

        Used by MED-18 cancel logic to find order_index for cancellation.
        """
        try:
            await self.api._ensure_client()
            # Generate auth token for the request
            auth = None
            try:
                await self.api._ensure_signer()
                if self.api._signer is not None:
                    if not hasattr(self, '_auth_manager'):
                        self._auth_manager = LighterAuthManager(
                            signer=self.api._signer,
                            account_index=self.cfg.account_index
                        )
                    auth = self._auth_manager.get_auth_token()
            except Exception as auth_err:
                logging.debug(f"Auth generation skipped: {auth_err}")
            orders = await self.api._order_api.account_active_orders(
                account_index=self.cfg.account_index,
                market_id=market_id,
                auth=auth,
                _request_timeout=30,
            )
            active = []
            if hasattr(orders, 'orders') and orders.orders:
                for o in orders.orders:
                    active.append({
                        'order_index': getattr(o, 'order_index', None),
                        'price': getattr(o, 'price', None),
                        'base_amount': getattr(o, 'base_amount', None),
                        'is_ask': getattr(o, 'is_ask', None),
                        'order_type': getattr(o, 'order_type', None),
                        'status': getattr(o, 'status', None),
                    })
            return active
        except Exception as e:
            logging.warning(f"⚠️ Could not check active orders for market {market_id}: {e}")
            return []

    async def _verify_position_closed(self, market_id: int, symbol: str) -> bool:
        """Poll the Lighter API to verify a position is actually closed after a close order.
        Uses progressively longer delays to account for exchange processing time.
        MED-25: Adds market_id to _verifying_close to skip DSL/SL evaluation during verification.
        """
        self._verifying_close.add(market_id)
        try:
            delays = [3, 5, 7, 10]  # MED-25: reduced from [5,10,15,20] — 25s total instead of 50s
            for attempt, delay in enumerate(delays):
                await asyncio.sleep(delay)
                try:
                    live_positions = await self.api.get_positions()
                    still_open = any(p["market_id"] == market_id and abs(p.get("size", 0)) > 0.001 for p in live_positions)
                    if not still_open:
                        logging.info(f"✅ {symbol}: position closure verified (attempt {attempt + 1}, after {delay}s)")
                        return True
                    # Also check if there are any active orders (the close order might still be pending)
                    active_orders = await self._check_active_orders(market_id)
                    sl_orders = [o for o in active_orders]  # all active orders are close-related (both long close and short close)
                    logging.info(
                        f"⏳ {symbol}: position still open (attempt {attempt + 1}/{len(delays)}), "
                        f"active_orders={len(active_orders)}, sl_orders={len(sl_orders)}"
                    )
                except Exception as e:
                    logging.warning(f"⚠️ {symbol}: error verifying closure (attempt {attempt + 1}): {e}")
            return False
        finally:
            self._verifying_close.discard(market_id)

    async def _verify_position_opened(self, market_id: int, expected_size: float, symbol: str) -> dict | None:
        """Verify a position exists on the exchange after open_order returns success.

        Retries up to 3 times with 1-second delays (max ~3s total).
        Returns position dict with actual filled size on success, None on failure.

        Handles:
        - Exchange rejecting the order after SDK returned success (phantom positions)
        - Partial fills (actual size < requested size)
        - EDGE-03: get_positions() returning None on network failure
        """
        for attempt in range(1, 4):
            await asyncio.sleep(1)
            try:
                live_positions = await self.api.get_positions()
                # Handle EDGE-03: get_positions() returns None on failure
                if live_positions is None:
                    logging.warning(f"⚠️ {symbol}: get_positions() returned None during open verification (attempt {attempt}/3)")
                    continue
                for p in live_positions:
                    if p["market_id"] == market_id and abs(p.get("size", 0)) > 0.001:
                        actual_size = p["size"]
                        # Fix #14: Reject severely underfilled positions (< 20%)
                        # Bot DSL logic is designed for full-size — don't manage tiny positions
                        fill_ratio = actual_size / expected_size if expected_size > 0 else 0
                        if fill_ratio < 0.3:
                            logging.error(
                                f"❌ {symbol}: position severely underfilled, REJECTING "
                                f"(actual={actual_size:.6f}, expected={expected_size:.6f}, fill={fill_ratio:.1%})"
                            )
                            return None  # Abort — don't manage severely underfilled positions
                        elif fill_ratio < 0.9:
                            logging.info(
                                f"📊 {symbol}: partial fill detected "
                                f"(actual={actual_size:.6f}, expected={expected_size:.6f}, fill={fill_ratio:.1%})"
                            )
                        else:
                            logging.info(
                                f"✅ {symbol}: position verified on exchange "
                                f"(size={actual_size:.6f}, attempt {attempt})"
                            )
                        return p
                logging.info(f"⏳ {symbol}: position not yet visible on exchange (attempt {attempt}/3)")
            except Exception as e:
                logging.warning(f"⚠️ {symbol}: error during open verification (attempt {attempt}/3): {e}")
        logging.error(f"❌ {symbol}: position NOT found on exchange after 3 verification attempts — order may have been rejected")
        return None

    async def _get_fill_price(self, market_id: int, client_order_index: str | None) -> float | None:
        """Query Lighter API for actual fill price of a closed order."""
        if not client_order_index:
            return None
        try:
            if not hasattr(self, '_auth_manager'):
                self._auth_manager = LighterAuthManager(
                    signer=self.api._signer,
                    account_index=self.cfg.account_index
                )
            auth = self._auth_manager.get_auth_token()
            base = self.cfg.lighter_url.rstrip('/')
            url = f'{base}/api/v1/accountInactiveOrders?account_index={self.cfg.account_index}&limit=100&auth={auth}'
            # Reuse or create a session for fill price queries
            if not hasattr(self, '_http_session') or self._http_session.closed:
                self._http_session = aiohttp.ClientSession()
            async with self._http_session.get(url) as resp:
                data = await resp.json()
                if data.get("code") == 200 and "orders" in data:
                    for o in data["orders"]:
                        coi = str(o.get("client_order_index", ""))
                        if coi == str(client_order_index):
                            filled_base = float(o.get("filled_base_amount", 0))
                            filled_quote = float(o.get("filled_quote_amount", 0))
                            if filled_base > 0:
                                return filled_quote / filled_base
            return None
        except Exception as e:
            logging.debug(f"Could not fetch fill price: {e}")
            return None

    async def _execute_ai_close(self, decision: dict) -> bool:
        """Execute an AI-recommended close. Returns True on success (position actually closed)."""
        symbol = decision.get("symbol")
        if not symbol:
            return False

        # Check close attempt cooldown — if we've failed too many times, skip
        cooldown_until = self._close_attempt_cooldown.get(symbol)
        if cooldown_until and time.monotonic() < cooldown_until:
            remaining = int(cooldown_until - time.monotonic())
            logging.info(f"🧊 AI close: {symbol} in close attempt cooldown ({remaining}s remaining) — skipping. Position may need manual intervention.")
            return False

        # Find position by symbol
        mid_to_close = None
        for mid, pos in self.tracker.positions.items():
            if pos.symbol == symbol:
                mid_to_close = mid
                break

        if mid_to_close is None:
            logging.info(f"AI close: no position in {symbol}")
            # Reset attempt counter if position is gone
            self._close_attempts.pop(symbol, None)
            return False

        pos = self.tracker.positions[mid_to_close]
        is_long = pos.side == "long"
        current_price = await self.api.get_price(mid_to_close)
        if not current_price:
            return False

        try:
            # MED-18: Cancel stale SL order before placing new one
            if pos.active_sl_order_id:
                logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before AI close")
                await self.api._cancel_order(mid_to_close, int(pos.active_sl_order_id))
                pos.active_sl_order_id = None
            sl_success, sl_coi = await self.api.execute_sl(mid_to_close, pos.size, current_price, is_long)
            if sl_success and sl_coi:
                pos.active_sl_order_id = sl_coi
        except VolumeQuotaError:
            self._start_volume_quota_cooldown()
            # Track attempts with graduated delay
            attempts = self._close_attempts.get(symbol, 0) + 1
            self._close_attempts[symbol] = attempts
            delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
            retry_delay = self._sl_retry_delays[delay_idx]
            self._close_attempt_cooldown[symbol] = time.monotonic() + retry_delay
            logging.warning(f"⚠️ AI close: {symbol} volume quota exhausted (attempt {attempts}, retry in {retry_delay}s)")
            return False
        if not sl_success:
            # Track attempts with graduated delay
            attempts = self._close_attempts.get(symbol, 0) + 1
            self._close_attempts[symbol] = attempts
            delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
            retry_delay = self._sl_retry_delays[delay_idx]
            self._close_attempt_cooldown[symbol] = time.monotonic() + retry_delay
            logging.warning(f"⚠️ Failed to submit close order for {pos.side} {symbol} (attempt {attempts}, retry in {retry_delay}s)")
            return False

        # CRITICAL-4: Don't log outcome yet — log ONCE after verification
        # Now verify it actually filled by polling the API
        position_closed = await self._verify_position_closed(mid_to_close, symbol)

        if not position_closed:
            # Increment attempt counter
            attempts = self._close_attempts.get(symbol, 0) + 1
            self._close_attempts[symbol] = attempts
            logging.warning(f"⚠️ {symbol}: close order submitted but position still open (attempt {attempts}/{self._max_close_attempts})")

            if attempts >= self._max_close_attempts:
                # Escalate: set cooldown and alert
                self._close_attempt_cooldown[symbol] = time.monotonic() + self._close_cooldown_seconds
                # CRITICAL-4: Log with estimated price as fallback after all retries exhausted
                self._log_outcome(pos, current_price, "ai_close", estimated=True)
                roe = ((current_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                    else ((pos.entry_price - current_price) / pos.entry_price * 100)
                await self.alerts.send(
                    f"🚨 *CLOSE FAILED ×{attempts}*\n"
                    f"{pos.side.upper()} {symbol}\n"
                    f"ROE: {roe:+.1f}%\n"
                    f"Order submitted but NOT filled after {attempts} attempts.\n"
                    f"Cooldown: {self._close_cooldown_seconds // 60}min — may need manual intervention."
                )
                logging.error(f"🚨 {symbol}: max close attempts ({self._max_close_attempts}) reached. Setting {self._close_cooldown_seconds}s cooldown.")
            return False

        # Position successfully closed — reset attempt counter
        self._close_attempts.pop(symbol, None)
        self._close_attempt_cooldown.pop(symbol, None)

        fill_price = await self._get_fill_price(mid_to_close, sl_coi)
        exit_price = fill_price if fill_price else current_price
        # CRITICAL-4: Log outcome ONCE with actual fill price after verification
        self._log_outcome(pos, exit_price, "ai_close")
        self._recently_closed[mid_to_close] = time.monotonic() + 300  # 5 min phantom guard
        pos.active_sl_order_id = None  # MED-18
        self.bot_managed_market_ids.discard(mid_to_close)
        self.tracker.remove_position(mid_to_close)

        roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
            else ((pos.entry_price - exit_price) / pos.entry_price * 100)

        await self.alerts.send(
            f"🤖 *AI → CLOSED*\n"
            f"{pos.side.upper()} {symbol}\n"
            f"ROE: {roe:+.1f}%\n"
            f"Reason: {decision.get('reasoning', '?')[:200]}"
        )
        logging.info(f"AI closed: {pos.side} {symbol} ROE={roe:+.1f}%")

        # Set cooldown — prevent re-opening this symbol for N minutes
        self._ai_close_cooldown[symbol] = time.monotonic() + self._ai_cooldown_seconds
        logging.info(f"🧊 {symbol}: AI close cooldown set ({self._ai_cooldown_seconds}s)")
        return True

    async def _execute_ai_close_all(self, decision: dict) -> bool:
        """Emergency close all positions — with verification."""
        reasoning = decision.get("reasoning", "Emergency halt")
        logging.warning(f"🚨 AI close_all triggered: {reasoning}")
        await self.alerts.send(
            f"🚨 *AI → CLOSE ALL*\n"
            f"Reason: {reasoning[:200]}"
        )

        failed_positions = []

        for i, (mid, pos) in enumerate(list(self.tracker.positions.items())):
            is_long = pos.side == "long"
            current_price = await self.api.get_price(mid) if self.api else None
            if i < len(self.tracker.positions) - 1:
                await asyncio.sleep(self.cfg.price_call_delay)

            if not current_price:
                logging.warning(f"⚠️ No price for {pos.symbol} — skipping close, keeping in tracker")
                failed_positions.append(pos.symbol)
                continue

            try:
                # MED-18: Cancel stale SL order before placing new one
                if pos.active_sl_order_id:
                    logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before close_all")
                    await self.api._cancel_order(mid, int(pos.active_sl_order_id))
                    pos.active_sl_order_id = None
                sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, current_price, is_long)
                if sl_success and sl_coi:
                    pos.active_sl_order_id = sl_coi
            except VolumeQuotaError:
                self._start_volume_quota_cooldown()
                logging.warning(f"⚠️ AI close_all: volume quota exhausted for {pos.symbol} — cooldown started")
                failed_positions.append(pos.symbol)
                continue

            if not sl_success:
                logging.warning(f"⚠️ Failed to submit close order for {pos.side} {pos.symbol}")
                failed_positions.append(pos.symbol)
                continue

            # Order submitted — verify it actually filled
            position_closed = await self._verify_position_closed(mid, pos.symbol)

            if position_closed:
                # Get actual fill price for accurate outcome logging
                fill_price = await self._get_fill_price(mid, sl_coi)
                exit_price = fill_price if fill_price else current_price
                # HIGH-6: Log outcome to DB for close_all positions
                self._log_outcome(pos, exit_price, "ai_close_all")
                roe = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                    else ((pos.entry_price - exit_price) / pos.entry_price * 100)
                logging.info(f"Emergency closed: {pos.side} {pos.symbol} ROE={roe:+.1f}%")
                self._recently_closed[mid] = time.monotonic() + 300
                pos.active_sl_order_id = None  # MED-18
                self.bot_managed_market_ids.discard(mid)
                self.tracker.remove_position(mid)
                await self.alerts.send(
                    f"✅ *CLOSE ALL → {pos.side.upper()} {pos.symbol}* closed"
                )
            else:
                logging.warning(f"⚠️ {pos.symbol}: close order submitted but position still open after verification")
                failed_positions.append(pos.symbol)
                await self.alerts.send(
                    f"⚠️ *CLOSE ALL → {pos.symbol}* verification failed — position may still be open"
                )

        return len(failed_positions) == 0

    def _resolve_market_id(self, symbol: str) -> int | None:
        """Resolve symbol to market_id. Tries scanner signals first, then cached positions."""
        # Try from signals file
        signals_path = Path(self._signals_file)
        if signals_path.exists():
            # MED-23: Check staleness before using signals for market ID resolution.
            # Stale signals may have wrong market IDs (scanner could have reassigned IDs).
            try:
                signals_age = time.time() - signals_path.stat().st_mtime
                if signals_age > 600:  # 10 minutes
                    logging.warning(f"⚠️ _resolve_market_id: signals.json stale (age={signals_age:.0f}s), skipping signal-based resolution for {symbol}")
                    signals_path = None  # Skip to position tracker fallback
            except OSError:
                pass

            if signals_path and signals_path.exists():
                data = safe_read_json(signals_path)
                if data:
                    for opp in data.get("opportunities", []):
                        if opp.get("symbol") == symbol:
                            return opp.get("marketId")

        # Try from position tracker (already-open positions)
        for mid, pos in self.tracker.positions.items():
            if pos.symbol == symbol:
                return mid

        return None

    def _log_outcome(self, pos: TrackedPosition, exit_price: float, exit_reason: str,
                     estimated: bool = False):
        """Log a closed trade outcome to the AI trader journal DB.

        Called ONCE per close — either with actual fill price after verification
        succeeds, or with estimated=True as fallback after max verification retries.
        This ensures only one outcome row per close (no double-writing).

        PnL math (no double-counting of leverage):
          pnl_pct  = raw price movement % (not leveraged)
          size_usd = notional position value at entry (size × entry_price)
          pnl_usd  = actual dollar P&L = notional × pnl_pct / 100
                     (this IS the real dollar gain/loss, leverage doesn't change it —
                      1 BTC moved $100 is $100 whether you used 1x or 10x margin)
          roe_pct  = Return on Equity % = pnl_pct × leverage
                     (what you earned relative to your margin deposit)
        """
        if _db is None:
            return
        try:
            is_long = pos.side == "long"
            # Raw price movement percentage (NOT leveraged)
            pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                else ((pos.entry_price - exit_price) / pos.entry_price * 100)
            # Notional position value at entry
            size_usd = pos.size * pos.entry_price
            # Dollar P&L = notional × raw price change % (leverage doesn't affect dollar P&L)
            pnl_usd = size_usd * pnl_pct / 100
            hold_seconds = int((datetime.now(timezone.utc) - pos.opened_at).total_seconds())
            # ROE = return relative to margin (not notional) = pnl_pct × leverage
            # Use actual position leverage, not config default
            actual_leverage = pos.dsl_state.leverage if pos.dsl_state else self.cfg.default_leverage
            roe_pct = pnl_pct * actual_leverage

            # Mark as estimated if we haven't verified the fill yet
            reason_tag = f"{exit_reason} (estimated)" if estimated else exit_reason
            _db.log_outcome({
                "symbol": pos.symbol,
                "direction": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "size_usd": size_usd,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "roe_pct": roe_pct,
                "hold_time_seconds": hold_seconds,
                "max_drawdown_pct": 0,  # not tracked yet
                "exit_reason": reason_tag,
                "decision_snapshot": {},
            })
            tag = " (est)" if estimated else ""
            logging.info(
                f"📝 Outcome logged{tag}: {pos.side} {pos.symbol} "
                f"PnL=${pnl_usd:+.2f} ({roe_pct:+.1f}% ROE @ {actual_leverage:.1f}x) "
                f"held={hold_seconds}s reason={exit_reason}"
            )
        except Exception as e:
            logging.warning(f"Failed to log outcome: {e}")

    def _update_outcome(self, pos: TrackedPosition, exit_price: float, exit_reason: str):
        """Update the most recent outcome for this symbol with actual fill price.

        Called after verification confirms the actual fill. If this fails or the
        bot crashes, the estimated outcome already exists in the DB.
        """
        if _db is None:
            return
        try:
            is_long = pos.side == "long"
            pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                else ((pos.entry_price - exit_price) / pos.entry_price * 100)
            size_usd = pos.size * pos.entry_price
            pnl_usd = size_usd * pnl_pct / 100
            # Use actual position leverage, not config default
            actual_leverage = pos.dsl_state.leverage if pos.dsl_state else self.cfg.default_leverage
            roe_pct = pnl_pct * actual_leverage

            updated = _db.update_latest_outcome(
                pos.symbol, exit_price, pnl_usd, pnl_pct, roe_pct, exit_reason
            )
            if updated:
                logging.info(
                    f"📝 Outcome updated: {pos.side} {pos.symbol} "
                    f"PnL=${pnl_usd:+.2f} ({roe_pct:+.1f}% ROE @ {actual_leverage:.1f}x) reason={exit_reason}"
                )
            else:
                logging.warning(f"Outcome update: no matching record for {pos.symbol}")
        except Exception as e:
            logging.warning(f"Failed to update outcome: {e}")

    def _write_ai_result(self, decision: dict, success: bool):
        """Write execution result for the AI trader to read."""
        try:
            positions = []
            for mid, pos in self.tracker.positions.items():
                current_price = self.api.get_mark_price(mid) if self.api else None
                positions.append({
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "current_price": current_price if current_price and current_price > 0 else pos.entry_price,
                    "size": pos.size,
                    "position_size_usd": pos.size * pos.entry_price,
                })
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "processed_decision_id": decision.get("decision_id"),
                "processed_timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_action": decision.get("action"),
                "decision_symbol": decision.get("symbol"),
                "success": success,
                "positions": positions,
            }
            # Atomic write (same pattern as ai-trader)
            tmp = str(self._ai_result_file) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(result, f, indent=2)
            os.replace(tmp, str(self._ai_result_file))
            # HIGH-10: Mark result as fresh — refresh should not overwrite until next tick
            self._result_dirty = True
        except Exception as e:
            logging.warning(f"Failed to write AI result: {e}")

    def _refresh_position_context(self):
        """MED-4: Write updated positions to result file between AI decisions.

        Preserves the last processed_decision_id so AI trader can still correlate,
        but updates positions to reflect DSL/SL/TP closes that happened since
        the last AI decision was processed.
        """
        # HIGH-10: Skip if a fresh AI result was just written — don't overwrite
        # the AI trader's result before it has a chance to read it.
        if self._result_dirty:
            logging.debug("Refresh skipped: result is dirty (fresh AI result not yet consumed)")
            return
        try:
            existing = safe_read_json(Path(self._ai_result_file))
            last_decision_id = existing.get("processed_decision_id") if existing else None

            positions = []
            for mid, pos in self.tracker.positions.items():
                current_price = self.api.get_mark_price(mid) if self.api else None
                positions.append({
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "current_price": current_price if current_price and current_price > 0 else pos.entry_price,
                    "size": pos.size,
                    "position_size_usd": pos.size * pos.entry_price,
                })
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "processed_decision_id": last_decision_id,  # Keep last AI decision ID for correlation
                "processed_timestamp": existing.get("processed_timestamp") if existing else datetime.now(timezone.utc).isoformat(),
                "decision_action": existing.get("decision_action") if existing else "refresh",
                "decision_symbol": existing.get("decision_symbol") if existing else None,
                "success": existing.get("success", True) if existing else True,
                "positions": positions,
            }
            tmp = str(self._ai_result_file) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(result, f, indent=2)
            os.replace(tmp, str(self._ai_result_file))
        except Exception as e:
            logging.debug(f"Failed to refresh position context: {e}")

    def _prune_caches(self):
        """Remove expired entries from in-memory caches to prevent unbounded growth."""
        now = time.monotonic()
        # Prune AI close cooldown (entries older than cooldown period)
        expired_cooldowns = [s for s, t in self._ai_close_cooldown.items() if t < now]
        for s in expired_cooldowns:
            del self._ai_close_cooldown[s]
        # Prune API lag warnings (entries older than 1 hour)
        expired_warnings = [s for s, t in self._api_lag_warnings.items() if t < now - 3600]
        for s in expired_warnings:
            del self._api_lag_warnings[s]
        # Prune recently closed positions (entries older than TTL)
        expired_closed = [m for m, t in self._recently_closed.items() if t < now]
        for m in expired_closed:
            del self._recently_closed[m]
        # Prune close attempt cooldowns (entries older than cooldown period)
        expired_close_cd = [s for s, t in self._close_attempt_cooldown.items() if t < now]
        for s in expired_close_cd:
            del self._close_attempt_cooldown[s]
        # Fix #15: Prune _close_attempts for symbols whose cooldown has expired
        # (counter is only useful while cooldown is active)
        for s in list(self._close_attempts.keys()):
            if s not in self._close_attempt_cooldown:
                del self._close_attempts[s]
        # Prune DSL close attempt cooldowns
        expired_dsl_close_cd = [s for s, t in self._dsl_close_attempt_cooldown.items() if t < now]
        for s in expired_dsl_close_cd:
            del self._dsl_close_attempt_cooldown[s]
        # Prune symbol cache (entries older than TTL)
        if self.api:
            expired_symbols = [mid for mid, (ts, _) in self.api._symbol_cache.items() if (now - ts) > self.api._symbol_cache_ttl]
            for mid in expired_symbols:
                del self.api._symbol_cache[mid]
        # Prune no-price ticks for positions no longer tracked
        orphaned_mids = [m for m in self._no_price_ticks if m not in self.tracker.positions]
        for m in orphaned_mids:
            del self._no_price_ticks[m]
        # MED-7: Remove bot_managed_market_ids where market is no longer tracked
        # and not in recently closed (manual closes remove from tracker but didn't clean this set)
        stale_managed = [m for m in self.bot_managed_market_ids
                         if m not in self.tracker.positions and m not in self._recently_closed]
        for m in stale_managed:
            self.bot_managed_market_ids.discard(m)
        if stale_managed:
            logging.debug(f"🧹 Pruned {len(stale_managed)} stale bot_managed_market_ids")

    def _is_volume_quota_cooldown(self) -> bool:
        """Check if bot is in volume quota cooldown."""
        return time.time() < self._volume_quota_cooldown_until

    def _start_volume_quota_cooldown(self):
        """Start quota cooldown with exponential backoff."""
        self._volume_quota_cooldown_until = time.time() + self._volume_quota_backoff_seconds
        logging.warning(f"🔄 Volume quota cooldown: {self._volume_quota_backoff_seconds}s (until {time.strftime('%H:%M:%S', time.localtime(self._volume_quota_cooldown_until))})")
        # Exponential backoff: 60s → 120s → 300s (capped)
        self._volume_quota_backoff_seconds = min(self._volume_quota_backoff_seconds * 2, self._volume_quota_max_backoff)

    def _reset_volume_quota_backoff(self):
        """Reset backoff on successful order."""
        if self._volume_quota_backoff_seconds > 60:
            logging.info("✅ Volume quota recovered, resetting backoff")
        self._volume_quota_backoff_seconds = 60
        self._volume_quota_cooldown_until = 0

    def _should_pace_orders(self) -> bool:
        """Pace orders to leverage 15-second free tx window when quota is low."""
        if self.api and self.api.volume_quota_remaining is not None and self.api.volume_quota_remaining < 35:
            time_since_last = time.time() - self._last_order_time
            if time_since_last < 16:  # 16s to be safe
                logging.debug(f"⏱️ Pacing orders (quota={self.api.volume_quota_remaining}, last_order={time_since_last:.1f}s ago)")
                return True
        return False

    def _should_skip_open_for_quota(self) -> bool:
        """Skip new opens when quota is low to preserve it for SL orders."""
        quota = self.api.volume_quota_remaining if self.api else None
        if quota is not None and quota < 35:
            logging.debug(f"🚫 Skipping new opens (quota={quota} < 35, preserving for SL)")
            return True
        return False

    def _is_quota_emergency(self) -> bool:
        """Emergency mode when quota is critically low."""
        quota = self.api.volume_quota_remaining if self.api else None
        return quota is not None and quota < 5

    def _should_skip_non_critical_orders(self) -> bool:
        """In emergency mode, only allow SL orders."""
        if self._is_quota_emergency():
            # Rate-limit warning to once per minute
            now = time.monotonic()
            if now - getattr(self, '_last_quota_emergency_warn', 0) > 60:
                self._last_quota_emergency_warn = now
                quota = self.api.volume_quota_remaining if self.api else None
                logging.warning(f"🚨 Quota emergency mode (remaining={quota}), SL only")
            return True
        return False

    def _mark_order_submitted(self):
        """Mark timestamp when order was submitted."""
        self._last_order_time = time.time()

    async def _tick(self):
        """One cycle: sync positions, update prices, check triggers."""
        # HIGH-13: _pending_sync.clear() moved to AFTER position verification section
        # (see section 1.4 below) to prevent race where previous tick's verification
        # is still sleeping when sync re-detects the position as "new".

        # ── Kill switch check ──
        kill_switch_now = self._kill_switch_path.exists()
        if kill_switch_now and not self._kill_switch_active:
            self._kill_switch_active = True
            logging.critical("🚨 KILL SWITCH ACTIVE — no new positions")
            await self.alerts.send("🚨 *KILL SWITCH ACTIVE*\nNo new positions will be opened.\nExisting positions still managed.")
        elif not kill_switch_now and self._kill_switch_active:
            self._kill_switch_active = False
            logging.info("✅ Kill switch deactivated")
            await self.alerts.send("✅ *Kill switch deactivated*\nBot resumed normal operation.")

        # NOTE: Don't skip entire tick during quota cooldown —
        # position sync, DSL evaluation, and AI decisions still need to run.
        # Order submission points handle quota errors individually via VolumeQuotaError.
        quota_cooldown = self._is_volume_quota_cooldown()
        if quota_cooldown:
            logging.debug("⏳ Quota cooldown active — running tick with order submission guards")

        self._prune_caches()
        if not self.api:
            return

        # 1. Sync positions from Lighter
        live_positions = await self.api.get_positions()

        # EDGE-03: Handle API/network failure — don't clear positions on error
        if live_positions is None:
            self._position_sync_failures += 1
            if self._position_sync_failures == 1:
                logging.warning(f"⚠️ Position sync failed (attempt {self._position_sync_failures}) — keeping existing tracker state")
            else:
                logging.error(f"❌ Position sync failed ({self._position_sync_failures} consecutive) — keeping existing tracker state")
            if self._position_sync_failures >= self._position_sync_failure_threshold:
                await self.alerts.send(
                    f"🔴 *Position Sync Failed ×{self._position_sync_failures}*\n"
                    f"Cannot reach Lighter API for position sync.\n"
                    f"Tracker state preserved (positions may be stale).\n"
                    f"Check network/proxy status."
                )
            # Skip position sync entirely — DSL evaluation still runs on existing positions
            live_mids = set()
        else:
            # Reset failure counter on successful sync
            if self._position_sync_failures > 0:
                logging.info(f"✅ Position sync recovered after {self._position_sync_failures} failure(s)")
                self._position_sync_failures = 0

            live_mids = {p["market_id"] for p in live_positions}

            # Cache mark prices from unrealized_pnl (authoritative exchange price for PnL)
            self.api.update_mark_prices_from_positions(live_positions)

        # Detect new positions & closed positions — only when API succeeded
        # When live_positions is None, skip entirely to preserve tracker state (EDGE-03)
        if live_positions is not None:
            # Detect new positions (skip markets opened this tick to avoid race)
            for pos in live_positions:
                mid = pos["market_id"]
                if mid in self._pending_sync:
                    continue
                # CRITICAL-2: Adopt unverified positions that now appear in live_positions
                existing = self.tracker.positions.get(mid)
                if existing and existing.unverified_at is not None:
                    logging.info(f"✅ {pos['symbol']}: unverified position confirmed on exchange — adopting")
                    # Update with actual exchange data
                    existing.unverified_at = None
                    existing.unverified_ticks = 0
                    existing.entry_price = pos["entry_price"]
                    existing.size = pos["size"]
                    if pos.get("leverage") and existing.dsl_state:
                        existing.dsl_state.leverage = pos["leverage"]
                    continue
                if mid in self.tracker.positions:
                    continue  # already tracked
                if pos["entry_price"] <= 0:
                    continue

                symbol = pos["symbol"]

                # BUG-07: Skip positions the bot didn't open
                if not self.cfg.track_manual_positions and mid not in self.bot_managed_market_ids:
                    logging.info(f"↩️ Unmanaged position detected, skipping: {pos['side'].upper()} {symbol} (market_id={mid})")
                    continue

                # Skip if bot recently closed this position (stale API data)
                if mid in self._recently_closed:
                    logging.debug(f"⏭️ {symbol}: recently closed by bot, ignoring stale API data")
                    continue

                # Check AI close cooldown before re-tracking
                cooldown_until = self._ai_close_cooldown.get(symbol)
                if cooldown_until and time.monotonic() < cooldown_until:
                    remaining = int(cooldown_until - time.monotonic())
                    # Rate limit API lag warnings (once per minute per symbol)
                    now_mono = time.monotonic()
                    last_warned = self._api_lag_warnings.get(symbol, 0)
                    if now_mono - last_warned > 60:
                        self._api_lag_warnings[symbol] = now_mono
                        logging.warning(f"🧊 DETECTED {symbol} from Lighter API but AI close cooldown active ({remaining}s) - API lag? IGNORING")
                    continue

                # Confirm on first detection — phantom guard via _recently_closed + _ai_close_cooldown already handles false positives
                logging.info(f"📌 API POSITION DETECTED: {pos['side'].upper()} {symbol}")
                self.tracker.add_position(
                    mid, pos["symbol"], pos["side"], pos["entry_price"], pos["size"],
                    leverage=pos.get("leverage")
                )
                logging.info(f"📊 Quota remaining: {self.api.volume_quota_remaining}")
                await self.alerts.send(
                    f"📌 *New position detected*\n"
                    f"{pos['side'].upper()} {pos['symbol']} @ ${pos['entry_price']:,.2f}\n"
                    f"Size: {pos['size']}"
                )

            # Detect closed positions (skip unverified — handled separately below)
            for mid in list(self.tracker.positions.keys()):
                if mid not in live_mids:
                    pos = self.tracker.positions[mid]
                    # CRITICAL-2: Don't remove unverified positions on first absence —
                    # they may just not be visible to the API yet. Give them 3 ticks.
                    if pos.unverified_at is not None:
                        continue
                    # Try to get fill price for accurate outcome logging
                    exit_price = self.api.get_mark_price(mid) if self.api else pos.entry_price
                    if not exit_price or exit_price <= 0:
                        exit_price = pos.entry_price
                    self._log_outcome(pos, exit_price, "exchange_close")
                    logging.info(f"Position closed by exchange: {pos.symbol}")
                    self._recently_closed[mid] = time.monotonic() + 300
                    pos.active_sl_order_id = None  # MED-18
                    self.bot_managed_market_ids.discard(mid)
                    self.tracker.remove_position(mid)

            # CRITICAL-2: Handle unverified positions not in live_mids
            # Increment tick count; alert and remove after 3 consecutive absent ticks
            for mid in list(self.tracker.positions.keys()):
                pos = self.tracker.positions[mid]
                if pos.unverified_at is None:
                    continue
                if mid in live_mids:
                    # Position appeared — will be adopted in detection loop above on next tick
                    continue
                pos.unverified_ticks += 1
                logging.warning(f"⚠️ {pos.symbol}: unverified position not in live positions (tick {pos.unverified_ticks}/3)")
                if pos.unverified_ticks >= 3:
                    logging.error(f"❌ {pos.symbol}: unverified position absent for 3 ticks — removing (order likely rejected)")
                    await self.alerts.send(
                        f"❌ *UNVERIFIED POSITION REMOVED*\n"
                        f"{pos.side.upper()} {pos.symbol}\n"
                        f"Absent from exchange for 3 consecutive ticks.\n"
                        f"Order was likely rejected. Position removed from tracker."
                    )
                    pos.active_sl_order_id = None  # MED-18
                    self.bot_managed_market_ids.discard(mid)
                    self.tracker.remove_position(mid)

        # Fix #17: Quota staleness warning — alert if no quota update for 10+ minutes
        if self.api and self.api._last_known_quota is not None and self.api._last_quota_time > 0:
            quota_age = time.time() - self.api._last_quota_time
            if quota_age > 600:  # 10 minutes
                age_min = int(quota_age / 60)
                logging.warning(
                    f"⚠️ Quota tracking stale — no update for {age_min}m "
                    f"(last known: {self.api._last_known_quota} TX). "
                    f"Guards still active with last known value."
                )
                # Reset timer to avoid spamming every tick (warn once per 10 min)
                self.api._last_quota_time = time.time()

        # Periodic quota status alert (every 20 minutes) — after position sync for accurate counts
        now = time.time()
        if now - self._last_quota_alert_time > self._quota_alert_interval:
            self._last_quota_alert_time = now
            api_quota = self.api.volume_quota_remaining if self.api else None
            in_cooldown = self._is_volume_quota_cooldown()
            if api_quota is not None:
                status = f"{api_quota} TX"
            elif self.api and self.api._last_known_quota is not None:
                age = int((now - self.api._last_quota_time) / 60)
                status = f"~{self.api._last_known_quota} TX (updated {age}m ago)"
            elif in_cooldown:
                status = "0 TX (exhausted)"
            else:
                status = "unknown"
            positions_count = len(self.tracker.positions)
            emoji = "🔴" if (api_quota is not None and api_quota < 35) or (api_quota is None and in_cooldown) else "🟡" if (api_quota is not None and api_quota < 200) else "🟢"
            await self.alerts.send(
                f"{emoji} *Quota Status*\n"
                f"Remaining: {status}\n"
                f"Positions: {positions_count}\n"
                f"Cooldown: {'active' if in_cooldown else 'none'}"
            )

        # 1.4. HIGH-13: Clear pending sync AFTER position verification section completes.
        # This prevents the race where previous tick's verification is still sleeping
        # and sync re-detects the position as "new", giving it a fresh DSLState instead
        # of the AI-configured one.
        self._pending_sync.clear()

        # Reconcile saved DSL state with detected positions (first tick after restart)
        await self._reconcile_positions(live_positions)

        # MED-4: Refresh position context in result file so AI trader sees current positions
        # even between its own decisions (DSL/SL/TP closes update tracker but not result file)
        # HIGH-10: Clear dirty flag — AI trader has had a full tick to read the result
        if self._ai_mode:
            self._result_dirty = False
        if self._ai_mode and self.tracker.positions:
            self._refresh_position_context()

        # 1.5. Process signals — AI mode or rule-based
        # NOTE: Moved AFTER position sync/confirmation so tracker is populated
        # before close_all or other AI decisions execute.
        if self._ai_mode:
            await self._process_ai_decision()
        else:
            await self._process_signals()

        # 2. Update tracked markets
        self.api.set_tracked_markets(list(self.tracker.positions.keys()))
        self.api._save_tracked_markets()

        # 3. Get prices and check triggers — each position wrapped independently
        tracked_items = list(self.tracker.positions.items())
        for i, (mid, pos) in enumerate(tracked_items):
            try:
                if i < len(tracked_items) - 1:
                    await asyncio.sleep(self.cfg.price_call_delay)
                await self._process_position_tick(mid, pos)
            except Exception as e:
                logging.error(f"Error processing {pos.symbol} (market {mid}): {e}", exc_info=True)
                continue  # one bad position doesn't kill the rest

        # Persist state for crash/restart recovery
        self._save_state()

        # ── Idle tick tracking — extend sleep when flat with no activity ──
        if len(self.tracker.positions) == 0 and not self._signal_processed_this_tick and not quota_cooldown:
            self._idle_tick_count += 1
        else:
            # Reset on any activity (positions, signals, or quota cooldown)
            self._idle_tick_count = 0
        self._signal_processed_this_tick = False  # reset per-tick flag

    async def _process_position_tick(self, mid: int, pos: TrackedPosition):
        """Process a single position's tick — fetch price, evaluate triggers, execute if needed."""
        # CRITICAL-2: Skip DSL/SL evaluation for unverified positions
        if pos.unverified_at is not None:
            logging.debug(f"⏭️ {pos.symbol}: skipping tick (unverified, tick {pos.unverified_ticks})")
            return
        # MED-25: Skip DSL/SL evaluation for positions being verified as closed
        if mid in self._verifying_close:
            logging.debug(f"⏭️ {pos.symbol}: skipping tick (verification in progress)")
            return
        price = await self.api.get_price_with_mark_fallback(mid)
        if not price:
            # BUG-06: Orphaned position detection — don't skip silently
            consecutive = self._no_price_ticks.get(mid, 0) + 1
            self._no_price_ticks[mid] = consecutive
            logging.warning(
                f"⚠️ {pos.symbol}: no price data (mark + trade both failed) — "
                f"consecutive no-price ticks: {consecutive}/{self._no_price_alert_threshold}"
            )
            if consecutive >= self._no_price_alert_threshold:
                await self.alerts.send(
                    f"🚨 *ORPHANED POSITION*\n"
                    f"{pos.symbol} ({pos.side.upper()})\n"
                    f"No price data for {consecutive} consecutive ticks.\n"
                    f"Entry: ${pos.entry_price:,.2f}\n"
                    f"DSL/SL evaluation suspended — MANUAL CHECK REQUIRED."
                )
                # Reset counter to avoid spamming every tick (alert once per threshold)
                self._no_price_ticks[mid] = 0
            return
        # Reset no-price counter on successful price fetch
        if mid in self._no_price_ticks:
            del self._no_price_ticks[mid]

        action = self.tracker.update_price(mid, price)
        is_long = pos.side == "long"

        if not action:
            return

        # Unpack tuple actions (action_name, details_dict)
        if isinstance(action, tuple):
            action, details = action
        else:
            details = {}

        # Informational alerts (no trade execution)
        if action == "trailing_activated":
            msg = (
                f"🎯 *TRAILING TP ACTIVE*\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"Price: ${details['price']:,.2f}\n"
                f"ROE: {details['roe']:+.1f}%\n"
                f"P&L: {details['pnl']:+.2f}%"
            )
            logging.info(msg)
            await self.alerts.send(msg)
            return

        if action == "dsl_tier_lock":
            msg = (
                f"🔒 *DSL TIER LOCKED*\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"ROE (at trigger): {details['roe']:+.1f}%\n"
                f"Lock floor: {details['floor_roe']:+.1f}% ROE (~${details['floor_price']:,.2f})\n"
                f"Tier: +{details['tier']}% ({details['breaches']}x)"
            )
            logging.info(msg)
            await self.alerts.send(msg)
            return

        if action == "dsl_stagnation_timer":
            msg = (
                f"⏳ *DSL STAGNATION TIMER STARTED*\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"ROE: {details['roe']:+.1f}%\n"
                f"Will exit if no new high within {self.cfg.stagnation_minutes}min"
            )
            logging.info(msg)
            await self.alerts.send(msg)
            return

        # Exit actions (DSL)
        if action in ("tier_lock", "stagnation", "hard_sl"):
            roe = pos.dsl_state.current_roe(price) if pos.dsl_state else 0
            labels = {
                "tier_lock": "🔒 DSL TIER LOCK BREACH",
                "stagnation": "⏸️ DSL STAGNATION EXIT",
                "hard_sl": "🛑 HARD STOP LOSS",
            }
            hw_str = f"HW Peak: {pos.dsl_state.high_water_roe:+.1f}%" if pos.dsl_state else ""
            msg = (
                f"{labels.get(action, action)}\n"
                f"Symbol: {pos.symbol}\n"
                f"Side: {pos.side}\n"
                f"Trigger: ${price:,.2f}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"ROE: {roe:+.1f}%\n"
                f"{hw_str}"
            )
            logging.info(msg)
            await self.alerts.send(msg)
            # Check DSL close attempt cooldown
            cooldown_until = self._dsl_close_attempt_cooldown.get(pos.symbol)
            if cooldown_until and time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                logging.info(f"🧊 DSL close: {pos.symbol} in DSL close cooldown ({remaining}s remaining) — skipping. Position may need manual intervention.")
                return  # Don't remove from tracker, but stop retrying

            try:
                # MED-18: Cancel stale SL order before placing new one
                if pos.active_sl_order_id:
                    logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before DSL close")
                    await self.api._cancel_order(mid, int(pos.active_sl_order_id))
                    pos.active_sl_order_id = None
                sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
                if sl_success and sl_coi:
                    pos.active_sl_order_id = sl_coi
            except VolumeQuotaError:
                self._start_volume_quota_cooldown()
                # Track attempts with graduated delay (same as failed SL)
                attempts = self._dsl_close_attempts.get(pos.symbol, 0) + 1
                self._dsl_close_attempts[pos.symbol] = attempts
                delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
                retry_delay = self._sl_retry_delays[delay_idx]
                self._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay
                logging.warning(f"⚠️ DSL close: {pos.symbol} volume quota exhausted (attempt {attempts}, retry in {retry_delay}s)")
                return  # Don't remove from tracker, will retry after cooldown
            if sl_success:
                # CRITICAL-4: Don't log outcome yet — log ONCE after verification
                position_closed = await self._verify_position_closed(mid, pos.symbol)
                if not position_closed:
                    # Increment DSL close attempt counter
                    attempts = self._dsl_close_attempts.get(pos.symbol, 0) + 1
                    self._dsl_close_attempts[pos.symbol] = attempts
                    logging.warning(f"⚠️ {pos.symbol}: DSL SL submitted but position still open (attempt {attempts}/{self._max_close_attempts})")

                    if attempts >= self._max_close_attempts:
                        # Escalate: set cooldown and alert
                        self._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + self._close_cooldown_seconds
                        # CRITICAL-4: Log with estimated price as fallback after all retries exhausted
                        self._log_outcome(pos, price, f"dsl_{action}", estimated=True)
                        await self.alerts.send(
                            f"🚨 *DSL CLOSE FAILED ×{attempts}*\n"
                            f"{pos.side.upper()} {pos.symbol}\n"
                            f"ROE: {roe:+.1f}%\n"
                            f"Action: {labels.get(action, action)}\n"
                            f"Order submitted but NOT filled after {attempts} attempts.\n"
                            f"Cooldown: {self._close_cooldown_seconds // 60}min — MANUAL INTERVENTION REQUIRED."
                        )
                        logging.error(f"🚨 {pos.symbol}: max DSL close attempts ({self._max_close_attempts}) reached. Setting {self._close_cooldown_seconds}s cooldown.")
                    return  # Don't remove from tracker, will retry next tick (unless cooldown active)
                # Position successfully closed — reset DSL attempt counter
                self._dsl_close_attempts.pop(pos.symbol, None)
                self._dsl_close_attempt_cooldown.pop(pos.symbol, None)
            else:
                # SL order failed (rate-limited or rejected) — track attempts with graduated delay
                attempts = self._dsl_close_attempts.get(pos.symbol, 0) + 1
                self._dsl_close_attempts[pos.symbol] = attempts
                delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
                retry_delay = self._sl_retry_delays[delay_idx]
                self._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay

                logging.warning(f"⚠️ {pos.symbol}: DSL SL order rejected (attempt {attempts}, retry in {retry_delay}s)")

                if attempts >= 4:
                    # After 3 graduated retries, alert for manual intervention
                    await self.alerts.send(
                        f"🚨 *DSL SL FAILED ×{attempts}*\n"
                        f"{pos.side.upper()} {pos.symbol}\n"
                        f"ROE: {roe:+.1f}%\n"
                        f"Action: {labels.get(action, action)}\n"
                        f"Retry delays exhausted. Next retry in 15min.\n"
                        f"MANUAL INTERVENTION may be needed."
                    )
                return  # Don't remove from tracker, will retry after cooldown
            fill_price = await self._get_fill_price(mid, sl_coi)
            exit_price = fill_price if fill_price else price
            # CRITICAL-4: Log outcome ONCE with actual fill price after verification
            self._log_outcome(pos, exit_price, f"dsl_{action}")
            self._recently_closed[mid] = time.monotonic() + 300  # 5 min phantom guard
            pos.active_sl_order_id = None  # MED-18
            self.bot_managed_market_ids.discard(mid)
            self.tracker.remove_position(mid)
            # Post-close completion alert
            roe_pct = ((exit_price - pos.entry_price) / pos.entry_price * 100) if is_long else ((pos.entry_price - exit_price) / pos.entry_price * 100)
            await self.alerts.send(
                f"✅ *DSL → CLOSED*\n"
                f"{pos.side.upper()} {pos.symbol}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"Exit: ${exit_price:,.2f}\n"
                f"ROE: {roe_pct:+.1f}%\n"
                f"Reason: {labels.get(action, action)}"
            )
            return

        # Legacy actions
        tp_price = self.tracker.compute_tp_price(pos)
        sl_price = self.tracker.compute_sl_price(pos)
        pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100)

        msg = (
            f"⚠️ *{action.upper().replace('_', ' ')}* triggered!\n"
            f"Symbol: {pos.symbol}\n"
            f"Side: {pos.side}\n"
            f"Trigger: ${price:,.2f}\n"
            f"Entry: ${pos.entry_price:,.2f}\n"
            f"P&L: {pnl_pct:+.2f}%"
        )

        logging.info(msg)
        await self.alerts.send(msg)

        # Execute the order
        if action == "trailing_take_profit":
            # In quota emergency mode, skip TP orders — only SL proceeds
            if self._should_skip_non_critical_orders():
                logging.warning(f"🚫 {pos.symbol}: TP skipped in quota emergency mode, SL only")
                return  # Keep position, will retry next tick when quota recovers
            try:
                await self.api.execute_tp(mid, pos.size, price, is_long)
            except VolumeQuotaError:
                self._start_volume_quota_cooldown()
                logging.warning(f"⚠️ TP: {pos.symbol} volume quota exhausted — cooldown started")
                return  # Keep position, will retry after cooldown
            # TP submitted — log outcome immediately (no verification loop for TP)
            self._log_outcome(pos, price, action)
        else:
            try:
                # MED-18: Cancel stale SL order before placing new one
                if pos.active_sl_order_id:
                    logging.info(f"🗑️ {pos.symbol}: cancelling stale SL order {pos.active_sl_order_id} before legacy SL")
                    await self.api._cancel_order(mid, int(pos.active_sl_order_id))
                    pos.active_sl_order_id = None
                sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
                if sl_success and sl_coi:
                    pos.active_sl_order_id = sl_coi
            except VolumeQuotaError:
                self._start_volume_quota_cooldown()
                # Track attempts with graduated delay
                attempts = self._close_attempts.get(pos.symbol, 0) + 1
                self._close_attempts[pos.symbol] = attempts
                delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
                retry_delay = self._sl_retry_delays[delay_idx]
                self._close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay
                logging.warning(f"⚠️ SL: {pos.symbol} volume quota exhausted (attempt {attempts}, retry in {retry_delay}s)")
                return  # Don't remove from tracker, will retry after cooldown
            if sl_success:
                # CRITICAL-4: Don't log outcome yet — log ONCE after verification
                position_closed = await self._verify_position_closed(mid, pos.symbol)
                if not position_closed:
                    logging.warning(f"⚠️ {pos.symbol}: SL submitted but position still open — keeping in tracker")
                    return  # Don't remove from tracker, will retry next tick
                fill_price = await self._get_fill_price(mid, sl_coi)
                price = fill_price if fill_price else price
                # CRITICAL-4: Log outcome ONCE with actual fill price after verification
                self._log_outcome(pos, price, action)
            else:
                # SL order failed (rate-limited or rejected) — track attempts with graduated delay
                # Note: _close_attempts is shared between AI close and legacy SL paths.
                # This is intentional — both paths count toward the same circuit breaker,
                # preventing either from hammering a stuck position independently.
                attempts = self._close_attempts.get(pos.symbol, 0) + 1
                self._close_attempts[pos.symbol] = attempts
                delay_idx = min(attempts - 1, len(self._sl_retry_delays) - 1)
                retry_delay = self._sl_retry_delays[delay_idx]
                self._close_attempt_cooldown[pos.symbol] = time.monotonic() + retry_delay
                logging.warning(f"⚠️ {pos.symbol}: SL order rejected (attempt {attempts}, retry in {retry_delay}s)")

                if attempts >= 4:
                    roe_pct = ((price - pos.entry_price) / pos.entry_price * 100) if is_long else ((pos.entry_price - price) / pos.entry_price * 100)
                    await self.alerts.send(
                        f"🚨 *SL FAILED ×{attempts}*\n"
                        f"{pos.side.upper()} {pos.symbol}\n"
                        f"ROE: {roe_pct:+.1f}%\n"
                        f"Action: {action.replace('_', ' ').upper()}\n"
                        f"Retry delays exhausted. Next retry in 15min.\n"
                        f"MANUAL INTERVENTION may be needed."
                    )
                return  # Don't remove from tracker, will retry after cooldown
        self._recently_closed[mid] = time.monotonic() + 300  # 5 min phantom guard
        pos.active_sl_order_id = None  # MED-18
        self.bot_managed_market_ids.discard(mid)
        self.tracker.remove_position(mid)

    def _serialize_dsl_state(self, dsl: DSLState) -> dict:
        """Serialize DSLState to a JSON-compatible dict."""
        return {
            "side": dsl.side,
            "entry_price": dsl.entry_price,
            "leverage": dsl.leverage,
            "high_water_roe": dsl.high_water_roe,
            "high_water_price": dsl.high_water_price,
            "high_water_time": dsl.high_water_time.isoformat() if dsl.high_water_time else None,
            "current_tier_trigger": dsl.current_tier.trigger_pct if dsl.current_tier else None,
            "breach_count": dsl.breach_count,
            "locked_floor_roe": dsl.locked_floor_roe,
            "stagnation_active": dsl.stagnation_active,
            "stagnation_started": dsl.stagnation_started.isoformat() if dsl.stagnation_started else None,
        }

    def _write_equity_file(self, equity: float):
        """HIGH-12: Write equity to shared state file for dashboard to read."""
        try:
            # Write to ai-trader's state directory (sibling of executor)
            equity_path = Path(self._ai_trader_dir) / "state" / "equity.json"
            equity_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(equity_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"equity": equity, "timestamp": datetime.now(timezone.utc).isoformat()}, f)
            os.replace(tmp, str(equity_path))
        except Exception as e:
            logging.debug(f"Failed to write equity file: {e}")

    def _save_state(self):
        """Persist critical ephemeral state to disk for crash/restart recovery."""
        now = time.monotonic()
        state = {
            "last_ai_decision_ts": self._last_ai_decision_ts,
            "last_signal_timestamp": self._last_signal_timestamp,
            "last_signal_hash": self._last_signal_hash,
            # Convert monotonic deadlines to remaining seconds for portability
            "recently_closed": {str(mid): max(0, t - now) for mid, t in self._recently_closed.items()},
            "ai_close_cooldown": {s: max(0, t - now) for s, t in self._ai_close_cooldown.items()},
            "close_attempts": self._close_attempts,
            "close_attempt_cooldown": {s: max(0, t - now) for s, t in self._close_attempt_cooldown.items()},
            "dsl_close_attempts": self._dsl_close_attempts,
            "dsl_close_attempt_cooldown": {s: max(0, t - now) for s, t in self._dsl_close_attempt_cooldown.items()},
            # BUG-07: Which market_ids were opened by the bot
            "bot_managed_market_ids": sorted(self.bot_managed_market_ids),
            # Persist position state + DSL state for restart recovery
            "positions": {
                str(mid): {
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "size": pos.size,
                    "leverage": pos.dsl_state.leverage if pos.dsl_state else self.cfg.default_leverage,
                    "sl_pct": pos.sl_pct,
                    "high_water_mark": pos.high_water_mark,
                    "trailing_active": pos.trailing_active,
                    "trailing_sl_level": pos.trailing_sl_level,
                    "unverified_at": pos.unverified_at,
                    "unverified_ticks": pos.unverified_ticks,
                    "active_sl_order_id": pos.active_sl_order_id,  # MED-18
                    "dsl": self._serialize_dsl_state(pos.dsl_state) if pos.dsl_state else None,
                }
                for mid, pos in self.tracker.positions.items()
            },
        }
        try:
            state_path = Path(__file__).parent / "state" / "bot_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(state_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, str(state_path))
        except Exception as e:
            logging.debug(f"Failed to save bot state: {e}")

    def _load_state(self):
        """Restore critical ephemeral state from disk after restart."""
        state_path = Path(__file__).parent / "state" / "bot_state.json"
        if not state_path.exists():
            return
        try:
            with open(state_path) as f:
                state = json.load(f)
            now = time.monotonic()

            self._last_ai_decision_ts = state.get("last_ai_decision_ts")
            self._last_signal_timestamp = state.get("last_signal_timestamp")
            self._last_signal_hash = state.get("last_signal_hash")

            # Convert remaining seconds back to monotonic deadlines
            for mid_str, remaining in state.get("recently_closed", {}).items():
                if remaining > 0:
                    self._recently_closed[int(mid_str)] = now + remaining

            for symbol, remaining in state.get("ai_close_cooldown", {}).items():
                if remaining > 0:
                    self._ai_close_cooldown[symbol] = now + remaining

            self._close_attempts = state.get("close_attempts", {})

            for symbol, remaining in state.get("close_attempt_cooldown", {}).items():
                if remaining > 0:
                    self._close_attempt_cooldown[symbol] = now + remaining

            self._dsl_close_attempts = state.get("dsl_close_attempts", {})

            for symbol, remaining in state.get("dsl_close_attempt_cooldown", {}).items():
                if remaining > 0:
                    self._dsl_close_attempt_cooldown[symbol] = now + remaining

            # BUG-07: Load bot-managed market IDs
            managed_ids = state.get("bot_managed_market_ids", [])
            self.bot_managed_market_ids = set(managed_ids)

            # If managed market IDs were lost but we have saved positions, reconstruct
            if not self.bot_managed_market_ids and state.get("positions"):
                for mid_str in state["positions"]:
                    try:
                        self.bot_managed_market_ids.add(int(mid_str))
                    except (ValueError, TypeError):
                        pass
                if self.bot_managed_market_ids:
                    logging.warning(f"Reconstructed bot_managed_market_ids from saved positions: {sorted(self.bot_managed_market_ids)}")

            # Load saved positions for DSL state restoration (applied after exchange detection)
            self._saved_positions = state.get("positions") or None

            restored = []
            if self._last_ai_decision_ts:
                restored.append(f"ai_decision_ts={self._last_ai_decision_ts}")
            if self._recently_closed:
                restored.append(f"recently_closed={len(self._recently_closed)}")
            if self._ai_close_cooldown:
                restored.append(f"ai_close_cooldown={len(self._ai_close_cooldown)}")
            if self._close_attempts:
                restored.append(f"close_attempts={len(self._close_attempts)}")
            if self._saved_positions:
                restored.append(f"saved_positions={len(self._saved_positions)}")
            if restored:
                logging.info(f"🔄 Bot state restored: {', '.join(restored)}")

            # If we restored a last decision timestamp, check if the current decision
            # is the same one we were processing before the crash.
            # - Same timestamp → ACK to unblock AI trader (post-crash unblock, from IPC fix)
            # - Different timestamp → new decision arrived during downtime, skip ACK
            #   so _tick() processes it normally (otherwise the decision is lost)
            if self._last_ai_decision_ts:
                try:
                    current_decision = safe_read_json(Path(self._ai_decision_file))
                    if current_decision and current_decision.get("timestamp") == self._last_ai_decision_ts:
                        # Same decision bot was processing before crash — ACK to unblock AI trader
                        ack_path = str(Path(self._ai_decision_file)) + ".ack"
                        decision_id = current_decision.get("decision_id", "")
                        with open(ack_path, "w") as f:
                            f.write(decision_id)
                        logging.info(f"🔓 Post-crash ACK written for decision {decision_id} (same decision, unblocking AI trader)")
                    elif current_decision:
                        logging.info("⏸️ New decision arrived during downtime — skipping ACK, will process on first tick")
                    else:
                        logging.info("⏸️ No decision file found — skipping ACK")
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"Failed to load bot state: {e}")

    async def _reconcile_state_with_exchange(self):
        """Reconcile bot state with actual exchange positions on startup.

        Ensures bot_managed_market_ids and tracker.positions match what's on the exchange.
        Prevents drift from crashes where state was saved but exchange state diverged.

        - Exchange positions not in state → adopted with fresh DSL state
        - State positions not on exchange → removed
        - Positions in both → validated and updated if stale
        """
        if not self.api:
            logging.warning("⚠️ Reconciliation skipped: API not initialized")
            return

        try:
            # Fetch current positions from exchange
            live_positions = await self.api.get_positions()

            # Handle API failure gracefully — don't crash startup
            if live_positions is None:
                logging.warning("⚠️ Reconciliation skipped: failed to fetch positions from exchange")
                return

            # Safety: if exchange returns empty but we have positions in state,
            # don't remove them — likely proxy/connection not ready yet on first call.
            # Only reconcile removals when exchange actually returns data.
            state_mids = set(self.bot_managed_market_ids)
            if not live_positions and state_mids:
                logging.warning(
                    f"⚠️ Reconciliation: exchange returned 0 positions but state has "
                    f"{len(state_mids)} — skipping removal (proxy may not be ready)"
                )
                # Still try to save any adopted positions, just don't remove
                # Since there are no exchange positions, there's nothing to adopt either
                return

            # Build market ID sets
            exchange_mids = {p["market_id"] for p in live_positions}

            # Track changes for summary
            adopted = 0
            removed = 0
            confirmed = 0
            updated = 0
            changed = False

            # Exchange positions NOT in state → adopt them
            for pos_data in live_positions:
                mid = pos_data["market_id"]
                if mid not in state_mids:
                    # Adopt this position — it exists on exchange but not in our state
                    self.bot_managed_market_ids.add(mid)
                    self.tracker.add_position(
                        mid,
                        pos_data["symbol"],
                        pos_data["side"],
                        pos_data["entry_price"],
                        pos_data["size"],
                        leverage=pos_data.get("leverage"),
                    )
                    adopted += 1
                    changed = True
                    logging.info(
                        f"📥 Reconciled adopted: {pos_data['side'].upper()} {pos_data['symbol']} "
                        f"@ ${pos_data['entry_price']:,.2f}"
                    )
                else:
                    # Position exists in both — validate and update if stale
                    existing = self.tracker.positions.get(mid)
                    if existing:
                        # Update size if stale (use exchange as source of truth)
                        if abs(existing.size - pos_data["size"]) > 0.001:
                            logging.info(
                                f"🔄 Reconciled updated size: {pos_data['symbol']} "
                                f"state={existing.size} → exchange={pos_data['size']}"
                            )
                            existing.size = pos_data["size"]
                            updated += 1
                            changed = True
                        else:
                            confirmed += 1

            # State positions NOT on exchange → remove them
            for mid in list(state_mids):
                if mid not in exchange_mids:
                    # Remove from state
                    self.bot_managed_market_ids.discard(mid)
                    self.tracker.positions.pop(mid, None)
                    removed += 1
                    changed = True
                    logging.info(f"🗑️ Reconciled removed: market_id={mid} (no longer on exchange)")

            # Save state if any changes were made
            if changed:
                self._save_state()
                logging.info(
                    f"🔄 Reconciled: +{adopted} adopted, -{removed} removed, "
                    f"={confirmed} confirmed, ~{updated} updated"
                )
            else:
                logging.info(f"✅ Reconciled: all {confirmed} positions match exchange")

        except Exception as e:
            # Never crash startup due to reconciliation failure
            logging.warning(f"⚠️ Reconciliation failed (non-fatal): {e}")
            # Continue with normal startup — _tick() will handle position sync

    async def _restore_dsl_state(self, dsl_data: dict, pos: TrackedPosition):
        """Restore saved DSL state onto a live TrackedPosition.

        Overwrites all tracked fields on the existing DSLState from saved data.
        The DSLState object is already constructed by tracker.add_position() with
        correct entry_price/side/leverage — we just restore the progress fields.
        """
        if not dsl_data or not pos.dsl_state:
            return
        try:
            saved_tier_trigger = dsl_data.get("current_tier_trigger")
            tier = None
            if saved_tier_trigger is not None:
                for t in self.tracker.dsl_cfg.tiers:
                    if t.trigger_pct == saved_tier_trigger:
                        tier = t
                        break
                # MED-24: Default to first tier on mismatch instead of None
                if tier is None and self.tracker.dsl_cfg.tiers:
                    tier = self.tracker.dsl_cfg.tiers[0]
                    logging.warning(
                        f"⚠️ Saved tier trigger {saved_tier_trigger}% not found in current config, "
                        f"defaulting to first tier ({tier.trigger_pct}%)"
                    )

            saved_hw_time = dsl_data.get("high_water_time")
            hw_time = datetime.fromisoformat(saved_hw_time) if saved_hw_time else None
            saved_stag_time = dsl_data.get("stagnation_started")
            stag_time = datetime.fromisoformat(saved_stag_time) if saved_stag_time else None

            pos.dsl_state.high_water_roe = dsl_data.get("high_water_roe", 0.0)
            pos.dsl_state.high_water_price = dsl_data.get("high_water_price", 0.0)
            pos.dsl_state.high_water_time = hw_time
            pos.dsl_state.current_tier = tier
            pos.dsl_state.breach_count = dsl_data.get("breach_count", 0)
            pos.dsl_state.locked_floor_roe = dsl_data.get("locked_floor_roe")
            pos.dsl_state.stagnation_active = dsl_data.get("stagnation_active", False)
            pos.dsl_state.stagnation_started = stag_time

            logging.info(
                f"🔄 Restored DSL state for {pos.symbol}: "
                f"HW_ROE={pos.dsl_state.high_water_roe:+.1f}%, "
                f"Tier={tier.trigger_pct if tier else 'none'}, "
                f"Floor={pos.dsl_state.locked_floor_roe}, "
                f"Breaches={pos.dsl_state.breach_count}"
            )
        except Exception as e:
            logging.warning(f"Failed to restore DSL state for {pos.symbol}: {e}")
            try:
                await self.alerts.send(f"⚠️ *DSL State Lost:* {pos.symbol}\nRestart reset tier progress. Starting fresh.")
            except Exception:
                pass

    async def _reconcile_positions(self, live_positions: list[dict] | None):
        """Reconcile saved positions with live exchange positions.

        Called once per tick (no-op after first successful reconciliation).
        Restores saved DSLState for positions that match the exchange.

        Saved but not on exchange → dropped (position was closed).
        On exchange but not saved → stays with fresh DSLState (new position).
        Both → DSLState restored from saved data.
        """
        if not self._saved_positions or live_positions is None:
            return

        live_mids = {p["market_id"] for p in live_positions}

        for mid_str, saved_pos in self._saved_positions.items():
            try:
                mid = int(mid_str)
            except (ValueError, TypeError):
                continue

            if mid in live_mids and mid in self.tracker.positions:
                # Position exists on both exchange and tracker — restore DSL state
                pos = self.tracker.positions[mid]
                dsl_data = saved_pos.get("dsl")
                if dsl_data:
                    await self._restore_dsl_state(dsl_data, pos)
                # Also restore legacy trailing state
                if saved_pos.get("trailing_sl_level") is not None:
                    pos.trailing_sl_level = saved_pos["trailing_sl_level"]
                if saved_pos.get("trailing_active"):
                    pos.trailing_active = True
                if saved_pos.get("high_water_mark"):
                    pos.high_water_mark = max(pos.high_water_mark, saved_pos["high_water_mark"])
                # Restore AI-specified stop loss % (Fix #10)
                if saved_pos.get("sl_pct") is not None:
                    pos.sl_pct = saved_pos["sl_pct"]
                # CRITICAL-2: Restore unverified state (reset ticks to 1 on restart)
                if saved_pos.get("unverified_at") is not None:
                    pos.unverified_at = time.time()
                    pos.unverified_ticks = 1  # Reset count on restart
                    logging.info(f"🔄 Restored unverified state for {pos.symbol} (tick 1/3)")
                # MED-18: Restore active SL order ID for cancellation
                if saved_pos.get("active_sl_order_id"):
                    pos.active_sl_order_id = saved_pos["active_sl_order_id"]
            elif mid not in live_mids:
                logging.info(f"🗑️ Saved position {saved_pos.get('symbol', mid)} no longer on exchange — dropped")

        # Clear after reconciliation (one-time operation per restart)
        self._saved_positions = None

    def _shutdown(self):
        logging.info("Shutdown requested...")
        self.running = False
        self._save_state()


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
