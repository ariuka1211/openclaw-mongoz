# Autopilot Trader — Architecture & Reference

> **Last updated:** 2026-03-23 by subagent audit
> **Purpose:** Complete system reference that survives session resets. Any fresh session can read this and fully understand the system.

---

## 1. Overview

An automated crypto perpetual futures trading system running on **Lighter.xyz** (a ZK-proof L2 DEX). It has three layers: (1) an **opportunity scanner** that scans all Lighter perp markets for trades, (2) an **AI decision engine** powered by LLM that decides what to trade, and (3) a **Python executor bot** that manages positions with a tiered dynamic stop-loss system (DSL). The system trades with ~$60 equity (as of 2026-03-23), max 3 concurrent positions, up to 20x leverage, and sends Telegram alerts for all actions. It runs as three systemd services on a 4vCPU/8GB RAM VPS.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          EXTERNAL DATA SOURCES                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ Lighter.xyz  │  │   OKX API    │  │   CEX Fund.  │                  │
│  │ REST API     │  │  (Klines)    │  │   Rates      │                  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                  │
│         │                 │                  │                          │
│         ▼                 ▼                  ▼                          │
│  ┌─────────────────────────────────────────────────┐                    │
│  │         ① OPPORTUNITY SCANNER (TypeScript)       │                    │
│  │   opportunity-scanner.ts — runs every 5 min      │                    │
│  │   Outputs: signals.json                          │                    │
│  └─────────────────────┬───────────────────────────┘                    │
│                        │ signals.json                                   │
│                        ▼                                                │
│  ┌─────────────────────────────────────────────────┐                    │
│  │         ② AI TRADER (Python)                     │                    │
│  │   ai_trader.py — runs every 2 min               │                    │
│  │   Components:                                    │                    │
│  │     • context_builder.py — assembles LLM prompt  │                    │
│  │     • llm_client.py — calls Kilo Gateway API     │                    │
│  │     • safety.py — hard rules the LLM can't break │                    │
│  │     • db.py — SQLite decision journal            │                    │
│  │   Outputs: ai-decision.json                      │                    │
│  └─────────────────────┬───────────────────────────┘                    │
│                        │ ai-decision.json                               │
│                        ▼                                                │
│  ┌─────────────────────────────────────────────────┐                    │
│  │         ③ EXECUTOR BOT (Python)                  │                    │
│  │   bot.py — runs every 5-60s                      │                    │
│  │   Components:                                    │                    │
│  │     • PositionTracker — DSL or legacy trailing    │                    │
│  │     • LighterAPI — exchange communication         │                    │
│  │     • dsl.py — Dynamic Stop Loss engine          │                    │
│  │     • auth_helper.py — REST API auth tokens       │                    │
│  │   Outputs: ai-result.json, Telegram alerts       │                    │
│  └─────────────────────┬───────────────────────────┘                    │
│                        │                                                │
│                        ▼                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │  Telegram    │  │   Dashboard  │  │  SQLite DB   │                  │
│  │  Alerts      │  │  (FastAPI)   │  │  (trader.db) │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow (simplified)

```
Scanner (5min) → signals.json
    ↓
AI Trader (2min) → reads signals.json + positions + history → LLM → ai-decision.json
    ↓
Bot (per tick) → reads ai-decision.json → executes via Lighter API → writes ai-result.json
    ↓
Bot also manages exits via DSL (tiered trailing stop-loss) — independent of AI
```

---

## 3. Components

### 3.1 Executor Bot (`bot.py`)

**Location:** `projects/autopilot-trader/bot/bot.py`
**Language:** Python (runs in a venv)
**Service:** `bot.service`

The core position management daemon. It does NOT decide what to trade — it only executes decisions from the AI trader and manages exits via DSL or legacy trailing stop-loss.

