#!/usr/bin/env python3
"""Integration test: BTC long with slippage buffer.

Skipped in CI unless LIGHTER_API_PRIVATE_KEY is set.
"""
import asyncio
import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("LIGHTER_API_PRIVATE_KEY"),
    reason="Requires LIGHTER_API_PRIVATE_KEY env var",
)


async def _place_order_with_slippage():
    import lighter
    from lighter import SignerClient, Configuration, ApiClient, OrderApi

    PROXY_USER = os.environ.get("LIGHTER_PROXY_USER", "")
    PROXY_PASS = os.environ.get("LIGHTER_PROXY_PASS", "")
    PROXY_HOST = os.environ.get("LIGHTER_PROXY_HOST", "64.137.96.74")
    PROXY_PORT = os.environ.get("LIGHTER_PROXY_PORT", "6641")
    PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}/" if PROXY_USER else None
    URL = "https://mainnet.zklighter.elliot.ai"
    ACCOUNT_INDEX = int(os.environ.get("LIGHTER_ACCOUNT_INDEX", "0"))
    API_KEY_INDEX = 3
    API_KEY_PRIVATE = os.environ.get("LIGHTER_API_PRIVATE_KEY", "")
    BTC_MARKET = 1

    config = Configuration(host=URL)
    config.proxy = PROXY
    client = ApiClient(configuration=config)
    order_api = OrderApi(client)

    ob = await order_api.order_book_orders(BTC_MARKET, 1)
    best_ask_str = ob.asks[0].price
    best_ask = int(best_ask_str.replace(".", ""))
    price_with_slippage = int(best_ask * 1.005)

    btc_amount = 15.0 / float(best_ask_str)
    base_amount = round(btc_amount * 100000)

    signer_config = Configuration(host=URL)
    signer_config.proxy = PROXY
    signer = SignerClient(
        url=URL,
        account_index=ACCOUNT_INDEX,
        api_private_keys={API_KEY_INDEX: API_KEY_PRIVATE},
    )
    signer.api_client = ApiClient(configuration=signer_config)
    signer.tx_api = lighter.TransactionApi(signer.api_client)
    signer.order_api = lighter.OrderApi(signer.api_client)

    client_order_index = int(time.time()) % 100000

    try:
        create_order, resp, err = await signer.create_market_order(
            market_index=BTC_MARKET,
            client_order_index=client_order_index,
            base_amount=base_amount,
            avg_execution_price=price_with_slippage,
            is_ask=False,
        )
        assert err is None, f"Order error: {err}"

        # Verify position
        await asyncio.sleep(2)
        account_api = lighter.AccountApi(client)
        result = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
        has_position = any(
            p.position and float(p.position) != 0
            for acc in result.accounts
            for p in acc.positions
        )
        assert has_position, "No position found after order"
    finally:
        await client.close()
        await signer.api_client.close()


def test_place_order_with_slippage():
    asyncio.run(_place_order_with_slippage())
