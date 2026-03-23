# Lighter SDK Order Submission Methods — Analysis Report

## Summary of Findings

**Root Cause: The `{"ratelimit": "didn't use volume quota"}` message is NOT an order failure.** It's a rate-limiting indicator from the Lighter server. The orders ARE being accepted (code=200) and ARE executing, but with a delay (10-30 seconds observed). The bot misclassifies these as successes when they're actually rate-limited, and the position verification system is broken (`_check_active_orders` doesn't exist on `LighterCopilot`).

---

## ALL Order Submission Methods Available in `SignerClient`

### 1. `create_order()` — The Foundation
**Parameters:**
- `market_index` — Market ID
- `client_order_index` — Unique order ID
- `base_amount` — Integer-scaled base amount
- `price` — Integer-scaled price (ACTS AS WORST ACCEPTABLE PRICE for market orders)
- `is_ask` — True=sell, False=buy
- `order_type` — 0=LIMIT, 1=MARKET, 2=STOP_LOSS, 3=STOP_LOSS_LIMIT, 4=TAKE_PROFIT, 5=TAKE_PROFIT_LIMIT, 6=TWAP
- `time_in_force` — 0=IOC, 1=GTT, 2=POST_ONLY
- `reduce_only` — False or True
- `trigger_price` — For stop/take-profit orders (default 0)
- `order_expiry` - Expiry timestamp (default -1 = 28 days, 0 = IOC)

**What it does:** Signs the order via C library (`SignCreateOrder`), sends the signed transaction to `/api/v1/sendTx`.

### 2. `create_market_order()` — Simplest Market Order
**Parameters:**
- `market_index`, `client_order_index`, `base_amount`
- `avg_execution_price` — THE WORST PRICE YOU'LL ACCEPT (passed as `price` to `create_order`)
- `is_ask`, `reduce_only`

**What it does:** Calls `create_order()` with:
- `order_type=ORDER_TYPE_MARKET` (1)
- `time_in_force=ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL` (0)
- `order_expiry=DEFAULT_IOC_EXPIRY` (0)
- `price=avg_execution_price`

**Key insight:** The `avg_execution_price` is the MAX price for a buy or MIN price for a sell. It's a LIMIT on the market order. If you set it too tight, the order may not fill immediately.

### 3. `create_market_order_limited_slippage()` — Current Method Used
**Parameters:**
- `market_index`, `client_order_index`, `base_amount`
- `max_slippage` — e.g., 0.02 for 2%
- `is_ask`, `reduce_only`
- `ideal_price` — Optional, fetched from order book if None

**What it does:**
1. Gets `ideal_price` via `get_best_price()` (best opposing price from order book)
2. Calculates `acceptable_execution_price = round(ideal_price * (1 + max_slippage * (-1 if is_ask else 1)))`
3. Calls `create_order()` with that price

**For closing a long (is_ask=True, selling):**
- `ideal_price` = best bid from order book
- `acceptable_price = ideal_price * (1 - max_slippage)` = WORSE THAN BEST BID by max_slippage

**For closing a short (is_ask=False, buying):**
- `ideal_price` = best ask from order book  
- `acceptable_price = ideal_price * (1 + max_slippage)` = WORSE THAN BEST ASK by max_slippage

### 4. `create_market_order_if_slippage()` — Pre-Validated Market Order
**Parameters:** Same as above.

**What it does (SAFER):**
1. Fetches 100 levels of the order book
2. Simulates the full execution by walking the book
3. Checks if `potential_execution_price` is within acceptable slippage
4. Checks if there's enough liquidity (`matched_size >= base_amount`)
5. ONLY THEN submits the order

**Returns error "Excessive slippage" or "Cannot be sure slippage will be acceptable due to the high size" if pre-checks fail.**

### 5. `create_market_order_quote_amount()` — Quote-Denominated Market Order
**Parameters:**
- `quote_amount` — USD amount (not base amount)
- `max_slippage`, `is_ask`, `reduce_only`
- `ideal_price` — Optional

**What it does:** Converts quote to base using simulated execution, then submits with slippage-limited price.

### 6. `create_tp_order()` / `create_sl_order()` — Trigger Orders
Create take-profit or stop-loss trigger orders with a `trigger_price`.

### 7. `create_grouped_orders()` — Batch Order Submission
Submit multiple orders atomically with grouping (OCO, OTO, OCO-OTO).

---

## What's NOT in the SDK

- No dedicated `close_position()` method
- No `submit_order()` or `place_order()` — only `create_order()` and `send_tx()`
- The `send_tx()` method does NOT pass `price_protection` to the API (available in API but not used)

---

## The "didn't use volume quota" Issue

### What It Means
- **code=200** = Transaction accepted by the server
- **message=`{"ratelimit": "didn't use volume quota"}`** = The order is being rate-limited. The server has a "volume quota" system for rate limiting. This message means the order was accepted but is being held in a queue.
- **`volume_quota_remaining=None`** = The volume quota system doesn't return a remaining value for this transaction type.

### Evidence from Logs
```
15:32:18 [INFO] ✅ SL order submitted: code=200, msg={"ratelimit": "didn't use volume quota"}, tx_hash=c46789856...
15:32:26 [WARNING] ⚠️ WLFI: error verifying closure (attempt 1): 'LighterCopilot' object has no attribute '_check_active_orders'
...
15:34:38 [INFO] ✅ WLFI: position closure verified (attempt 4, after 20s)
```

**The position DID close after ~20 seconds.** The order was accepted and executed, just with rate-limiting delay.

### Why It Happens
Lighter's matching engine rate-limits order submissions. The volume quota system is designed to prevent spam. New accounts or accounts with low trading volume get lower quotas. The order IS submitted to the matching engine but execution is delayed.

---

## What's Wrong with Current Usage

### Issue 1: Misclassification of Rate-Limited Orders as Successes
The bot logs `code=200` as "success" without checking the rate-limit message. Orders with `"didn't use volume quota"` should be logged as "rate-limited, pending execution" not "submitted successfully."

### Issue 2: Broken Position Verification
```
'LighterCopilot' object has no attribute '_check_active_orders'
```
The verification method doesn't exist, so the bot can't tell if a position actually closed.

### Issue 3: Rapid Re-submission Without Waiting
The bot re-submits orders rapidly (every ~8 seconds) without waiting for the previous rate-limited order to execute. This wastes the volume quota and creates more rate-limited orders.

### Issue 4: `price_protection` Not Used
The `send_tx` API supports an optional `price_protection` boolean parameter that the SDK's `send_tx` method doesn't pass. This could potentially improve execution.

---

## Recommended Changes

### 1. Use `create_market_order_if_slippage()` Instead
Switch from `create_market_order_limited_slippage()` to `create_market_order_if_slippage()` for closing positions. This method:
- Pre-checks the order book to verify the order CAN fill
- Returns clear errors if slippage is too high or liquidity is insufficient
- Prevents wasted transactions that get rate-limited

### 2. Add Rate-Limit Detection
Check for the rate-limit message and handle it properly:
```python
resp_msg = getattr(resp, 'message', None)
if resp_msg and 'ratelimit' in str(resp_msg).lower():
    logging.warning(f"⏳ Order rate-limited, will execute with delay: {resp_msg}")
    # Don't immediately retry — wait for execution
```

### 3. Fix Position Verification
Implement `_check_active_orders()` or use the existing `order_api.account_active_orders()` to verify if positions actually closed.

### 4. Add Backoff for Re-submissions
When a rate-limited order is detected, wait at least 30 seconds before attempting another close.

### 5. Consider Using `create_market_order()` Directly with Proper Price
For immediate execution, use `create_market_order()` with a wider slippage tolerance (e.g., 5%) and `reduce_only=True`. The key is setting `avg_execution_price` to be far enough from the current price to guarantee immediate fill.

---

## Code Changes Needed

**File: `bot.py` — `execute_sl()` method (line ~850)**

Change from:
```python
result = await self._signer.create_market_order_limited_slippage(...)
```

To:
```python
result = await self._signer.create_market_order_if_slippage(
    market_index=market_id,
    client_order_index=client_order_index,
    base_amount=base_amount,
    max_slippage=0.05,  # 5% slippage for guaranteed fill on close
    is_ask=is_long,
    reduce_only=True,
    ideal_price=best_price_int,
)
```

Or for the simplest approach, use `create_market_order()` directly with a generous price:
```python
# For closing a long (selling), set price well below best bid
worst_acceptable = int(best_price_int * 0.95)  # 5% below best bid
result = await self._signer.create_market_order(
    market_index=market_id,
    client_order_index=client_order_index,
    base_amount=base_amount,
    avg_execution_price=worst_acceptable,
    is_ask=is_long,
    reduce_only=True,
)
```

---

## Conclusion

The order submission methods themselves are working correctly. The issue is:

1. **Rate limiting** causes delays, not failures — but the bot treats them as successes
2. **Broken verification** means the bot can't confirm execution
3. **Rapid retries** compound the rate-limiting problem
4. **No pre-validation** means some orders get rate-limited when they shouldn't be submitted at all

The fix is:
- Use `create_market_order_if_slippage()` for pre-validation
- Properly detect and handle rate-limited orders
- Fix the position verification system
- Add backoff between retries
