# Autopilot Trader — Architecture & Reference

> **Last updated:** 2026-03-25
> **Purpose:** Complete system reference that survives session resets. Any fresh session can read this and fully understand the system.

---

## 1. Overview

An automated crypto perpetual futures trading system running on **Lighter.xyz** (a ZK-proof L2 DEX). Three layers: an **opportunity scanner** (all Lighter perp markets), an **AI decision engine** (LLM-powered), and a **Python executor bot** with tiered dynamic stop-loss (DSL). ~$60 equity, max 3 concurrent positions, up to 20x leverage. Telegram alerts for all actions. Three systemd services on a 4vCPU/8GB VPS.

---

## 2. Architecture

```
Scanner (5min) → signals.json
    ↓
AI Trader (2min) → LLM → ai-decision.json
    ↓
Bot (5-60s) → executes → ai-result.json + Telegram alerts
    ↓
DSL manages exits independently of AI
```

### File Contracts

| File | Written By | Read By | Format |
|------|-----------|---------|--------|
| `signals.json` | Scanner | AI Trader, Bot | `{timestamp, config, opportunities[]}` |
| `ai-decision.json` | `ipc/bot_protocol.py` | Bot | `{timestamp, action, symbol, direction, size_usd, ...}` |
| `ai-result.json` | Bot | AI Trader | `{timestamp, positions[], success, ...}` |
| `trader.db` | AI Trader, Bot | AI Trader, Reflection | SQLite (decisions, outcomes, alerts tables) |
| `tracked_markets.json` | Bot | Bot | `[market_ids]` |
| `quota_state.json` | Bot | Bot | `{quota, timestamp}` |

---

## 3. Components

### 3.1 Executor Bot (`bot.py`)

**Location:** `projects/autopilot-trader/bot/bot.py` | **Language:** Python (venv) | **Service:** `bot.service`

Core position management daemon. Executes AI decisions, manages exits via DSL or legacy trailing, sends Telegram alerts.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `bot.py` | 331 | Coordinator — init, main loop, tick orchestration |
| `api/lighter_api.py` | 596 | DEX API wrapper — orders, positions, prices |
| `core/execution_engine.py` | 575 | Main tick loop + position processing |
| `core/state_manager.py` | 432 | Save/load/reconcile bot state |
| `core/position_tracker.py` | 263 | DSL + legacy trailing TP/SL evaluation |
| `core/executor.py` | 335 | AI open/close/close_all execution |
| `core/signal_handler.py` | 207 | Scanner signal processing |
| `core/shared_utils.py` | 197 | Pacing, quota, market ID, outcome logging |
| `dsl.py` | 178 | Dynamic Stop Loss engine |
| `config.py` | 147 | BotConfig dataclass, YAML loading |
| `core/decision_handler.py` | 147 | AI decision validation + dispatch |
| `core/verifier.py` | 132 | Position verification + fill polling |
| `core/order_manager.py` | 123 | Quota/pacing/order cache cleanup |
| `core/signal_processor.py` | 109 | Thin compatibility wrapper → delegates to handlers |
| `core/result_writer.py` | 100 | AI result IPC writing |
| `core/models.py` | 67 | TrackedPosition + BotState dataclasses |
| `auth_helper.py` | 86 | REST API auth tokens (6-hour tokens, 30-min refresh) |
| `api/proxy_patch.py` | 68 | SOCKS5 proxy monkey-patch |
| `healthcheck.py` | 49 | Health endpoint |
| `alerts/telegram.py` | 40 | Telegram alerts |

### 3.2 DSL — Dynamic Stop Loss (`dsl.py`)

**Location:** `projects/autopilot-trader/bot/dsl.py` | **Language:** Pure Python

Adapted from Senpi's DSL v5. Tiered trailing stop-loss that tightens as profit increases. Tracks high-water ROE and activates progressively tighter floors past tier triggers. Also tracks stagnation (≥5% ROE but stalled 60min → auto-exit).

#### Default Tiers

| Trigger | Lock HW% | Buffer ROE | Breaches | Behavior |
|---------|----------|------------|----------|----------|
| +7% ROE | 40%      | 5%         | 3        | First gear — wide buffer |
| +12% ROE| 55%      | 4%         | 2        | Tightening |
| +15% ROE| 75%      | 3%         | 2        | Aggressive lock |
| +20% ROE| 85%      | 2%         | 1        | Maximum protection |

#### Exit Triggers

