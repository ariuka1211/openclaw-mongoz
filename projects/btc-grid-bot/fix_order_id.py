#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/lighter_api.py', 'r') as f:
    content = f.read()

# New place_limit_order implementation that retrieves real order ID
new_place_limit = '''    async def place_limit_order(self, side: str, price: float, size: float) -> dict:
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
        
        # Try to get the order we just placed by matching client order index
        # We'll fetch all open orders and find the one with matching side/price
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

# Replace the old place_limit_order method
# Find the method definition and replace everything until the next method
pattern = r'(    async def place_limit_order\(self, side: str, price: float, size: float\) -> dict:.*?raise RuntimeError\(f"Unexpected response type: {type\(result\)}}"\)\n)'
replacement = new_place_limit + '\n'

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/lighter_api.py', 'w') as f:
    f.write(content)

print("Updated place_limit_order to retrieve real order_id")