# Lighter DEX Order Execution Research Findings

## Date: 2026-03-22

## Root Cause: Volume Quota Exhaustion (NOT an SDK bug)

The "didn't use volume quota" message is a **rate limit rejection**, not a successful order submission. The bot incorrectly logs it as success because `resp_code=200`.

### Evidence from bot.log
```
13:45:23 [INFO] ✅ SL market order submitted: resp_code=200, resp_msg={"ratelimit": "didn't use volume quota"}
13:50:34 [INFO] 📌 API POSITION CONFIRMED: LONG RESOLV  ← position still open after "successful" SL
```

The position remains open after the supposed "successful" stop loss execution. This confirms the order was **rejected** despite returning HTTP 200.

## How Lighter's Volume Quota Works

From https://apidocs.lighter.xyz/docs/volume-quota-program:

1. New accounts start with **1K quota** (transactions)
2. Every $5 of trading volume → +1 quota
3. Max stackable: 5,000,000 TX allowance (never expires)
4. Free SendTx every 15 seconds (doesn't consume quota)
5. **Shared across all sub-accounts** under the same L1 address
6. Orders that consume quota: CreateOrder, CancelAllOrders, ModifyOrder, CreateGroupedOrders
7. Cancels do NOT consume quota

When quota is exhausted, API returns `{"ratelimit": "didn't use volume quota"}` with HTTP 200 (not an error at the HTTP level, but the order is NOT submitted to the sequencer).

## How Market Orders Work on Lighter

From the SDK (signer_client.py), Lighter has 3 market order variants:

### 1. `create_market_order()` — Direct market order
```python
await signer.create_market_order(
    market_index=1,
    client_order_index=...,
    base_amount=...,
    avg_execution_price=worst_acceptable_price,  # THIS IS THE KEY PARAMETER
    is_ask=False,  # False = buying, True = selling
    reduce_only=False,
)
```
- Uses ORDER_TYPE_MARKET (1) + IOC (0)
- `avg_execution_price` = worst acceptable price (not a true "average")
- If price can't be matched, order is silently cancelled (status 12: Expired)

### 2. `create_market_order_limited_slippage()` — Best price + slippage limit
```python
await signer.create_market_order_limited_slippage(
    market_index=1,
    client_order_index=...,
    base_amount=...,
    max_slippage=0.02,  # 2%
    is_ask=False,
    reduce_only=True,
)
```
- Fetches best price, calculates acceptable_execution_price = best * (1 ± max_slippage)
- For buying: acceptable = best * (1 + max_slippage)
- For selling: acceptable = best * (1 - max_slippage)
- Also uses IOC under the hood

### 3. `create_market_order_if_slippage()` — Pre-checks order book
```python
await signer.create_market_order_if_slippage(...)
```
- Fetches 100 levels of order book
- Checks if the entire order can fill within max_slippage
- Returns "Excessive slippage" or "Cannot be sure slippage will be acceptable due to the high size" if conditions aren't met
- **Most reliable** for ensuring execution

## Key SDK Insights

### Order Type = Market + IOC
All 3 "market order" methods use:
- `ORDER_TYPE_MARKET` (1)
- `ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL` (0) 
- `order_expiry = 0`

### Price Interpretation for Taker Orders
From the official docs:
> "when specifying a price for a taker order, that is to be interpreted as the worst price you're willing to accept - if the sequencer cannot offer you an equal or better price, the order is cancelled."

### reduce_only Orders
- `reduce_only=True`: Order only executes if it reduces position size
- Error `AppReduceOnlyIncreasesPosition (21732)` if it would increase position
- `reduce_only=False` is used for opening positions

### Error: "ReduceOnlyIncreasesPosition" (21732)
This error occurs when:
- A `reduce_only=True` order would increase the position
- The position was already closed but the bot still tries to close it
- Position size changed between order creation and execution

## Other Relevant Errors from Lighter

Order cancellation reasons (status codes):
- `CanceledOrder_ReduceOnly (6)` — reduce only order couldn't be filled
- `CanceledOrder_TooMuchSlippage (9)` — slippage exceeded
- `CanceledOrder_NotEnoughLiquidity (10)` — not enough liquidity
- `CanceledOrder_Expired (12)` — IOC order couldn't fill immediately
- `CanceledOrder_InvalidBalance (16)` — invalid balance

## Fixes Required

### 1. Fix the Volume Quota Issue
- **Increase volume quota** by trading more volume (each $5 = +1 quota)
- **Add delays** between order submissions (at least 15 seconds for free TX)
- **Use the free interval**: every 15 seconds, one TX is free
- **Batch operations** where possible (SendTxBatch)
- **Use LIMIT orders** instead of market orders — limit orders placed at the best price often get maker fees (positive) instead of taker fees

### 2. Fix Error Handling
The bot currently treats `resp_code=200` as success. But the `msg` field contains the actual result:
```python
# Current (broken):
if resp is not None:
    resp_code = getattr(resp, 'code', None)  # 200 = always OK
    resp_msg = getattr(resp, 'msg', None)    # This contains the actual status!

# Should check:
if "didn't use volume quota" in str(resp_msg):
    # Order was NOT submitted - treat as failure
```

### 3. Better Market Order Method
Use `create_market_order_if_slippage` instead of `create_market_order_limited_slippage` for stop losses — it pre-checks the order book to ensure execution is possible.

### 4. Consider Limit Orders Instead of Market Orders
For a more reliable approach, use limit orders with IOC at prices slightly crossing the spread:
```python
# Instead of market order, use limit IOC
best_price = await signer.get_best_price(market_id, is_ask=selling)
# Add small buffer for immediate fill
price_with_buffer = best_price * (1 + 0.005 if buying else -0.005)
await signer.create_order(
    market_index=market_id,
    client_order_index=...,
    base_amount=...,
    price=price_with_buffer,
    is_ask=selling,
    order_type=ORDER_TYPE_LIMIT,  # 0, not MARKET
    time_in_force=ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,  # 0
    order_expiry=0,
    reduce_only=True,
)
```

## Lighter Order Types Reference

- `ORDER_TYPE_LIMIT (0)` — Standard limit order
- `ORDER_TYPE_MARKET (1)` — Market order (limit + IOC internally)
- `ORDER_TYPE_STOP_LOSS (2)` — Triggered stop loss
- `ORDER_TYPE_STOP_LOSS_LIMIT (3)` — Stop loss with limit price
- `ORDER_TYPE_TAKE_PROFIT (4)` — Take profit order
- `ORDER_TYPE_TAKE_PROFIT_LIMIT (5)` — Take profit with limit price
- `ORDER_TYPE_TWAP (6)` — Time-weighted average price

Time-in-Force:
- `ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL (0)` — Fill immediately or cancel
- `ORDER_TIME_IN_FORCE_GOOD_TILL_TIME (1)` — Active until expiry
- `ORDER_TIME_IN_FORCE_POST_ONLY (2)` — Maker order only (rejected if taker)

## Sources
- Official API docs: https://apidocs.lighter.xyz/docs/trading
- Volume Quota: https://apidocs.lighter.xyz/docs/volume-quota-program  
- Rate Limits: https://apidocs.lighter.xyz/docs/rate-limits
- SDK source: https://github.com/elliottech/lighter-python
- Error codes: https://apidocs.lighter.xyz/docs/data-structures-constants-and-errors