- `hard_sl` — ROE ≤ -12.5% (sl_pct * leverage = 1.25% * 10x = 12.5% ROE loss)
- `tier_lock` — ROE dropped below the locked floor after enough breaches
- `stagnation` — High ROE but stalled for 60 minutes

### 3.3 Opportunity Scanner (`src/main.ts`)

**Location:** `projects/autopilot-trader/scanner/src/main.ts` | **Language:** TypeScript (Bun) | **Service:** `scanner.service` (via `scanner-daemon.sh`, every 5 min)

Scans all Lighter perp markets for actionable opportunities.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `src/main.ts` | 259 | Entry point, module wiring |
| `src/output.ts` | 222 | Console display + signals.json write |
| `src/position-sizing.ts` | 111 | Risk-based sizing + safety checks |
| `src/types.ts` | 105 | All interfaces |
| `src/signals/order-block.ts` | 82 | Order block detection + score |
| `src/api/okx.ts` | 59 | OKX klines + cache |
| `src/signals/oi-trend.ts` | 56 | OI change score |
| `src/config.ts` | 43 | Constants + settings |
| `src/signals/price-momentum.ts` | 44 | Daily price change score |
| `src/signals/moving-average-alignment.ts` | 43 | MA structure score |
| `src/signals/funding-spread.ts` | 37 | Funding rate spread score |
| `src/api/lighter.ts` | 40 | Lighter REST calls |
| `src/direction-vote.ts` | 47 | Long/short majority vote |

#### Signal Components (weighted composite)

| Signal | Weight | Description |
|--------|--------|-------------|
| Funding Arbitrage | 35% | Lighter funding rate vs CEX average |
| MA Alignment | 20% | Price > MA50 > MA99 > MA200 (OKX 1H) |
| Volume Anomaly | 15% | Daily volume vs OI-based baseline |
| Momentum | 15% | Daily price change magnitude |
| Order Block | 15% | Distance to nearest support/resistance OB |

#### Position Sizing (risk-based)

```
riskAmountUsd = equity × 5%
stopLossDistance = dailyVolatility × 1.0
positionSizeUsd = riskAmountUsd / stopLossDistance
```

Safety: liquidation distance must be ≥ 2× stop-loss distance.

### 3.4 AI Trader (`ai_trader.py`)

**Location:** `projects/autopilot-trader/ai-decisions/ai_trader.py` | **Language:** Python (async daemon) | **Service:** `ai-decisions.service`

Thin coordinator — init, daemon loop, calls `cycle_runner.run_cycle()`. All logic delegated to modular sub-packages.

| Module | Responsibility |
|--------|---------------|
| `ai_trader.py` | Thin coordinator — init, daemon loop, main() |
| `cycle_runner.py` | Cycle orchestration — context → LLM → parse → safety → IPC → log |
| `db.py` | SQLite decision journal |
| `llm_client.py` | OpenRouter/Kilo LLM client with retry/backoff |
| `safety.py` | Hard safety rules the LLM can't override |
| `llm/parser.py` | Parse and validate LLM JSON responses |
| `context/data_reader.py` | Read signals + positions from files, DB fallback |
| `context/pattern_engine.py` | Learned pattern rules with decay/reinforcement |
| `context/stats_formatter.py` | Performance stats + hold regret formatting |
| `context/prompt_builder.py` | LLM prompt assembly with token budget |
| `context/sanitizer.py` | Prompt injection detection |
| `context/token_estimator.py` | Token counting (tiktoken with fallback) |
| `ipc/bot_protocol.py` | IPC: send decisions, check results, emergency halt |

#### Safety Rules (in `safety.py`)

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

#### Kill Switches (in `ipc/bot_protocol.py`)

- **5 consecutive LLM failures** → emergency halt (writes `close_all` to bot)
- **15 safety rejections in 30 min** → emergency halt
- **Daily drawdown > 10%** → emergency halt

#### LLM Client (`llm_client.py`)

Uses Kilo Gateway API (`api.kilo.ai/api/gateway`) with `KILOCODE_API_KEY` JWT. Primary + fallback model: `xiaomi/mimo-v2-pro`. Retry with exponential backoff (4s → 8s → 16s). Rate limit handling (429 → 60s backoff).

---

## 5. Configuration

### Bot Config Key Settings

| Setting | Value | Note |
|---------|-------|------|
| `sl_pct` | 1.25% | Hard stop loss |
| `trailing_tp_trigger_pct` | 1.0% | Start trailing |
| `trailing_tp_delta_pct` | 1.0% | Trail distance |
| `default_leverage` | 10x | ROE calc |
| `dsl_enabled` | true | Tiered trailing |
| `stagnation_roe_pct` | 5.0% | Exit if ROE≥5% and stalled |
| `stagnation_minutes` | 60 | Stagnation timeout |
| `ai_mode` | true | AI decisions |
| `price_poll_interval` | 5 | Seconds |
| `price_call_delay` | 5.0 | Seconds between price calls |

