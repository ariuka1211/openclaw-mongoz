#!/usr/bin/env python3
"""Open a market position on Lighter.

Usage: python3 open_position.py <symbol> <side> <size_usdc> [leverage]
  symbol: BTC, ETH, etc.
  side: long, short
  size_usdc: notional value in USDC
  leverage: default 5
"""
import asyncio
import sys
import time
import os
sys.path.insert(0, os.path.dirname(__file__))
from helpers import load_config, make_api_client, make_signer
import lighter

MARKET_MAP = {"BTC": 1, "BTC-PERP": 1, "ETH": 2, "ETH-PERP": 2}

async def main():
    if len(sys.argv) < 4:
        print("Usage: open_position.py <symbol> <side> <size_usdc> [leverage]")
        print("  e.g.: open_position.py BTC long 15 5")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    side = sys.argv[2].lower()
    size_usdc = float(sys.argv[3])
    leverage = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    is_ask = side == "short"

    market_id = MARKET_MAP.get(symbol)
    if not market_id:
        print(f"❌ Unknown symbol: {symbol}. Use: {', '.join(MARKET_MAP.keys())}")
        sys.exit(1)

    cfg = load_config()
    client = make_api_client(cfg)
    order_api = lighter.OrderApi(client)
    signer = await make_signer(cfg)

    # Get price
    ob = await order_api.order_book_orders(market_id, 1)
    if is_ask:
        best_price_str = ob.bids[0].price  # selling at bid
        price_buf = int(int(best_price_str.replace(".", "")) * 0.995)  # 0.5% slippage for fills
    else:
        best_price_str = ob.asks[0].price  # buying at ask
        price_buf = int(int(best_price_str.replace(".", "")) * 1.005)

    price_float = float(best_price_str)
    print(f"📊 {symbol} best {'bid' if is_ask else 'ask'}: ${price_float:,.2f}")

    # Calculate size
    coin_amount = size_usdc / price_float
    base_amount = round(coin_amount * 100000)  # size_decimals=5

    if base_amount == 0:
        print(f"❌ Size too small: {coin_amount:.8f} {symbol}")
        sys.exit(1)

    margin = size_usdc / leverage
    print(f"📈 {'SELL' if is_ask else 'BUY'} {base_amount} units {symbol}-PERP")
    print(f"   Notional: ${size_usdc:.2f} | Leverage: {leverage}x | Margin: ${margin:.2f}")

    cidx = int(time.time()) % 100000
    try:
        create_order, resp, err = await signer.create_market_order(
            market_index=market_id,
            client_order_index=cidx,
            base_amount=base_amount,
            avg_execution_price=price_buf,
            is_ask=is_ask,
        )
        if err:
            print(f"❌ Order failed: {err}")
            sys.exit(1)
        tx = resp.tx_hash if hasattr(resp, 'tx_hash') else resp
        print(f"✅ Order placed! tx={tx}")
    except Exception as e:
        print(f"❌ Exception: {e}")
        sys.exit(1)

    await client.close()
    await signer.api_client.close()

asyncio.run(main())