#### Key Responsibilities:
- **Position sync:** Polls Lighter API for open positions, detects new/closed positions with two-cycle confirmation (prevents phantom positions)
- **AI decision execution:** Reads `ai-decision.json`, executes open/close/close_all commands
- **Signal-based execution (fallback):** Reads `signals.json`, opens top-scored opportunities when `ai_mode: false`
- **Exit management:** DSL tiered trailing stop-loss OR legacy flat trailing TP/SL
- **Price tracking:** Gets mark prices from `unrealized_pnl` (authoritative exchange price) with fallback to `recent_trades`
- **Telegram alerts:** Sends all actions/failures to Telegram
- **Quota management:** Tracks Lighter volume quota, exponential backoff when exhausted, prioritizes SL orders when quota < 50

#### Key Classes:
- **`LighterCopilot`** — main bot loop (`start()` → `_tick()` cycle)
- **`PositionTracker`** — tracks positions, evaluates TP/SL triggers
- **`LighterAPI`** — wrapper around Lighter SDK (orders, positions, prices)
- **`TelegramAlerter`** — sends Markdown alerts via Telegram Bot API
- **`TrackedPosition`** — per-position data (side, entry, HWM, DSL state)

#### Bot Cycle (`_tick()`):
1. Prune expired cache entries
2. Sync positions from Lighter API (detect new/closed)
3. Cache mark prices from unrealized_pnl
4. Two-cycle confirmation for new positions
5. If AI mode: read `ai-decision.json` and execute
6. If signal mode: read `signals.json` and open top-scored positions
7. Get prices for all tracked positions
8. Evaluate triggers (DSL or legacy) and execute exits

#### Key Design Decisions:
- **Two-cycle confirmation:** New positions must appear in 2 consecutive syncs before tracking (prevents phantom detections)
- **Mark price from unrealized_pnl:** More accurate than recent_trades for ROE evaluation
- **Close verification:** After submitting close order, polls API 4 times with progressive delays (5+10+15+20s = 50s total)
- **Close attempt circuit breaker:** Max 3 attempts, then 15-min cooldown + Telegram alert
- **AI close cooldown:** 30-minute cooldown prevents re-opening same symbol after AI closes it
- **Phantom position guard:** 5-minute window after bot-closes a position, ignores it in sync

### 3.2 DSL — Dynamic Stop Loss (`dsl.py`)

**Location:** `projects/autopilot-trader/bot/dsl.py`
**Language:** Pure Python (no LLM, no exchange dependency)

Adapted from Senpi's DSL v5. A tiered trailing stop-loss system that tightens as profit increases.

#### How It Works:
1. Tracks high-water ROE (peak profit as percentage)
2. As ROE climbs past tier triggers (7%, 12%, 15%, 20%), activates progressively tighter floors
3. Each tier has a `consecutive_breaches` threshold — price must break the floor N times before locking
4. `trailing_buffer_roe` provides a fixed ROE cushion from the peak (e.g., floor = HW - 5%)
5. Also tracks stagnation: if ROE ≥ 8% but no new high for 60 minutes → auto-exit

#### Default Tiers:
| Trigger | Lock HW% | Buffer ROE | Breaches | Behavior |
|---------|----------|------------|----------|----------|
| +7% ROE | 40%      | 5%         | 3        | First gear — wide buffer |
| +12% ROE| 55%      | 4%         | 2        | Tightening |
| +15% ROE| 75%      | 3%         | 2        | Aggressive lock |
| +20% ROE| 85%      | 2%         | 1        | Maximum protection |

#### Exit Triggers:
- `hard_sl` — ROE ≤ -20% (sl_pct * leverage = 2% * 10x = 20% ROE loss)
- `tier_lock` — ROE dropped below the locked floor after enough breaches
- `stagnation` — High ROE but stalled for 60 minutes

### 3.3 Opportunity Scanner (`opportunity-scanner.ts`)

**Location:** `projects/autopilot-trader/scanner/opportunity-scanner.ts`
**Language:** TypeScript (runs via Bun)
**Service:** `scanner.service` (wraps `scanner-daemon.sh`, runs every 5 min)

Scans all Lighter perp markets for actionable opportunities.

#### Signal Components (weighted composite):
| Signal | Weight | Description |
|--------|--------|-------------|
| Funding Arbitrage | 35% | Lighter funding rate vs CEX average (Binance, Bybit, etc.) |
| MA Alignment | 20% | Price > MA50 > MA99 > MA200 (from OKX 1H klines) |
| Volume Anomaly | 15% | Daily volume vs OI-based baseline estimate |
| Momentum | 15% | Daily price change magnitude |
| Order Block | 15% | Distance to nearest support/resistance OB (from OKX) |

