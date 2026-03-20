#!/usr/bin/env python3
"""Get account status: balance, positions, current prices."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from helpers import load_config, make_api_client, make_signer
import lighter

async def main():
    cfg = load_config()
    client = make_api_client(cfg)
    account_api = lighter.AccountApi(client)
    order_api = lighter.OrderApi(client)

    # Account info
    try:
        result = await account_api.account(by="index", value=str(cfg["account_index"]))
        for acc in result.accounts:
            balance = float(acc.collateral) if acc.collateral else 0
            print(f"💰 Account: {cfg['account_index']}")
            print(f"   Balance: ${balance:.2f} USDC")
            print()

            if acc.positions:
                print("📊 Open Positions:")
                print(f"   {'Side':<6} {'Symbol':<10} {'Size':<12} {'Entry':<12}")
                print(f"   {'─'*6} {'─'*10} {'─'*12} {'─'*12}")
                for pos in acc.positions:
                    size = float(pos.position) if hasattr(pos, 'position') and pos.position else 0
                    if size == 0:
                        continue
                    side = "LONG" if size > 0 else "SHORT"
                    entry = float(pos.avg_entry_price) if hasattr(pos, 'avg_entry_price') and pos.avg_entry_price else 0
                    market_id = pos.market_id if hasattr(pos, 'market_id') else 0
                    
                    # Get symbol
                    symbol = f"MKT{market_id}"
                    try:
                        books = await order_api.order_books()
                        for b in books.order_books:
                            if b.market_id == market_id:
                                symbol = b.symbol
                                break
                    except:
                        pass

                    # Get current price
                    price_str = "?"
                    try:
                        trades = await order_api.recent_trades(market_id=market_id, limit=1)
                        if trades.trades:
                            current = float(trades.trades[0].price)
                            pnl_pct = ((current - entry) / entry * 100) if side == "LONG" else ((entry - current) / entry * 100)
                            price_str = f"${current:,.2f} ({pnl_pct:+.2f}%)"
                    except:
                        pass

                    print(f"   {side:<6} {symbol:<10} {abs(size):<12} ${entry:<11,.2f} → {price_str}")
            else:
                print("📊 No open positions")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    await client.close()

asyncio.run(main())
