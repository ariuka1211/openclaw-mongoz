# Trading Lessons — Hard-Won Knowledge

Lessons learned from a month of building and operating the autopilot trading system.

## Architecture

- **Exchange = source of truth** for positions. State files get wiped by crashes. On startup, always reconcile with exchange — adopt orphans, remove phantoms.
- **Save state immediately after each position open.** Crash between open and periodic save = lost position.
- **DSL high-water marks reset on adoption/reconciliation.** Can't reconstruct from exchange data alone — accepted tradeoff.
- **`volume_quota_remaining` always returns None on mainnet.** Quota guard code checking `is not None` is dead.

## Bugs

- **Side detection:** use `pos.sign` (+1/-1), not `pos.position` (always positive on Lighter). Wrong side = inverted DSL.
- **Market orders work, limit orders don't** on Lighter. `avg_execution_price` acts as hard limit. Use `create_market_order_limited_slippage`.
- **Confidence scale mismatch:** AI outputs 0-1, bot expects 0-100. Mismatch rejects everything.
- **`stop_loss_pct` from AI must be stored on TrackedPosition.** Otherwise bot silently ignores it.
- **Parse decision JSON with brace-depth tracking.** `rfind('}')` is fragile on nested JSON.
- **Proxy URL scheme:** `http://` may work sometimes, `socks5://` is correct for SOCKS5. aiohttp varies by version.
- **State file wiped to empty by crash storms.** Multiple crashes can leave `{}`. Reconciliation handles this.

## Operations

- **DSL close loop needs circuit breaker.** Max 3 close attempts + 15min cooldown + Telegram alert.
- **Close cooldown:** 30min too aggressive, 5min reasonable.
- **Always restart services after merging subagent fixes.** Bugs often only crash on first run.
- **ExecStartPre healthcheck** prevents cascading restart loops.
- **Signal staleness:** check `signals.json` modification time before using. Could be hours old.

## Audit History

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| AI-Trader | 3 | 5 | 7 | 4 |
| Executor Bot | 1 | 3 | 4 | 5 |
| Integration | 0 | 3 | 4 | 4 |
| Total | 4 | 11 | 15 | 13 |

_Last updated: 2026-03-24_