#### Position Sizing (risk-based):
```
riskAmountUsd = equity × 5% (riskPct)
stopLossDistance = dailyVolatility × 1.0 (SL multiple)
positionSizeUsd = riskAmountUsd / stopLossDistance
```
Safety: liquidation distance must be ≥ 2× stop-loss distance.

#### Data Sources:
- **Lighter API:** `orderBookDetails` + `funding-rates` (all markets)
- **OKX API:** 1H klines for MA calculation + OB detection (only for markets with OKX equivalents)

#### Direction Logic:
Majority vote of 3 signals: MA direction (↑=long, ↓=short), OB type (support=long, resistance=short), funding spread (negative=long, positive=short). Tiebreaker = MA direction.

#### Output:
Writes `signals.json` with all qualified opportunities (score ≥ 60, safety passed), including position sizing, direction, and all raw scores. Stale signals (>20 min old) are auto-pruned.

### 3.4 AI Trader (`ai_trader.py`)

**Location:** `projects/autopilot-trader/ai-decisions/ai_trader.py`
**Language:** Python (async daemon)
**Service:** `ai-decisions.service`

The LLM-driven decision engine. Runs every 2 minutes (configurable), calls LLM to decide what to trade.

#### Cycle Flow:
1. Read `signals.json` (top 15 by score, filtered to exclude recently traded symbols)
2. Read current positions from `ai-result.json`
3. Read recent decisions from SQLite (last 20)
4. Read trade outcomes from SQLite (last 10)
5. Read strategy memory (`state/strategy_memory.md`)
6. **Change detection:** SHA-256 hash of top 10 signals + positions. If nothing changed → skip LLM call (saves tokens)
7. Build compressed prompt via `context_builder.py`
8. Call LLM via Kilo Gateway API (`api.kilo.ai/api/gateway`)
9. Parse JSON response, validate schema
10. Safety check via `safety.py`
11. If approved → write `ai-decision.json` for bot to consume
12. Log everything to SQLite

#### Kill Switches:
- **5 consecutive LLM failures** → emergency halt (writes `close_all` to bot)
- **15 safety rejections in 30 min** → emergency halt
- **Daily drawdown > 10%** → emergency halt

#### Decision Schema (LLM output):
```json
{
  "action": "open|close|close_all|hold",
  "symbol": "BTC",
  "direction": "long|short",
  "size_pct_equity": 3.5,
  "leverage": 5,
  "stop_loss_pct": 2.0,
  "reasoning": "Strong MA alignment + funding favorable",
  "confidence": 0.85
}
```

### 3.5 Context Builder (`context_builder.py`)

Assembles the LLM prompt from compressed context. Includes:
- Current positions with ROE
- Top 10 market opportunities with funding direction
- Recent trade outcomes (last 5)
- Learned patterns from strategy memory (sanitized for injection)
- Account stats (win rate, avg win/loss, daily PnL, session timing)
- Recent decisions (last 5)

Has a `_filter_traded_symbols()` method that removes symbols traded in the last 2 hours to avoid re-trading losers.

### 3.6 Safety Layer (`safety.py`)

Hard-coded rules the LLM cannot override. Every decision passes through before reaching the bot.

#### Safety Rules:
| Rule | Limit |
|------|-------|
| Max concurrent positions | 3 |
| Max leverage | 20x |
| Max size per position | 5% equity |
| Max total exposure | 15% equity |
| Max daily drawdown | 10% |
| Min confidence | 0.3 |
| Min scanner score | 60 |
| Required stop loss | Yes (min 0.5%) |
| Max orders per hour | 50 |
| Cooldown after loss | 5 minutes |
| Liq distance ≥ 2× SL distance | Enforced |

### 3.7 LLM Client (`llm_client.py`)

