"""
Lighter Copilot — Trailing TP/SL Bot

Monitors open positions on Lighter.xyz and manages
trailing take profit + stop loss orders.
"""

import asyncio
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
load_dotenv(Path(__file__).parent / ".env")

import lighter
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

    def validate(self) -> list[str]:
        """Validate config values. Returns list of error strings (empty = valid)."""
        errors = []

        # Required non-empty string fields
        for field_name in ("lighter_url", "account_index", "api_key_index", "api_key_private"):
            val = getattr(self, field_name)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"Required field '{field_name}' is missing or empty")

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
                await session.post(url, json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
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
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
                    consecutive_breaches=t.get("consecutive_breaches", 3),
                )
                for t in cfg.dsl_tiers
            ]

    def compute_tp_price(self, pos: TrackedPosition) -> float | None:
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
        return pos.trailing_sl_level or pos.entry_price * (1 - self.cfg.sl_pct / 100 if pos.side == "long" else 1 + self.cfg.sl_pct / 100)

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
            if not pos.trailing_active:
                trigger = pos.entry_price * (1 + self.cfg.trailing_tp_trigger_pct / 100)
                if price >= trigger:
                    pos.trailing_active = True
                    trailing_just_activated = True
                    logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${price:,.2f}")
        elif pos.side == "short" and price < pos.high_water_mark:
            pos.high_water_mark = price
            if not pos.trailing_active:
                trigger = pos.entry_price * (1 - self.cfg.trailing_tp_trigger_pct / 100)
                if price <= trigger:
                    pos.trailing_active = True
                    trailing_just_activated = True
                    logging.info(f"🎯 {pos.symbol} trailing TP ACTIVE at ${price:,.2f}")

        # Update trailing stop loss (ratchets up on longs, down on shorts — never reverses)
        if pos.side == "long":
            candidate = price * (1 - self.cfg.sl_pct / 100)
            if pos.trailing_sl_level is None or candidate > pos.trailing_sl_level:
                old = pos.trailing_sl_level
                pos.trailing_sl_level = candidate
                if old is not None:
                    logging.info(f"🛡️ {pos.symbol} trailing SL advanced: ${old:,.2f} → ${candidate:,.2f}")
        else:
            candidate = price * (1 + self.cfg.sl_pct / 100)
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

    def add_position(self, market_id: int, symbol: str, side: str, entry: float, size: float, leverage: float = None):
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
        )
        self.positions[market_id] = pos
        mode = f"DSL (lev={lev}x)" if self.cfg.dsl_enabled else "legacy trailing"
        logging.info(f"📌 Tracking: {side.upper()} {symbol} @ ${entry:,.2f}, size={size}, mode={mode}")

    def remove_position(self, market_id: int):
        self.positions.pop(market_id, None)


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

        # ── SignerClient with optional proxy (subclass instead of monkey-patch) ──
        if cfg.proxy_url:
            signer_config = lighter.Configuration(host=cfg.lighter_url)
            signer_config.proxy = cfg.proxy_url
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

            # self._signer = ProxySignerClient(
            #     url=cfg.lighter_url,
            #     account_index=cfg.account_index,
            #     api_private_keys={cfg.api_key_index: cfg.api_key_private},
            # )
            self._signer = None  # Create lazily in async context
            self._signer_has_own_client = True
        else:
            # self._signer = lighter.SignerClient(
            #     url=cfg.lighter_url,
            #     account_index=cfg.account_index,
            #     api_private_keys={cfg.api_key_index: cfg.api_key_private},
            # )
            self._signer = None  # Create lazily in async context
            self._signer_has_own_client = False

        # Persisted tracked market IDs
        self._state_dir = Path(__file__).parent / "state"
        self._tracked_markets_file = self._state_dir / "tracked_markets.json"
        self.tracked_market_ids: list[int] = self._load_tracked_markets()

        # Mark price cache — derived from unrealized_pnl during position sync
        # More accurate than recent_trades for ROE/stop-loss calculations
        self._mark_prices: dict[int, float] = {}  # market_id → mark_price

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

    async def get_positions(self) -> list[dict]:
        """Fetch open positions from Lighter."""
        try:
            await self._ensure_client()
            result = await self._account_api.account(
                by="index", value=str(self.cfg.account_index)
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
            logging.error(f"Failed to fetch positions: {e}")
            return []

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
            # For shorts: pnl = size * (entry - mark) → mark = entry - pnl/size
            # But pnl sign already encodes direction (positive = profit),
            # and size is signed (positive = long, negative = short).
            # Unified formula: mark = entry + pnl / abs(size)
            abs_size = abs(size)
            mark_price = entry + (pnl / abs_size)
            if mark_price > 0:
                self._mark_prices[mid] = mark_price

    def get_mark_price(self, market_id: int) -> float | None:
        """Get mark price for a position. Uses cached mark price from account API
        (derived from unrealized_pnl), falls back to recent_trades."""
        cached = self._mark_prices.get(market_id)
        if cached and cached > 0:
            return cached
        # Fallback to recent trades (may be stale, but better than nothing)
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
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    logging.info(f"✅ TP order submitted: tx={tx}, resp_code={resp_code}, resp_msg={resp_msg}")
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
            slippage = int(best_price_int * 0.02)
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
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    if resp_msg and "didn't use volume quota" in str(resp_msg):
                        logging.warning(f"⚠️ Open order rate-limited (volume quota): {resp_msg}")
                        return False  # Opening should fail explicitly if rate-limited
                    logging.info(f"✅ Position opened: {'LONG' if is_long else 'SHORT'} {size_usd:.2f} USD -> tx={tx}, resp_code={resp_code}, resp_msg={resp_msg}")
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
                max_slippage=0.02,
                is_ask=is_long,      # close long = sell = ask
                reduce_only=True,
                ideal_price=best_price_int,
            )
            logging.info(
                f"🔍 SL order: market={market_id}, size={size}, base_amount={base_amount}, "
                f"best_price={best_price_int}, max_slippage=0.02, is_ask={is_long}, "
                f"reduce_only=True, coi={client_order_index}"
            )
            # SDK returns Union[Tuple[CreateOrder, RespSendTx, None], Tuple[None, None, str]]
            if isinstance(result, tuple):
                if len(result) >= 3 and result[2] is not None:
                    error_msg = result[2]
                    logging.error(f"❌ SL order rejected by exchange: {error_msg}")
                    # Check for specific error types
                    if "slippage" in str(error_msg).lower():
                        logging.error(f"❌ SL order rejected: excessive slippage — market may have moved beyond 2%")
                    return False, None
                if result[0] is None:
                    logging.error("❌ SL order returned None (no order created)")
                    return False, None
                # Log full response details for debugging
                tx = result[0]
                resp = result[1] if len(result) > 1 else None
                if resp is not None:
                    resp_code = getattr(resp, 'code', None)
                    resp_msg = getattr(resp, 'msg', None) or getattr(resp, 'message', None)
                    resp_tx_hash = getattr(resp, 'tx_hash', None)
                    resp_pred_ms = getattr(resp, 'predicted_execution_time_ms', None)
                    resp_quota = getattr(resp, 'volume_quota_remaining', None)
                    if resp_msg and "didn't use volume quota" in str(resp_msg):
                        logging.warning(f"⚠️ SL order rate-limited (volume quota): {resp_msg}")
                        # Still return True — the order MIGHT execute later, but warn clearly
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

    async def close(self):
        """Close all aiohttp sessions."""
        errors = []
        # Close main API client
        try:
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
        self._opened_signals: set[int] = set()
        self._min_score = 60  # Only open positions for signals >= this score
        # AI Autopilot
        self._ai_mode = cfg.ai_mode
        self._ai_decision_file = cfg.ai_decision_file
        self._ai_result_file = cfg.ai_result_file
        self._last_ai_decision_ts: str | None = None
        # AI close cooldown — prevent re-opening same symbol after AI closes it
        self._ai_close_cooldown: dict[str, float] = {}  # symbol → monotonic() deadline
        self._ai_cooldown_seconds = 1800  # 30 minutes
        self._api_lag_warnings: dict[str, float] = {}  # symbol → last warning timestamp
        self._pending_sync: set[int] = set()  # market_ids opened this tick — skip in sync
        # Phantom position prevention: require 2 consecutive sync cycles to confirm new positions
        self._pending_positions: dict[int, dict] = {}  # market_id → pos_data (awaiting confirmation)
        self._recently_closed: dict[int, float] = {}  # market_id → monotonic() expire time (bot-closed positions)
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

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        try:
            while self.running:
                await self._tick()
                await asyncio.sleep(self.cfg.price_poll_interval)
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
            try:
                await self.alerts.send("🔴 *Lighter Copilot* stopped")
            except Exception:
                pass
            logging.info("Bot stopped.")

    async def _get_balance(self) -> float:
        """Fetch USDC collateral balance from Lighter."""
        try:
            result = await self.api._account_api.account(
                by="index", value=str(self.cfg.account_index)
            )
            for acc in result.accounts:
                return float(acc.collateral) if acc.collateral else 0
        except Exception as e:
            logging.error(f"Failed to fetch balance: {e}")
        return 0

    async def _process_signals(self):
        """Read signals.json and open positions for new, unopened signals."""
        signals_path = Path(self._signals_file)
        if not signals_path.exists():
            return

        try:
            with open(signals_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Failed to read signals file: {e}")
            return

        # Only process new signal files
        if data.get("timestamp") == self._last_signal_timestamp:
            return
        self._last_signal_timestamp = data.get("timestamp")

        # Clear old tracked signals on new signal run
        self._opened_signals.clear()

        # Auto-detect balance and scale positions proportionally
        balance = await self._get_balance()
        scanner_equity = data.get("config", {}).get("accountEquity", balance)
        if balance <= 0:
            logging.warning("⚠️ Zero or negative balance, cannot open positions")
            return
        scale = balance / scanner_equity
        if abs(scale - 1.0) > 0.01:
            logging.info(f"📐 Scaling positions: balance=${balance:.2f} / scanner_equity=${scanner_equity:.2f} = {scale:.4f}×")

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
                success = await self.api.open_position(mid, size_usd, is_long, current_price)
                if success:
                    self._opened_signals.add(mid)
                    self._pending_sync.add(mid)
                    # Track in position tracker — entry at current price
                    actual_size = size_usd / current_price
                    self.tracker.add_position(mid, symbol, direction, current_price, actual_size, leverage=self.cfg.default_leverage)
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
            size_usd = decision.get("size_usd", 0)
            if not isinstance(size_usd, (int, float)) or size_usd <= 0:
                return f"Invalid size_usd: {size_usd!r}"
            direction = decision.get("direction")
            if direction not in ("long", "short"):
                return f"Invalid direction: {direction!r}"
            confidence = decision.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
                    return f"Invalid confidence: {confidence!r}"

        if action == "close":
            confidence = decision.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
                    return f"Invalid confidence: {confidence!r}"

        return None

    async def _process_ai_decision(self):
        """Read AI decision file and execute if valid."""
        path = Path(self._ai_decision_file)
        if not path.exists():
            return

        try:
            with open(path) as f:
                decision = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        # Only process new decisions
        ts = decision.get("timestamp", "")
        if ts == self._last_ai_decision_ts:
            return
        self._last_ai_decision_ts = ts

        # Validate decision
        validation_error = self._validate_ai_decision(decision)
        if validation_error:
            logging.warning(f"⚠️ AI decision rejected: {validation_error}")
            self._write_ai_result(decision, success=False)
            return

        action = decision.get("action")
        if action not in ("open", "close", "close_all"):
            return  # hold or unknown — do nothing

        if action == "close_all":
            await self._execute_ai_close_all(decision)
            self._write_ai_result(decision, success=True)
            return
        elif action == "open":
            success = await self._execute_ai_open(decision)
        elif action == "close":
            success = await self._execute_ai_close(decision)
        else:
            success = True

        # Write result back for the AI trader
        self._write_ai_result(decision, success=success)

    async def _execute_ai_open(self, decision: dict) -> bool:
        """Execute an AI-recommended open. Returns True on success."""
        symbol = decision.get("symbol")
        direction = decision.get("direction")
        size_usd = decision.get("size_usd", 0)

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

        is_long = direction == "long"
        current_price = await self.api.get_price(market_id)
        if not current_price:
            logging.warning(f"AI open: no price for {symbol}")
            return False

        success = await self.api.open_position(market_id, size_usd, is_long, current_price)
        if success:
            self._pending_sync.add(market_id)
            actual_size = size_usd / current_price
            self.tracker.add_position(market_id, symbol, direction, current_price, actual_size, leverage=self.cfg.default_leverage)
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
        """Check if there are any active (unfilled) orders for this market on our account."""
        try:
            await self.api._ensure_client()
            # Generate auth token for the request
            auth = None
            try:
                await self.api._ensure_signer()
                if self.api._signer is not None:
                    auth, err = self.api._signer.create_auth_token_with_expiry()
                    if err:
                        logging.warning(f"⚠️ Auth token generation error: {err}")
                        auth = None
            except Exception as auth_err:
                logging.debug(f"Auth generation skipped: {auth_err}")
            orders = await self.api._order_api.account_active_orders(
                account_index=self.cfg.account_index,
                market_id=market_id,
                auth=auth,
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
        """
        delays = [5, 10, 15, 20]  # progressive delays: 5s, 10s, 15s, 20s = 50s total
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
                sl_orders = [o for o in active_orders if o.get('is_ask') == True]  # sell orders for long close
                logging.info(
                    f"⏳ {symbol}: position still open (attempt {attempt + 1}/{len(delays)}), "
                    f"active_orders={len(active_orders)}, sl_orders={len(sl_orders)}"
                )
            except Exception as e:
                logging.warning(f"⚠️ {symbol}: error verifying closure (attempt {attempt + 1}): {e}")
        return False

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

        sl_success, sl_coi = await self.api.execute_sl(mid_to_close, pos.size, current_price, is_long)
        if not sl_success:
            logging.warning(f"⚠️ Failed to submit close order for {pos.side} {symbol} — keeping in tracker")
            return False

        # Order submitted — now verify it actually filled by polling the API
        position_closed = await self._verify_position_closed(mid_to_close, symbol)

        if not position_closed:
            # Increment attempt counter
            attempts = self._close_attempts.get(symbol, 0) + 1
            self._close_attempts[symbol] = attempts
            logging.warning(f"⚠️ {symbol}: close order submitted but position still open (attempt {attempts}/{self._max_close_attempts})")

            if attempts >= self._max_close_attempts:
                # Escalate: set cooldown and alert
                self._close_attempt_cooldown[symbol] = time.monotonic() + self._close_cooldown_seconds
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

        self._log_outcome(pos, current_price, "ai_close")
        self._recently_closed[mid_to_close] = time.monotonic() + 300  # 5 min phantom guard
        self.tracker.remove_position(mid_to_close)

        roe = ((current_price - pos.entry_price) / pos.entry_price * 100) if is_long \
            else ((pos.entry_price - current_price) / pos.entry_price * 100)

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

    async def _execute_ai_close_all(self, decision: dict):
        """Emergency close all positions."""
        reasoning = decision.get("reasoning", "Emergency halt")
        logging.warning(f"🚨 AI close_all triggered: {reasoning}")
        await self.alerts.send(
            f"🚨 *AI → CLOSE ALL*\n"
            f"Reason: {reasoning[:200]}"
        )
        for i, (mid, pos) in enumerate(list(self.tracker.positions.items())):
            is_long = pos.side == "long"
            current_price = await self.api.get_price(mid) if self.api else None
            if i < len(self.tracker.positions) - 1:
                await asyncio.sleep(self.cfg.price_call_delay)
            if current_price:
                sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, current_price, is_long)
                if sl_success:
                    self._log_outcome(pos, current_price, "ai_close_all")
                    roe = ((current_price - pos.entry_price) / pos.entry_price * 100) if is_long \
                        else ((pos.entry_price - current_price) / pos.entry_price * 100)
                    logging.info(f"Emergency closed: {pos.side} {pos.symbol} ROE={roe:+.1f}%")
                    self._recently_closed[mid] = time.monotonic() + 300  # 5 min phantom guard
                    self.tracker.remove_position(mid)
                else:
                    logging.warning(f"⚠️ Failed to close {pos.side} {pos.symbol} — keeping in tracker")
            else:
                logging.warning(f"⚠️ No price for {pos.symbol} — skipping close, keeping in tracker")

    def _resolve_market_id(self, symbol: str) -> int | None:
        """Resolve symbol to market_id. Tries scanner signals first, then cached positions."""
        # Try from signals file
        try:
            signals_path = Path(self._signals_file)
            if signals_path.exists():
                with open(signals_path) as f:
                    data = json.load(f)
                for opp in data.get("opportunities", []):
                    if opp.get("symbol") == symbol:
                        return opp.get("marketId")
        except Exception:
            pass

        # Try from position tracker (already-open positions)
        for mid, pos in self.tracker.positions.items():
            if pos.symbol == symbol:
                return mid

        return None

    def _log_outcome(self, pos: TrackedPosition, exit_price: float, exit_reason: str):
        """Log a closed trade outcome to the AI trader journal DB.

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
            roe_pct = pnl_pct * self.cfg.default_leverage

            _db.log_outcome({
                "symbol": pos.symbol,
                "direction": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "size_usd": size_usd,
                "pnl_usd": pnl_usd,
                "pnl_pct": roe_pct,
                "hold_time_seconds": hold_seconds,
                "max_drawdown_pct": 0,  # not tracked yet
                "exit_reason": exit_reason,
                "decision_snapshot": {},
            })
            logging.info(
                f"📝 Outcome logged: {pos.side} {pos.symbol} "
                f"PnL=${pnl_usd:+.2f} ({roe_pct:+.1f}% ROE) "
                f"held={hold_seconds}s reason={exit_reason}"
            )
        except Exception as e:
            logging.warning(f"Failed to log outcome: {e}")

    def _write_ai_result(self, decision: dict, success: bool):
        """Write execution result for the AI trader to read."""
        try:
            positions = []
            for mid, pos in self.tracker.positions.items():
                positions.append({
                    "market_id": mid,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "size": pos.size,
                    "size_usd": pos.size * pos.entry_price,
                })
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_action": decision.get("action"),
                "decision_symbol": decision.get("symbol"),
                "success": success,
                "positions": positions,
            }
            with open(self._ai_result_file, "w") as f:
                json.dump(result, f, indent=2)
        except Exception as e:
            logging.warning(f"Failed to write AI result: {e}")

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
        # Prune DSL close attempt cooldowns
        expired_dsl_close_cd = [s for s, t in self._dsl_close_attempt_cooldown.items() if t < now]
        for s in expired_dsl_close_cd:
            del self._dsl_close_attempt_cooldown[s]
        # Prune symbol cache (entries older than TTL)
        if self.api:
            expired_symbols = [mid for mid, (ts, _) in self.api._symbol_cache.items() if (now - ts) > self.api._symbol_cache_ttl]
            for mid in expired_symbols:
                del self.api._symbol_cache[mid]

    async def _tick(self):
        """One cycle: sync positions, update prices, check triggers."""
        self._prune_caches()
        if not self.api:
            return

        # 1. Sync positions from Lighter
        live_positions = await self.api.get_positions()
        live_mids = {p["market_id"] for p in live_positions}

        # Cache mark prices from unrealized_pnl (authoritative exchange price for PnL)
        self.api.update_mark_prices_from_positions(live_positions)

        # Detect new positions (skip markets opened this tick to avoid race)
        # Two-cycle confirmation: position must appear in 2 consecutive syncs before tracking
        for pos in live_positions:
            mid = pos["market_id"]
            if mid in self._pending_sync:
                continue
            if mid in self.tracker.positions:
                continue  # already tracked
            if pos["entry_price"] <= 0:
                continue

            symbol = pos["symbol"]

            # Skip if bot recently closed this position (stale API data)
            if mid in self._recently_closed:
                logging.debug(f"⏭️ {symbol}: recently closed by bot, ignoring stale API data")
                continue

            # Check AI close cooldown before re-tracking
            cooldown_until = self._ai_close_cooldown.get(symbol)
            if cooldown_until and time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                # Rate limit API lag warnings (once per minute per symbol)
                now = time.monotonic()
                last_warned = self._api_lag_warnings.get(symbol, 0)
                if now - last_warned > 60:
                    self._api_lag_warnings[symbol] = now
                    logging.warning(f"🧊 DETECTED {symbol} from Lighter API but AI close cooldown active ({remaining}s) - API lag? IGNORING")
                continue

            # Two-cycle confirmation: first appearance → pending, second → track
            if mid not in self._pending_positions:
                self._pending_positions[mid] = pos
                logging.info(f"⏳ {symbol}: new position detected (pending confirmation, will track after 2nd sync)")
                continue

            # Second consecutive sync — confirm and track
            logging.info(f"📌 API POSITION CONFIRMED: {pos['side'].upper()} {symbol}")
            self.tracker.add_position(
                mid, pos["symbol"], pos["side"], pos["entry_price"], pos["size"],
                leverage=pos.get("leverage")
            )
            self._pending_positions.pop(mid, None)
            await self.alerts.send(
                f"📌 *New position confirmed*\n"
                f"{pos['side'].upper()} {pos['symbol']} @ ${pos['entry_price']:,.2f}\n"
                f"Size: {pos['size']}"
            )

        # Clean up pending positions that disappeared (were phantom)
        disappeared = set(self._pending_positions.keys()) - live_mids
        for mid in disappeared:
            phantom = self._pending_positions.pop(mid)
            logging.debug(f"👻 Phantom position gone: {phantom.get('symbol', f'MKT{mid}')}")

        # Detect closed positions
        for mid in list(self.tracker.positions.keys()):
            if mid not in live_mids:
                pos = self.tracker.positions[mid]
                logging.info(f"Position closed: {pos.symbol}")
                self.tracker.remove_position(mid)

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

    async def _process_position_tick(self, mid: int, pos: TrackedPosition):
        """Process a single position's tick — fetch price, evaluate triggers, execute if needed."""
        price = await self.api.get_price_with_mark_fallback(mid)
        if not price:
            return

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
                f"ROE: {details['roe']:+.1f}%\n"
                f"Floor: {details['floor_roe']:+.1f}% ROE (~${details['floor_price']:,.2f})\n"
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
                f"Exit: ${price:,.2f}\n"
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

            sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
            if sl_success:
                position_closed = await self._verify_position_closed(mid, pos.symbol)
                if not position_closed:
                    # Increment DSL close attempt counter
                    attempts = self._dsl_close_attempts.get(pos.symbol, 0) + 1
                    self._dsl_close_attempts[pos.symbol] = attempts
                    logging.warning(f"⚠️ {pos.symbol}: DSL SL submitted but position still open (attempt {attempts}/{self._max_close_attempts})")

                    if attempts >= self._max_close_attempts:
                        # Escalate: set cooldown and alert
                        self._dsl_close_attempt_cooldown[pos.symbol] = time.monotonic() + self._close_cooldown_seconds
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
                logging.warning(f"⚠️ {pos.symbol}: DSL SL order rejected — keeping in tracker")
                return  # Don't remove from tracker, will retry next tick
            self._log_outcome(pos, price, f"dsl_{action}")
            self._recently_closed[mid] = time.monotonic() + 300  # 5 min phantom guard
            self.tracker.remove_position(mid)
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
            await self.api.execute_tp(mid, pos.size, price, is_long)
        else:
            sl_success, sl_coi = await self.api.execute_sl(mid, pos.size, price, is_long)
            if sl_success:
                position_closed = await self._verify_position_closed(mid, pos.symbol)
                if not position_closed:
                    logging.warning(f"⚠️ {pos.symbol}: SL submitted but position still open — keeping in tracker")
                    return  # Don't remove from tracker, will retry next tick
            else:
                logging.warning(f"⚠️ {pos.symbol}: SL order rejected — keeping in tracker")
                return  # Don't remove from tracker, will retry next tick

        self._log_outcome(pos, price, action)
        self._recently_closed[mid] = time.monotonic() + 300  # 5 min phantom guard
        self.tracker.remove_position(mid)

        # Clear pending sync set at end of tick
        self._pending_sync.clear()

    def _shutdown(self):
        logging.info("Shutdown requested...")
        self.running = False


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
