#!/usr/bin/env python3
"""
BTC Grid Bot — Lighter.xyz API wrapper.

Uses ProxySignerClient (proven working pattern from V1 bot).
Methods: equity, price, open orders, limit orders, cancel, close.
"""

import asyncio
import logging
import os
import sys
import time

import lighter
from dotenv import load_dotenv
from lighter.signer_client import get_signer

# Patch aiohttp for SOCKS5 proxy support
import importlib.util
_proxy_patch_path = '/root/.openclaw/workspace/projects/autopilot-trader/bot/api/proxy_patch.py'
_spec = importlib.util.spec_from_file_location("__proxy_patch", _proxy_patch_path)
_proxy_patch_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_proxy_patch_module)

load_dotenv('/root/.openclaw/workspace/.env')

# ── Constants ──────────────────────────────────────────────────

PROXY_URL = "socks5://14a5aedaa4832:4d27d95d7c@92.61.99.50:12324/"
URL = "https://mainnet.zklighter.elliot.ai"
BTC_MARKET_ID = 1
BTC_SIZE_DECIMALS = None  # fetched from exchange at init
BTC_PRICE_DECIMALS = None  # fetched from exchange at init
ACCOUNT_INDEX = 719758
API_KEY_INDEX = 3

LIGHTER_API_PRIVATE_KEY = os.getenv("LIGHTER_API_PRIVATE_KEY")


# ── ProxySignerClient ─────────────────────────────────────────

class ProxySignerClient(lighter.SignerClient):
    """SignerClient that routes through SOCKS5 proxy."""
    def __init__(self_inner, url, account_index, api_private_keys):
        signer_config = lighter.Configuration(host=url)
        signer_config.proxy = PROXY_URL
        self_inner.url = url
        self_inner.chain_id = 304
        self_inner.validate_api_private_keys(api_private_keys)
        self_inner.api_key_dict = api_private_keys
        self_inner.account_index = account_index
        self_inner.signer = get_signer()
        self_inner.api_client = lighter.ApiClient(configuration=signer_config)
        self_inner.tx_api = lighter.TransactionApi(self_inner.api_client)
        self_inner.order_api = lighter.OrderApi(self_inner.api_client)
        self_inner.nonce_manager = lighter.nonce_manager.nonce_manager_factory(
            nonce_manager_type=lighter.nonce_manager.NonceManagerType.OPTIMISTIC,
            account_index=account_index,
            api_client=self_inner.api_client,
            api_keys_list=list(api_private_keys.keys()),
        )
        for ki in api_private_keys.keys():
            self_inner.create_client(ki)


# ── LighterAPI ─────────────────────────────────────────────────