### Environment Variables

All secrets in `/root/.openclaw/workspace/.env`:

| Variable | Purpose |
|----------|---------|
| `LIGHTER_ACCOUNT_INDEX` | 719758 |
| `LIGHTER_API_PRIVATE_KEY` | Trading key |
| `LIGHTER_PROXY_*` | SOCKS5 proxy (Webshare) |
| `TELEGRAM_BOT_TOKEN` | Alert bot |
| `TELEGRAM_CHAT_ID` | Alert channel |
| `KILOCODE_API_KEY` | JWT for Kilo Gateway |

---

## 7. Known Issues & Gotchas

### 🔴 Critical / Active

1. **SL Execution Bug** — `create_market_order_limited_slippage` accepted by API (HTTP 200) but not actually executing. Root cause: `avg_execution_price` acts as a hard limit on the exchange side, not just a slippage guard. Orders may silently fail to fill. Current workaround: `create_market_order_if_slippage` with 2% max slippage. Monitor closely.

2. **Position Re-detection Loop** — After bot restart, same positions get detected as "new" every sync cycle, creating duplicate DSLState entries. The two-cycle confirmation helps but doesn't fully solve it. Positions opened before restart lose their DSL state.

3. **Volume Quota** — Lighter account has a volume quota (initial 1000 TX). Failed/rejected orders still burn quota. When quota = 0, no orders execute. System has exponential backoff (60s→120s→300s) and quota prioritization (blocks new opens when quota < 50 to preserve for SL). May need account tier upgrade.

### 🟡 Moderate

4. **SOCKS5 Proxy Required** — Lighter API is geo-restricted. The bot patches `lighter.rest.RESTClientObject.__init__` to use `aiohttp_socks.ProxyConnector`. The `SignerClient` is subclassed (`ProxySignerClient`) to use proxy-configured `ApiClient`. Both REST and signer need the proxy.

5. **Bot Config Path** — All three systemd services use `projects/autopilot-trader/` paths. `ai-decisions.service` loads env directly from workspace `.env`.

6. **Mark Price Derivation** — Bot reverse-engineers mark price from `unrealized_pnl / size`. More accurate than `recent_trades` for ROE evaluation but depends on correct side detection.

7. **Close Verification Delays** — Progressive delays (5+10+15+20s = 50s total) after close order submission. During volatile markets, position may move against you during verification wait.

8. **Phantom Position Prevention** — Requires 2 consecutive sync cycles to confirm new positions. During the confirmation window, the bot sends a "pending confirmation" Telegram alert.

### 🟢 Minor

9. **Scanner Equity Hardcoded** — Scanner has `accountEquity: 60` hardcoded. Bot auto-scales position sizes based on actual balance vs scanner's config, so mismatch is handled but scanner output shows wrong absolute numbers.

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

> ⚠️ **NEVER provide account addresses without verifying from the .env file first.** Always check `LIGHTER_ACCOUNT_INDEX` in `.env`.

---

## Appendix A: File Structure