HTTP client for Kilo Gateway API (`api.kilo.ai/api/gateway`). Uses `KILOCODE_API_KEY` JWT.
- Primary + fallback model (both `xiaomi/mimo-v2-pro`)
- Retry with exponential backoff (4s → 8s → 16s)
- Rate limit handling (429 → 60s backoff)
- Tracks stats: calls, tokens, latency

### 3.8 Dashboard (`dashboard.py` + `dashboard/index.html`)

FastAPI web server on port 8080. Endpoints:
- `/` — HTML dashboard
- `/api/status` — uptime, model, performance stats
- `/api/decisions` — recent decisions from SQLite
- `/api/performance` — win rate, PnL, trade counts
- `/api/alerts` — recent alerts

### 3.9 Auth Helper (`auth_helper.py`)

Manages Lighter REST API auth tokens. Tokens are long-lived (6-hour max), generated via `create_auth_token_with_expiry()`. Cached in-memory and on disk (`.auth_token.json`). Auto-refreshes 30 minutes before expiry.

---

## 4. Data Flow

```
                    ┌─────────────────────┐
                    │  Lighter + OKX APIs  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Scanner (Bun/TS)   │  Every 5 min
                    │  opportunity-       │
                    │  scanner.ts         │
                    └──────────┬──────────┘
                               │ signals.json
                    ┌──────────▼──────────┐
                    │  AI Trader (Python)  │  Every 2 min
                    │  Reads:              │
                    │   • signals.json     │
                    │   • ai-result.json   │  ← bot writes this
                    │   • SQLite decisions │
                    │   • SQLite outcomes  │
                    │   • strategy_memory  │
                    │  Calls: LLM → safety│
                    └──────────┬──────────┘
                               │ ai-decision.json
                    ┌──────────▼──────────┐
                    │  Bot (Python)        │  Every 5-60s
                    │  Reads:              │
                    │   • ai-decision.json │
                    │  Executes via SDK    │
                    │  Manages DSL exits   │
                    └──────────┬──────────┘
                               │
                    ┌──────────┼──────────┐
                    │          │          │
              ┌─────▼──┐  ┌───▼───┐  ┌──▼──────┐
              │Telegram │  │ai-    │  │SQLite   │
              │Alerts   │  │result │  │decisions│
              └─────────┘  │.json  │  │outcomes │
                           └───────┘  └─────────┘
```

### File Contracts:

| File | Written By | Read By | Format |
|------|-----------|---------|--------|
| `signals.json` | Scanner | AI Trader, Bot | `{timestamp, config, opportunities[]}` |
| `ai-decision.json` | AI Trader | Bot | `{timestamp, action, symbol, direction, size_usd, ...}` |
| `ai-result.json` | Bot | AI Trader | `{timestamp, positions[], success, ...}` |
| `trader.db` | AI Trader, Bot | AI Trader, Reflection | SQLite (decisions, outcomes, alerts tables) |
| `strategy_memory.md` | Reflection | AI Trader (context) | Markdown |
| `tracked_markets.json` | Bot | Bot | `[market_ids]` |
| `quota_state.json` | Bot | Bot | `{quota, timestamp}` |

---

## 5. Configuration

### Bot Config (`bot/config.yml`)

```yaml
lighter_url: "https://mainnet.zklighter.elliot.ai"
account_index: ${LIGHTER_ACCOUNT_INDEX}      # 719758
api_key_index: 3
api_key_private: "${LIGHTER_API_PRIVATE_KEY}"
proxy_url: "socks5://..."                     # SOCKS5 via aiohttp-socks

trailing_tp_trigger_pct: 0.0                  # 0 = trailing from entry
trailing_tp_delta_pct: 1.0                    # 1% trail distance
sl_pct: 2.0                                   # Hard SL at -2% from entry

dsl_enabled: true
default_leverage: 10.0
stagnation_roe_pct: 8.0                       # Exit if ROE≥8% and stalled
stagnation_minutes: 60
dsl_tiers: [4 tiers: 7/12/15/20% triggers]

ai_mode: true
ai_decision_file: "../signals/ai-decision.json"
ai_result_file: "../signals/ai-result.json"
signals_file: "../signals/signals.json"

telegram_token: "${TELEGRAM_BOT_TOKEN}"
telegram_chat_id: "${TELEGRAM_CHAT_ID}"
price_poll_interval: 5
price_call_delay: 5.0
```

