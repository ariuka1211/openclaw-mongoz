#!/usr/bin/env python3
"""Close a position on Lighter.

Usage: python3 close_position.py <symbol>
  symbol: BTC, ETH, etc.
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
    if len(sys.argv) < 2:
        print("Usage: close_position.py <symbol>")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    market_id = MARKET_MAP.get(symbol)
    if not market_id:
        print(f"❌ Unknown symbol: {symbol}")
        sys.exit(1)

    cfg = load_config()
    client = make_api_client(cfg)
    account_api = lighter.AccountApi(client)
    order_api = lighter.OrderApi(client)
    signer = await make_signer(cfg)

    # Find current position
    result = await account_api.account(by="index", value=str(cfg["account_index"]))
    pos_size = 0.0
    entry_price = 0.0
    for acc in result.accounts:
        for p in acc.positions:
            if p.market_id == market_id and float(p.position) != 0:
                pos_size = float(p.position)
                entry_price = float(p.avg_entry_price) if p.avg_entry_price else 0
                break

    if pos_size == 0:
        print(f"📌 No open {symbol} position to close")
        await client.close()
        return

    is_long = pos_size > 0
    side = "LONG" if is_long else "SHORT"
    abs_size = abs(pos_size)
    print(f"📊 Found {side} position: {abs_size} {symbol} @ ${entry_price:,.2f}")

    # Get price
    ob = await order_api.order_book_orders(market_id, 1)
    if is_long:
        best_price_str = ob.bids[0].price  # selling to close
        price_buf = int(int(best_price_str.replace(".", "")) * 0.995)
    else:
        best_price_str = ob.asks[0].price  # buying to close
        price_buf = int(int(best_price_str.replace(".", "")) * 1.005)

    # Calculate close size
    base_amount = round(abs_size * 100000)
    cidx = int(time.time()) % 100000

    print(f"📉 Closing {side}: SELL" if is_long else f"📉 Closing {side}: BUY")
    print(f"   Amount: {base_amount} units @ ~${float(best_price_str):,.2f}")

    try:
        create_order, resp, err = await signer.create_market_order(
            market_index=market_id,
            client_order_index=cidx,
            base_amount=base_amount,
            avg_execution_price=price_buf,
            is_ask=is_long,  # sell to close long, buy to close short
        )
        if err:
            print(f"❌ Close failed: {err}")
            sys.exit(1)
        tx = resp.tx_hash if hasattr(resp, 'tx_hash') else resp
        print(f"✅ Position closed! tx={tx}")
    except Exception as e:
        print(f"❌ Exception: {e}")
        sys.exit(1)

    await client.close()
    await signer.api_client.close()

asyncio.run(main())
