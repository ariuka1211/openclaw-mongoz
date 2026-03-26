# Lighter API Reference

### Key Endpoints Used:

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/v1/account` | GET | API key | Fetch positions, collateral, PnL |
| `/api/v1/accountLimits` | GET | Auth token | Check account tier and limits |
| `/api/v1/orderBooks` | GET | Public | Get market symbols + decimals |
| `/api/v1/orderBookDetails` | GET | Public | Market data (volume, OI, price range) |
| `/api/v1/recentTrades` | GET | Public | Latest price for a market |
| `/api/v1/funding-rates` | GET | Public | All funding rates (Lighter + CEX) |
| `/api/v1/accountActiveOrders` | GET | Auth token | Check active orders for a market |
| `/api/v1/accountInactiveOrders` | GET | Auth token | Query fill prices of closed orders |

### Order Creation (via SDK SignerClient):
- `create_market_order` — Market open (buy/sell)
- `create_market_order_if_slippage` — Market order with pre-check (used for SL)
- `create_tp_order` — Take profit order (trigger + limit)
- `create_market_order_limited_slippage` — Alternative market order (tried but has hard-limit bug)

### Auth:
- **Trading:** API key (index 3) + private key for signing transactions via `SignerClient`
- **REST queries:** Long-lived auth tokens (6-hour max) via `LighterAuthManager` — `create_auth_token_with_expiry()`, cached on disk with 30-min refresh buffer

### Quota Behavior:
- Each account has a **volume quota** (number of transactions allowed)
- Initial quota varies by account tier
- **Every order submission consumes quota** — even rejected/failed orders
- Quota response field: `volume_quota_remaining` on order response objects
- When quota = 0: API returns 200 but order doesn't execute ("didn't use volume quota")
- **No public endpoint to query remaining quota** — only available from order responses
- Bot tracks quota from responses and persists to `state/quota_state.json` for restart continuity

### Rate Limiting:
- REST API has rate limits (HTTP 429)
- Bot adds `price_call_delay` (5s) between sequential price calls
- Account balance check is disabled due to rate limits (TODO: re-enable)

### SDK Notes:
- Python SDK: `lighter` package (from Lighter)
- SignerClient handles nonce management internally (`NonceManagerType.OPTIMISTIC`)
- Order API returns tuple: `(CreateOrder, RespSendTx, None)` on success, `(None, None, error_msg)` on failure
- Market decimal precision varies per market — fetched from `orderBooks` endpoint
- All integer amounts are scaled by `10^decimals` (e.g., price $100 with 2 decimals → 10000)