### AI Trader Config (`ai-decisions/config.json`)

```json
{
  "db_path": "state/trader.db",
  "decision_file": "../ai-decision.json",
  "signals_file": "../signals.json",
  "strategy_memory_file": "state/strategy_memory.md",
  "max_signals": 15,
  "cycle_interval_seconds": 120,
  "max_consecutive_failures": 5,
  "llm": {
    "api_base": "https://api.kilo.ai/api/gateway",
    "primary_model": "xiaomi/mimo-v2-pro",
    "fallback_model": "xiaomi/mimo-v2-pro",
    "timeout_seconds": 60
  },
  "safety": {
    "max_positions": 3,
    "max_leverage": 20.0,
    "max_size_pct_equity": 5.0,
    "max_daily_drawdown_pct": 10.0,
    "max_orders_per_hour": 50
  }
}
```

### Scanner Config (hardcoded in `opportunity-scanner.ts`)

```typescript
accountEquity: 60        // Updated to actual balance
riskPct: 0.05            // 5% equity risked per trade
stopLossMultiple: 1.0
maxLeverageCap: 20
maxConcurrentPositions: 3
minDailyVolume: 100000   // $100K minimum
```

### Environment Variables (`.env`)

All secrets are in `/root/.openclaw/workspace/.env`:
- `LIGHTER_ACCOUNT_INDEX` = 719758
- `LIGHTER_API_PRIVATE_KEY` — trading key
- `LIGHTER_PROXY_*` — SOCKS5 proxy (Webshare)
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- `KILOCODE_API_KEY` — JWT for Kilo Gateway

---

## 6. Services

### systemd Services

| Service | File | Working Dir | Command | Status |
|---------|------|-------------|---------|--------|
| `bot` | `/etc/systemd/system/bot.service` | `.../bot/` | `venv/bin/python3 bot.py` | active |
| `scanner` | `/etc/systemd/system/scanner.service` | N/A | `scanner-daemon.sh` (runs bun in loop) | active |
| `ai-trader` | `/etc/systemd/system/ai-decisions.service` | `.../ai-decisions/` | `python3 ai_trader.py` | active |

### Scanner Daemon (`scanner-daemon.sh`)

Simple bash loop: runs `bun run scanner/opportunity-scanner.ts --max-positions 3` every 300 seconds. Output goes to `scanner.log`.

### Management Commands:
```bash
# Check status
systemctl status bot scanner ai-trader

# View logs
journalctl -u bot -f
journalctl -u ai-decisions -f
tail -f projects/autopilot-trader/scanner.log

# Restart
systemctl restart bot
systemctl restart scanner
systemctl restart ai-trader
```

---

## 7. Known Issues & Gotchas

### 🔴 Critical / Active

1. **SL Execution Bug** — `create_market_order_limited_slippage` accepted by API (HTTP 200) but not actually executing. Root cause: `avg_execution_price` acts as a hard limit on the exchange side, not just a slippage guard. Orders may silently fail to fill. Current workaround: `create_market_order_if_slippage` with 2% max slippage. Monitor closely.

2. **Position Re-detection Loop** — After bot restart, same positions get detected as "new" every sync cycle, creating duplicate DSLState entries. The two-cycle confirmation helps but doesn't fully solve it. Positions opened before restart lose their DSL state.

3. **Volume Quota** — Lighter account has a volume quota (initial 1000 TX). Failed/rejected orders still burn quota. When quota = 0, no orders execute. System has exponential backoff (60s→120s→300s) and quota prioritization (blocks new opens when quota < 50 to preserve for SL). May need account tier upgrade.

4. **AI Trader API URL** — Was broken (`api.kilo.ai/v1` → should be `api.kilo.ai/api/gateway`). If AI trader returns HTTP 404, check this config value.

5. **Side Detection Bug** — Fixed in commit 6b94de8. Was using `pos.position` (always positive) instead of `pos.sign` (+1/-1), causing all positions to be tracked as LONG. If positions seem inverted, check the `sign` field extraction.

