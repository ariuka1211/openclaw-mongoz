#!/usr/bin/env python3

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/lighter_api.py', 'r') as f:
    lines = f.readlines()

# Find the line where place_limit_order starts
start_idx = None
for i, line in enumerate(lines):
    if 'async def place_limit_order(self, side: str, price: float, size: float) -> dict:' in line:
        start_idx = i
        break

if start_idx is None:
    print("Could not find place_limit_order method")
    exit(1)

# Find the end of the method (the next method definition or end of class)
end_idx = None
for i in range(start_idx + 1, len(lines)):
    if lines[i].strip().startswith('async def ') or lines[i].strip().startswith('def '):
        end_idx = i
        break

if end_idx is None:
    end_idx = len(lines)

# New method content
new_method = '''    async def place_limit_order(self, side: str, price: float, size: float) -> dict:
        """Place a limit order on BTC.

        Returns {"order_id": str, "price": float, "side": str, "size": float}.
        """
        await self._ensure_signer()

        base_amount = self._to_lighter_amount(size, self._size_decimals)
        price_int = self._to_lighter_amount(price, self._price_decimals)
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

        if isinstance(result, tuple):
            if len(result) >= 3 and result[2] is not None:
                raise RuntimeError(f"Order rejected: {result[2]}")
        
        # Wait a moment and then fetch the order to get the real order_id
        # The exchange assigns order_index server-side; we need to retrieve it
        await asyncio.sleep(0.1)  # small delay to allow exchange to process
        
        # Try to get the order we just placed by matching side/price
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
            # If we can't find the order, fall back to client_order_index
            logging.warning("Could not retrieve real order_id after placement, using client_order_index")
            return {
                "order_id": str(self._client_order_index),
                "price": price,
                "side": side,
                "size": size,
            }
        
        raise RuntimeError(f"Failed to place order: order not found after creation")
'''

# Replace the lines
lines[start_idx:end_idx] = [new_method + '\n']

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/lighter_api.py', 'w') as f:
    f.writelines(lines)

print(f"Replaced place_limit_order method (lines {start_idx+1} to {end_idx})")