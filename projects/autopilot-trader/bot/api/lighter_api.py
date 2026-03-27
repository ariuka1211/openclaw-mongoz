"""
Lighter.xyz DEX API wrapper.

Handles authentication, position queries, price feeds, order execution
(TP/SL/market), volume quota tracking, and state persistence.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import lighter

from config import BotConfig


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
        self._state_dir = Path(__file__).parent.parent / "state"
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
                        # Extract initial_margin_fraction and compute leverage from exchange
                        margin_fraction = float(pos.initial_margin_fraction) if hasattr(pos, 'initial_margin_fraction') and pos.initial_margin_fraction else 0
                        if margin_fraction > 0:
                            pos_leverage = round(min(100.0 / margin_fraction, self.cfg.dsl_leverage), 1)
                        else:
                            pos_leverage = self.cfg.dsl_leverage
                        sign = int(pos.sign) if hasattr(pos, 'sign') else 1
                        side = "long" if sign > 0 else "short"
                        positions.append({
                            "market_id": market_id,
                            "symbol": symbol,
                            "size": size,
                            "side": side,
                            "entry_price": entry,
                            "unrealized_pnl": unrealized_pnl,
                            "leverage": pos_leverage,
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

    async def get_market_leverage(self, market_id: int) -> float:
        """Get actual exchange leverage for a market from IMF (default_initial_margin_fraction).

        Uses order_books API to get market specs. Leverage = 1 / IMF (in decimal).
        IMF is in micro units (1,000,000 = 100%), so divide by 1,000,000.
        Falls back to cfg.dsl_leverage if API fails.
        """
        try:
            await self._ensure_client()
            books = await self._order_api.order_books(market_id=market_id)
            for book in books.order_books:
                if book.market_id == market_id and hasattr(book, 'default_initial_margin_fraction'):
                    imf_micro = float(book.default_initial_margin_fraction)
                    if imf_micro > 0:
                        # IMF is in micro units: 100,000 micro = 10% = 0.10 decimal
                        imf_decimal = imf_micro / 1_000_000
                        leverage = 1.0 / imf_decimal
                        return min(leverage, 100.0)  # cap at 100x for safety
                    return self.cfg.dsl_leverage
        except Exception as e:
            logging.warning(f"get_market_leverage(market={market_id}) failed: {e}")
        return self.cfg.dsl_leverage

    async def set_leverage(self, market_id: int, leverage: float) -> bool:
        """Set leverage cap on the exchange via IMF. Best-effort — may not work on all markets.

        Args:
            market_id: Market to set leverage for
            leverage: Desired leverage (e.g., 10.0 for 10x)

        Returns:
            True if set succeeds, False on unexpected failure. Exchange enforces
            its own market limits regardless.
        """
        await self._ensure_signer()
        from lighter.signer_client import SignerClient
        try:
            tx_info, api_response, error = await self._signer.update_leverage(
                market_index=market_id,
                margin_mode=SignerClient.CROSS_MARGIN_MODE,
                leverage=leverage,
            )
            if error:
                logging.warning(f"⚠️ set_leverage({market_id}, {leverage}x): {error}")
                return True  # Proceed — exchange enforces its own limits on orders

            # Log quota impact
            if api_response:
                quota_val, _ = self._extract_quota_from_response(api_response)
                if quota_val is not None:
                    self._update_quota_cache(quota_val)

            logging.info(f"✅ Leverage set: {leverage}x for market {market_id} (quota={self._volume_quota_remaining})")
            return True
        except Exception as e:
            logging.error(f"❌ set_leverage({market_id}, {leverage}x) exception: {e}")
            return False  # Network/auth error — don't proceed

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