### 🟡 Moderate

6. **SOCKS5 Proxy Required** — Lighter API is geo-restricted. The bot patches `lighter.rest.RESTClientObject.__init__` to use `aiohttp_socks.ProxyConnector`. The `SignerClient` is subclassed (`ProxySignerClient`) to use proxy-configured `ApiClient`. Both REST and signer need the proxy.

7. **Bot Config Path** — All three systemd services use correct `projects/autopilot-trader/` paths. `ai-decisions.service` uses `ai-trader.env` symlinked to workspace `.env`.

8. **Mark Price Derivation** — Bot reverse-engineers mark price from `unrealized_pnl / size`. This is more accurate than `recent_trades` for ROE evaluation but depends on correct side detection (was buggy before fix).

9. **Close Verification Delays** — Progressive delays (5+10+15+20s = 50s total) after close order submission. During volatile markets, position may move against you during verification wait.

10. **Phantom Position Prevention** — Requires 2 consecutive sync cycles to confirm new positions. During the confirmation window, the bot sends a "pending confirmation" Telegram alert.

### 🟢 Minor

11. **Scanner Equity Hardcoded** — Scanner has `accountEquity: 60` hardcoded. Bot auto-scales position sizes based on actual balance vs scanner's `accountEquity` config, so this mismatch is handled but the scanner output shows wrong absolute numbers.

12. **DB Outcomes Missing leverage** — The `_log_outcome()` in bot.py calculates ROE as `pnl_pct * default_leverage` (hardcoded 10x) instead of using the actual per-position leverage from exchange.

---

## 8. Account Info

| Field | Value |
|-------|-------|
| **Lighter Account Index** | 719758 |
| **L1 Address** | `0x1D73456fA182B669783c5adaaB965AbB1A373bEE` |
| **API Key Index** | 3 |
| **Exchange** | Lighter.xyz (mainnet.zklighter.elliot.ai) |
| **Chain ID** | 304 (mainnet) |
| **Approximate Equity** | ~$60 USDC (as of 2026-03-23) |

> ⚠️ **NEVER provide account addresses without verifying from the .env file first.** On 2026-03-23, an unverified address caused a $100 loss. Always check `LIGHTER_ACCOUNT_INDEX` in `.env`.

---

## 9. Recent Decisions

### Architectural Choices Made:

1. **AI replaces middle layer, not scanner or exits** — Scanner still finds opportunities. DSL still manages exits. AI only decides WHAT to trade and WHEN.

2. **File-based IPC** — Bot and AI trader communicate via JSON files (`ai-decision.json`, `ai-result.json`) rather than shared memory or IPC sockets. Simple, debuggable, survives restarts.

3. **Change detection** — AI trader hashes top 10 signals + positions and skips LLM call if nothing changed. Saves ~50% of API calls.

4. **DSL over flat trailing** — Tiered stop-loss with high-water marks is strictly better than flat trailing SL. Keeps positions open during rallies, tightens as profit grows.

5. **Mark price from unrealized_pnl** — Derives mark price from PnL formula instead of using recent trades. More accurate for ROE evaluation.

6. **Two-cycle position confirmation** — New positions must appear in 2 consecutive syncs to prevent false positives from API lag.

7. **Quota prioritization** — When volume quota < 50, blocks new opens to preserve quota for critical SL orders.

8. **Graduated close retry** — Close failures retry with increasing delays (15s→60s→300s→900s) to avoid burning quota on impossible fills.

9. **Signal weights never auto-applied** — Signal analyzer suggests weight adjustments but they require human review.

10. **Kilo Gateway for LLM** — Switched from OpenRouter to Kilo Gateway (`api.kilo.ai/api/gateway`) using JWT auth (`KILOCODE_API_KEY`). Model: `xiaomi/mimo-v2-pro`.

---

## 10. Lighter API

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

---

## Appendix A: File Structure

