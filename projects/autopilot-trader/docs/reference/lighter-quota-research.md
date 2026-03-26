# Lighter DEX Volume Quota Research & Bot Strategy Plan

## Date: 2026-03-22

---

## 1. How the Volume Quota System Works

**Source:** [apidocs.lighter.xyz/docs/volume-quota-program](https://apidocs.lighter.xyz/docs/volume-quota-program)

### Core Mechanism

Volume quota is a **separate rate-limiting system** for `sendTx` and `sendTxBatch` that only applies to **Premium accounts**. Standard accounts are bound to the general 60 requests/minute rate limit instead.

**What consumes quota:**
- `L2CreateOrder` (type 14)
- `L2CancelAllOrders` (type 16)
- `L2ModifyOrder` (type 17)
- `L2CreateGroupedOrders` (type 28)

**What does NOT consume quota:**
- `L2CancelOrder` (individual cancel) — does NOT consume quota
- Cancels do not consume quota
- Every 15 seconds, you get 1 **free** `sendTx` that doesn't consume quota
- Non-order transaction types (withdraw, transfer, etc.) have separate rate limits

### Quota Allocation Rules

| Starting Quota | 1,000 TX (for new accounts) |
|---|---|
| Earning Rate | +1 TX per $5 of trading volume |
| Maximum Stack | 5,000,000 TX allowance |
| Expiration | None — quota does NOT expire |
| Scope | Shared across all sub-accounts under the same L1 address |

### How "didn't use volume quota" Works

The error `"didn't use volume quota"` means the bot tried to submit a transaction (order) but the **volume quota is exhausted**. The system returns a response like `"10780 volume quota remaining"` when quota is available. When quota is 0, it blocks new order transactions.

### Rate Limit Tiers (Premium sendTx/sendTxBatch per minute)

| Staked LIT | sendTx/sendTxBatch per minute |
|---|---|
| 0 | 4,000 |
| 1,000 | 5,000 |
| 3,000 | 6,000 |
| 10,000 | 7,000 |
| 30,000 | 8,000 |
| 100,000 | 12,000 |
| 300,000 | 24,000 |
| 500,000 | 40,000 |

**Note:** Fee credits count as staked LIT for these limits.

---

## 2. Premium vs Standard Account Behavior

| Feature | Standard | Premium |
|---|---|---|
| sendTx/sendTxBatch rate limit | 60/min (shared with all API) | 4,000+/min (separate bucket) |
| Volume Quota | ❌ Not available | ✅ Available |
| Maker/Taker Fees | 0.0040% / 0.0280% | 0% / 0% |
| Taker Latency | 200ms | 300ms |
| Maker/Cancel Latency | — | 200ms |

**Key insight:** With Premium, you have two separate rate-limit buckets:
1. **REST API rate limits** (weighted requests for account data, order books, etc.)
2. **sendTx/sendTxBatch rate limits** (tied to staked LIT)

Getting rate-limited in one bucket does NOT affect the other.

**Upgrade/Downgrade rules:**
- Requires: no open positions, no open orders, 24h cooldown between changes
- Endpoint: `POST /api/v1/changeAccountTier`

---

## 3. API Endpoints to Check Quota Status

### Direct Quota Check

The `volume_quota_remaining` field is returned in the `RespSendTx` and `RespSendTxBatch` response objects **after each transaction**. It is NOT available proactively via a separate endpoint.

- **RespSendTx** fields: `code`, `message`, `tx_hash`, `predicted_execution_time_ms`, `volume_quota_remaining` (StrictInt)
- **RespSendTxBatch** fields: same structure

**The `volume_quota_remaining` field is always `None` in the bot's current code** — this means either:
1. The field isn't populated by the server in certain error conditions
2. The pydantic model isn't parsing the JSON correctly
3. The server only returns it on successful transactions

### Account Limits Endpoint

`GET /api/v1/accountLimits?account_index=<INDEX>`

Returns:
```json
{
  "code": 200,
  "max_llp_percentage": ...,
  "max_llp_amount": ...,
  "user_tier": "standard" | "premium",
  "can_create_public_pool": true/false,
  "current_maker_fee_tick": ...,
  "current_taker_fee_tick": ...,
  "leased_lit": ...,
  "effective_lit_stakes": "..."
}
```

The `user_tier` field confirms if the account is premium. The `effective_lit_stakes` shows effective staked LIT (including fee credits). However, this does **NOT** directly show remaining volume quota.

### No Direct "Query Quota" Endpoint Exists

**There is no dedicated API endpoint to check remaining volume quota.** The only way to know is:
1. From the `volume_quota_remaining` field in each `sendTx` response
2. By inferring from the error message when quota is exhausted

---

## 4. Bot-Side Strategy: Recommended Approach

### 4a. Track Volume Quota from Responses

After every successful `sendTx`, extract `volume_quota_remaining` from the response and maintain a local counter:

```python
self._volume_quota_remaining = getattr(resp, 'volume_quota_remaining', None)
```

Update this after every order/modify/cancel-all submission. This gives a **local estimate** of remaining quota.

### 4b. Implement Quota-Aware Cooldown

When `volume_quota_remaining` is low (e.g., < 50) or the "didn't use volume quota" error appears:

1. **Immediate:** Stop submitting new orders
2. **Wait:** The system grants +1 free tx every 15 seconds, and quota accumulates via trading volume
3. **Resume:** After cooldown, gradually resume (don't burst)

**Proposed cooldown after quota exhaustion:**
- Initial wait: 60 seconds
- Retry with 1 tx to test if quota has regenerated
- If still blocked: exponential backoff (60s → 120s → 300s)
- Max backoff: 5 minutes
- Reset backoff on first successful tx

### 4c. Prioritize Critical Orders

When quota is low, prioritize order types:
1. **SL orders** (stop-loss) — highest priority (position protection)
2. **TP orders** (take-profit) — medium priority
3. **New opens** — lowest priority (can wait)

### 4d. Use Cancels Freely (They Don't Consume Quota)

Individual `L2CancelOrder` does NOT consume volume quota. So the bot can always cancel individual orders without quota concerns. Only `L2CancelAllOrders` (type 16) consumes quota.

### 4e. Leverage the Free Tx Every 15 Seconds

Every 15 seconds, one `sendTx` is free (doesn't consume quota). For low-frequency trading, pace order submissions to align with this free window.

### 4f. Reduce Unnecessary Order Modifications

The bot currently creates market orders with IOC. If it also creates maker orders that need modification, each modification consumes quota. Consider:
- Fewer limit order modifications
- Use `L2CreateGroupedOrders` (batching) — consumes 1 quota regardless of how many orders in the batch
- Avoid spamming orders when quota is low

### 4g. Check `user_tier` on Startup

On bot startup, query `GET /api/v1/accountLimits` to verify `user_tier == "premium"` and log the `effective_lit_stakes`. This confirms the account upgrade is active.

---

## 5. Information Still Need to Confirm

1. **Why `volume_quota_remaining` is always `None`:** Need to capture raw JSON response to confirm if the field is actually in the server response but not parsing correctly, or if the server omits it on error responses.

2. **Free tx regeneration behavior:** Does the 15-second free tx roll over (bankable) or is it a hard reset every 15 seconds? (Likely hard reset, but unconfirmed.)

3. **Volume quota reset cycle:** Whether accumulated volume quota has any periodic reset, or if it truly never expires. (Docs say "does not expire" but worth confirming.)

4. **Premium tier activation timing:** Whether the premium tier upgrade takes effect immediately or after some propagation delay.

5. **Quota consumption for market vs limit orders:** Whether market orders (IOC) consume the same quota amount as limit orders. (They likely do, as both use L2CreateOrder type 14.)

6. **Proxy contribution:** The proxy (92.61.99.50:12324) is likely NOT contributing to volume quota issues — volume quota is tied to the L1 address, not IP. However, the proxy could cause issues with the general REST API rate limits (60 req/min for standard, 24,000 weighted for premium).

---

## 6. Summary of Key Findings

- **Root cause:** The bot is exhausting its volume quota by submitting too many order transactions (create/modify/cancel-all)
- **Premium is active** but volume quota is a separate concept from rate limits — having premium gives you quota, but you can still exhaust it
- **New accounts start at 1,000 TX quota** and earn +1 TX per $5 traded
- **No direct way to check remaining quota** outside of reading it from transaction responses
- **Cancels don't consume quota** (individual cancels; cancel-all does)
- **The 15-second free tx** provides a minimum guaranteed throughput
- **The `volume_quota_remaining` field exists in SDK response models** but is apparently always `None` — this needs investigation (likely server doesn't populate it on all responses)