class LighterAPI:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._client = None
        self._account_api = None
        self._order_api = None
        self._signer = None
        self._size_decimals = None
        self._price_decimals = None

        # Monotonic counter for client order indices
        self._client_order_index = int(time.time() * 1000)

    async def _fetch_market_decimals(self):
        """Fetch supported_size_decimals and supported_price_decimals from exchange."""
        await self._ensure_client()
        if self._size_decimals is None:
            books = await self._order_api.order_books()
            for b in books.order_books:
                if b.market_id == BTC_MARKET_ID:
                    self._size_decimals = int(b.supported_size_decimals)
                    self._price_decimals = int(b.supported_price_decimals)
                    logging.info(f"Market decimals: size={self._size_decimals}, price={self._price_decimals}")
                    return
        raise RuntimeError(f"Market {BTC_MARKET_ID} not found in order_books")

    async def _ensure_client(self):
        """Lazy init of read-only API clients."""
        if self._client is not None:
            return
        config = lighter.Configuration(host=URL)
        config.proxy = PROXY_URL
        self._client = lighter.ApiClient(config)
        self._account_api = lighter.AccountApi(self._client)
        self._order_api = lighter.OrderApi(self._client)

    async def _ensure_signer(self):
        """Lazy init of ProxySignerClient."""
        if self._signer is not None:
            return
        if not LIGHTER_API_PRIVATE_KEY:
            raise RuntimeError("LIGHTER_API_PRIVATE_KEY not set in .env")
        self._signer = ProxySignerClient(
            url=URL,
            account_index=ACCOUNT_INDEX,
            api_private_keys={API_KEY_INDEX: LIGHTER_API_PRIVATE_KEY},
        )

    def _get_auth_token(self) -> str:
        """Generate an auth token for authenticated API queries."""
        auth, err = self._signer.create_auth_token_with_expiry(api_key_index=API_KEY_INDEX)
        if err:
            raise RuntimeError(f"Auth token error: {err}")
        return auth

    @staticmethod
    def _to_lighter_amount(value: float, decimals: int) -> int:
        """Convert float to Lighter's integer representation."""
        return int(round(abs(value) * (10 ** decimals)))

    def _next_client_order_index(self) -> int:
        self._client_order_index += 1
        return self._client_order_index % (2 ** 32)

    # ── Public API ──────────────────────────────────────────────

    async def get_equity(self) -> float:
        """Return account USDC equity (collateral). Initializes market decimals."""
        await self._ensure_client()
        if self._size_decimals is None:
            await self._fetch_market_decimals()
        result = await self._account_api.account(
            by="index",
            value=str(ACCOUNT_INDEX),
            _request_timeout=30,
        )
        for account in result.accounts:
            return float(account.collateral)
        raise RuntimeError("No accounts found")

    async def get_btc_price(self) -> float:
        """Return current BTC mark price from recent trades."""
        await self._ensure_client()
        trades = await self._order_api.recent_trades(market_id=BTC_MARKET_ID, limit=1)
        if trades.trades:
            return float(trades.trades[0].price)
        raise RuntimeError("No recent trades for BTC")

    async def get_open_orders(self) -> list[dict]:
        """Return list of open orders for BTC market.

        Each dict: {order_id, price, size, side}
        """
        await self._ensure_signer()
        auth = self._get_auth_token()

        result = await self._signer.order_api.account_active_orders(
            account_index=ACCOUNT_INDEX,
            market_id=BTC_MARKET_ID,
            auth=auth,
            _request_timeout=30,
        )

        orders = []
        if hasattr(result, 'orders') and result.orders:
            for o in result.orders:
                orders.append({
                    "order_id": str(o.order_id),
                    "price": float(o.price),
                    "size": float(o.remaining_base_amount),
                    "side": "sell" if o.is_ask else "buy",
                })
        return orders

    async def place_limit_order(self, side: str, price: float, size: float) -> dict:
        """Place a limit order on BTC.

        Returns {"order_id": str, "price": float, "side": str, "size": float}.
        """
        await self._ensure_signer()

        base_amount = self._to_lighter_amount(size, self._size_decimals)
        price_int = self._to_lighter_amount(price, self._price_decimals)
        logging.info(f"Placing order: side={side}, price={price}, size={size}, base_amount={base_amount}, price_int={price_int}")
        is_ask = (side == "sell")

        result = await self._signer.create_order(
            market_index=BTC_MARKET_ID,
            client_order_index=self._next_client_order_index(),
            base_amount=base_amount,
            price=price_int,
            is_ask=is_ask,
            order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
            time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
        )
        logging.info(f"Order placement result: {result}")

        if isinstance(result, tuple):
            if len(result) >= 3 and result[2] is not None:
                raise RuntimeError(f"Order rejected: {result[2]}")
        
        # Wait for exchange to process and make order visible
        # Try up to 10 times with 2 second delay between attempts
        for attempt in range(10):
            await asyncio.sleep(2.0)  # 2 second delay
            try:
                orders = await self.get_open_orders()
                # Find the order that matches side and price (it should be the one we just placed)
                for o in orders:
                    if o["side"] == side and abs(o["price"] - price) < 0.01:
                        # Found our order
                        return {
                            "order_id": o["order_id"],
                            "price": price,
                            "side": side,
                            "size": size,
                        }
            except Exception:
                pass  # continue to next attempt
            logging.info(f"Attempt {attempt+1}: order not yet visible, retrying...")

        # If we still can't find the order after 3 attempts, fall back to client_order_index
        logging.warning("Could not retrieve real order_id after placement, using client_order_index")
        return {
            "order_id": str(self._client_order_index),
            "price": price,
            "side": side,
            "size": size,
        }

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order by order_id (order_index)."""
        await self._ensure_signer()

        try:
            order_index = int(order_id)
        except ValueError:
            return False

        result = await self._signer.cancel_order(
            market_index=BTC_MARKET_ID,
            order_index=order_index,
        )

        if isinstance(result, tuple):
            if len(result) >= 3 and result[2] is not None:
                logging.warning(f"Cancel rejected: {result[2]}")
                return False
            return True
        return False

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders for BTC market. Returns count cancelled."""
        orders = await self.get_open_orders()
        if not orders:
            return 0

        cancelled = 0
        for order in orders:
            ok = await self.cancel_order(order["order_id"])
            if ok:
                cancelled += 1
            await asyncio.sleep(0.1)  # avoid rate limits

        return cancelled

    async def get_btc_balance(self) -> float:
        """Return account BTC balance/position size in BTC units.

        Returns positive value for long position, 0 for no position.
        """
        await self._ensure_signer()
        auth = self._get_auth_token()

        try:
            result = await self._account_api.account(
                by="index",
                value=str(ACCOUNT_INDEX),
            )
            for account in result.accounts:
                if hasattr(account, 'positions') and account.positions:
                    for pos in account.positions:
                        if pos.market_id == BTC_MARKET_ID:
                            # Lighter `position` is a float string (e.g. "0.00198")
                            # `sign`: 1 for long, -1 for short
                            try:
                                pos_size = float(pos.position)
                            except (ValueError, TypeError):
                                pos_size = 0.0
                            if pos_size != 0:
                                sign = int(pos.sign) if pos.sign else 1
                                pos_size = pos_size * sign  # negative for shorts
                                return pos_size
                return 0.0
            raise RuntimeError("No accounts found")
        except Exception as e:
            logging.error(f"Failed to fetch BTC balance: {e}")
            return 0.0

    async def close(self):
        """Close all aiohttp sessions."""
        errors = []
        try:
            if self._client is not None:
                await self._client.close()
        except Exception as e:
            errors.append(f"main client: {e}")
        try:
            if self._signer and hasattr(self._signer, 'api_client') and self._signer.api_client is not None:
                await self._signer.api_client.close()
        except Exception as e:
            errors.append(f"signer client: {e}")
        if errors:
            logging.warning(f"API close errors: {'; '.join(errors)}")