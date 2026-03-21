#!/usr/bin/env python3
"""Close existing position, set leverage to 10x, open ~$150 notional BTC long."""
import asyncio
import os
import time
import lighter
from lighter import SignerClient, Configuration, ApiClient, AccountApi, OrderApi

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
TARGET_NOTIONAL = 150.0  # USD notional value

async def main():
    # Setup
    config = Configuration(host=URL)
    config.proxy = PROXY
    client = ApiClient(configuration=config)
    order_api = OrderApi(client)
    account_api = AccountApi(client)

    signer = SignerClient(
        url=URL,
        account_index=ACCOUNT_INDEX,
        api_private_keys={API_KEY_INDEX: API_KEY_PRIVATE},
    )
    signer_config = Configuration(host=URL)
    signer_config.proxy = PROXY
    signer.api_client = ApiClient(configuration=signer_config)
    signer.tx_api = lighter.TransactionApi(signer.api_client)
    signer.order_api = lighter.OrderApi(signer.api_client)

    # Step 1: Check current position
    result = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
    current_pos = 0.0
    for acc in result.accounts:
        balance = float(acc.collateral)
        print(f"Balance: ${balance:.2f}")
        print(f"Available: ${acc.available_balance}")
        for p in acc.positions:
            if p.market_id == BTC_MARKET and float(p.position) != 0:
                current_pos = float(p.position)
                print(f"Current BTC position: {current_pos} (entry: {p.avg_entry_price})")

    # Step 2: Close existing position if any
    if current_pos > 0:
        print(f"\nClosing {current_pos} BTC position...")
        ob = await order_api.order_book_orders(BTC_MARKET, 1)
        best_bid = ob.bids[0].price
        price_int = int(best_bid.replace(".", ""))
        # Price with 0.5% buffer for fill
        price_buf = int(price_int * 0.995)
        
        size = round(current_pos * 100000)  # size_decimals=5
        cidx = int(time.time()) % 100000
        
        create_order, resp, err = await signer.create_market_order(
            market_index=BTC_MARKET,
            client_order_index=cidx,
            base_amount=size,
            avg_execution_price=price_buf,
            is_ask=True,  # SELL to close long
        )
        if err:
            print(f"Close ERROR: {err}")
        else:
            print(f"Close SUCCESS: tx={resp.tx_hash if hasattr(resp, 'tx_hash') else resp}")
        
        await asyncio.sleep(3)

    # Step 3: Get fresh price and balance
    result = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
    for acc in result.accounts:
        balance = float(acc.collateral)
        print(f"\nBalance after close: ${balance:.2f}")
    
    ob = await order_api.order_book_orders(BTC_MARKET, 1)
    best_ask_str = ob.asks[0].price
    best_ask = int(best_ask_str.replace(".", ""))
    print(f"BTC ask: {best_ask_str}")

    # Step 4: Calculate new position size
    btc_price = float(best_ask_str)
    btc_amount = TARGET_NOTIONAL / btc_price
    base_amount = round(btc_amount * 100000)
    margin_needed = TARGET_NOTIONAL / 10.0  # 10x leverage
    print(f"\nTarget: ${TARGET_NOTIONAL} notional at {btc_price:.1f} = {btc_amount:.6f} BTC")
    print(f"base_amount: {base_amount} units ({btc_amount:.6f} BTC)")
    print(f"Margin needed (10x): ${margin_needed:.2f}")
    
    if margin_needed > balance * 0.95:
        # Can't do 10x, use what we have with more leverage
        actual_leverage = balance * 0.95 / margin_needed * 10
        # Recalc with available
        max_notional = balance * 0.95 * actual_leverage
        btc_amount = max_notional / btc_price
        base_amount = round(btc_amount * 100000)
        print(f"Adjusted: max notional=${max_notional:.2f}, base={base_amount}")

    # Step 5: Open new position
    # Price with 0.5% slippage buffer
    price_buf = int(best_ask * 1.005)
    cidx = int(time.time()) % 100000
    print(f"\nPlacing: BUY {base_amount} units BTC-PERP at limit {price_buf}")
    
    create_order, resp, err = await signer.create_market_order(
        market_index=BTC_MARKET,
        client_order_index=cidx,
        base_amount=base_amount,
        avg_execution_price=price_buf,
        is_ask=False,
    )
    if err:
        print(f"Order ERROR: {err}")
    else:
        tx = resp.tx_hash if hasattr(resp, 'tx_hash') else resp
        print(f"Order SUCCESS! tx={tx}")

    # Step 6: Verify
    await asyncio.sleep(3)
    result = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
    for acc in result.accounts:
        print(f"\nFinal balance: ${acc.collateral}")
        print(f"Available: ${acc.available_balance}")
        for p in acc.positions:
            if p.market_id == BTC_MARKET and float(p.position) != 0:
                notional = float(p.position_value) if hasattr(p, 'position_value') and p.position_value else float(p.position) * float(p.avg_entry_price)
                print(f"Position: {p.position} BTC @ {p.avg_entry_price} (${notional:.2f} notional)")

    await client.close()
    await signer.api_client.close()

asyncio.run(main())