```
projects/autopilot-trader/
├── bot/
│   ├── bot.py                # Coordinator: init, main loop, tick orchestration
│   ├── config.py             # BotConfig dataclass, YAML loading
│   ├── config.yml            # Bot configuration
│   ├── config.example.yml    # Config template
│   ├── dsl.py                # Dynamic Stop Loss engine (tiered trailing)
│   ├── auth_helper.py        # REST API auth token manager
│   ├── healthcheck.py        # Health endpoint
│   ├── requirements.txt      # Python dependencies
│   ├── api/
│   │   ├── lighter_api.py    # DEX API wrapper: orders, positions, prices
│   │   └── proxy_patch.py    # SOCKS5 proxy monkey-patch
│   ├── core/
│   │   ├── models.py         # TrackedPosition + BotState dataclasses
│   │   ├── position_tracker.py # DSL + legacy trailing TP/SL
│   │   ├── signal_processor.py # Thin compatibility wrapper
│   │   ├── signal_handler.py # Scanner signal processing
│   │   ├── decision_handler.py # AI decision validation + dispatch
│   │   ├── executor.py       # AI open/close/close_all execution
│   │   ├── verifier.py       # Position verification + fill polling
│   │   ├── result_writer.py  # AI result IPC writing
│   │   ├── shared_utils.py   # Pacing, quota, market ID, outcome logging
│   │   ├── state_manager.py  # Save/load/reconcile state
│   │   ├── order_manager.py  # Quota checks + order pacing
│   │   └── execution_engine.py # Main tick + position processing
│   ├── alerts/
│   │   └── telegram.py       # Telegram alerts
│   ├── venv/                 # Python virtual environment
│   └── state/
│       ├── tracked_markets.json
│       └── bot_state.json
├── scanner/
│   ├── src/
│   │   ├── main.ts           # Entry point, module wiring
│   │   ├── types.ts          # All interfaces
│   │   ├── config.ts         # Constants + settings
│   │   ├── output.ts         # Console display + signals.json write
│   │   ├── position-sizing.ts # Risk-based sizing + safety checks
│   │   ├── direction-vote.ts # Long/short majority vote
│   │   ├── api/
│   │   │   ├── lighter.ts    # Lighter REST calls
│   │   │   └── okx.ts        # OKX klines + cache
│   │   └── signals/
│   │       ├── funding-spread.ts   # Funding rate spread score
│   │       ├── price-momentum.ts   # Daily price change score
│   │       ├── moving-average-alignment.ts # MA structure score
│   │       ├── order-block.ts      # Order block detection + score
│   │       └── oi-trend.ts         # OI change score
│   ├── scanner-daemon.sh     # Persistent loop wrapper
│   └── tests/
├── ai-decisions/
│   ├── ai_trader.py          # Thin coordinator — init, daemon loop
│   ├── cycle_runner.py       # Cycle orchestration pipeline
│   ├── db.py                 # SQLite decision journal
│   ├── llm_client.py         # Kilo Gateway HTTP client
│   ├── safety.py             # Hard safety rules
│   ├── llm/
│   │   └── parser.py         # Parse LLM JSON responses
│   ├── context/
│   │   ├── data_reader.py    # Read signals + positions
│   │   ├── pattern_engine.py # Learned pattern rules
│   │   ├── stats_formatter.py# Performance stats + regret
│   │   ├── prompt_builder.py # Prompt assembly w/ token budget
│   │   ├── sanitizer.py      # Prompt injection detection
│   │   └── token_estimator.py# Token counting (tiktoken)
│   ├── ipc/
│   │   └── bot_protocol.py   # IPC: decisions, results, halt
│   ├── config.json           # AI trader config
│   ├── prompts/
│   │   ├── system.txt        # System prompt for LLM
│   │   └── decision.txt      # Decision prompt template
│   └── state/
│       ├── patterns.json     # Trade pattern data
│       └── trader.db-*       # SQLite WAL/SHM
├── dashboard/
│   ├── app.py                # Flask app factory
│   ├── index.html            # Frontend (single-page)
│   ├── api/
│   │   ├── portfolio.py      # /api/portfolio endpoints
│   │   ├── scanner.py        # /api/scanner endpoints
│   │   ├── system.py         # /api/system endpoints
│   │   └── trader.py         # /api/trader endpoints
│   ├── utils.py              # Shared helpers (read_json, time_ago)
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
│   ├── __init__.py           # Python package marker
│   └── ipc_utils.py          # Shared IPC read/write helpers
├── archives/
│   ├── bot-scanner/          # Old OSA scanner (deleted)
│   ├── scanner/              # Archived: correlation-guard, funding-monitor, cleanup scripts
│   └── docs/                 # Completed modularization plans + test plans
├── docs/
│   ├── autopilot-trader.md   # This file
│   ├── cheatsheet.md         # Quick file map + patterns
│   ├── lighter-api.md        # Lighter API endpoints + SDK reference
│   ├── pocket-ideas.md       # Feature ideas backlog
│   ├── unified-dashboard-plan.md
│   └── lighter-quota-research.md
└── signals/
    └── signals.json          # Scanner output (live)
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
- **Two-cycle position confirmation** — New positions must appear in 2 consecutive syncs before tracking to prevent phantom detections from API lag.
- **Quota prioritization** — When volume quota < 50, block new opens to preserve quota for critical SL orders.
- **Graduated close retry** — Close failures retry with increasing delays (15s→60s→300s→900s) to avoid burning quota on impossible fills.
- **Change detection saves ~50% LLM calls** — AI trader hashes top 10 signals + positions and skips LLM call if unchanged.
- **Mark price from unrealized_pnl** — Derives mark price from PnL formula instead of using recent trades. More accurate for ROE evaluation.