```
projects/autopilot-trader/
├── ai-decisions/
│   ├── ai_trader.py          # Main AI daemon
│   ├── context_builder.py    # LLM prompt assembler
│   ├── llm_client.py         # Kilo Gateway HTTP client
│   ├── safety.py             # Hard safety rules
│   ├── db.py                 # SQLite helpers (DecisionDB)
│   ├── config.json           # AI trader config
│   ├── prompts/
│   │   ├── system.txt        # System prompt for LLM
│   │   └── decision.txt      # Decision prompt template
│   ├── state/
│   │   ├── patterns.json     # Trade pattern data
│   │   └── trader.db-*       # SQLite WAL/SHM
│   └── logs/
├── bot/
│   ├── bot.py                # Main bot loop
│   ├── dsl.py                # Dynamic Stop Loss engine
│   ├── auth_helper.py        # REST API auth token manager
│   ├── config.yml            # Bot configuration
│   ├── config.example.yml    # Config template
│   ├── requirements.txt      # Python dependencies
│   ├── healthcheck.py        # Health endpoint
│   ├── venv/                 # Python virtual environment
│   └── state/
│       ├── tracked_markets.json
│       └── bot_state.json
├── scanner/
│   ├── opportunity-scanner.ts  # Signal scoring (every 5 min)
│   ├── scanner-daemon.sh       # Persistent loop wrapper
│   └── .gitignore
├── dashboard/
│   ├── app.py                # Flask app factory
│   ├── index.html            # Frontend (single-page)
│   ├── api/
│   │   ├── portfolio.py      # /api/portfolio endpoints
│   │   ├── scanner.py        # /api/scanner endpoints
│   │   ├── system.py         # /api/system endpoints
│   │   └── trader.py         # /api/trader endpoints
│   └── utils.py          # Shared helpers (read_json, time_ago)
│   └── js/
│       ├── store.js          # Shared state store
│       ├── portfolio.js      # Portfolio module
│       ├── scanner.js        # Scanner module
│       ├── system.js         # System module
│       ├── trader.js         # Trader module
│       ├── performance.js    # Performance module
│       └── providers.js      # Data providers
├── ipc/
│   ├── signals.json          # Scanner → AI, Bot
│   ├── ai-decision.json      # AI → Bot
│   └── ai-result.json        # Bot → AI
├── shared/
│   ├── __init__.py       # Python package marker
│   └── ipc_utils.py          # Shared IPC read/write helpers
├── archives/
│   ├── bot-scanner/          # Old OSA scanner (deleted)
│   └── scanner/              # Archived: correlation-guard, funding-monitor, cleanup scripts
├── docs/
│   ├── autopilot-trader.md   # This file
│   ├── cheatsheet.md         # Quick file map + patterns
│   ├── pocket-ideas.md       # Feature ideas backlog
│   ├── unified-dashboard-plan.md
│   └── lighter-quota-research.md
└── signals/
    └── oi-snapshot.json      # OI trend comparison data
```

---

## Lessons Learned

*Hard-won knowledge from building and operating the system (March 2026).*

- **DSL high-water marks reset on adoption/reconciliation** — Can't reconstruct from exchange data alone. Accepted tradeoff.
- **Market orders work, limit orders don't on Lighter** — `avg_execution_price` acts as a hard limit. Use `create_market_order_if_slippage` instead of `create_market_order_limited_slippage` (the latter has a hard-limit bug — see Known Issue #1).
- **Confidence scale mismatch** — AI outputs 0-1, bot expects 0-100. Mismatch silently rejects everything.
- **`stop_loss_pct` from AI must be stored on TrackedPosition** — Otherwise bot silently ignores the AI's stop-loss instruction.
- **Parse decision JSON with brace-depth tracking** — `rfind('}')` is fragile on nested JSON and will break.
- **Proxy URL scheme** — `socks5://` is correct for SOCKS5. `http://` varies by aiohttp version and may fail.
- **State file wiped to empty by crash storms** — Multiple crashes can leave `{}`. Reconciliation handles this gracefully.
- **Close cooldown** — 30min too aggressive, 5min reasonable.
- **Always restart services after merging subagent fixes** — Bugs often only crash on first run.
- **ExecStartPre healthcheck** — Prevents cascading restart loops.
- **Signal staleness** — Check `signals.json` modification time before using. Could be hours old.
d.
