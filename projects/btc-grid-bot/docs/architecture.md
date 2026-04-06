# BTC Grid Bot — Architecture Map

> Last updated: 2026-04-05
> Total: ~5K lines Python, 12 modules, 2 services

---

## High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    BTC Smart Grid Bot                           │
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌─────────────────┐ │
│  │  OKX Public  │────▶│  AI Analyst  │────▶│  Grid Manager   │ │
│  │  REST API    │     │  (06:00 UTC) │     │  place/monitor  │ │
│  │  15m + 30m   │     │              │     │  fill/replace   │ │
│  └──────────────┘     └───────┬──────┘     └────────┬────────┘ │
│                               │                     │          │
│  ┌──────────────┐     ┌───────▼──────┐     ┌────────▼────────┐ │
│  │  Market      │────▶│ Indicators   │     │  Lighter DEX    │ │
│  │  Intel       │     │ (12 modules) │     │  WebSocket API  │ │
│  └──────────────┘     └──────────────┘     └─────────────────┘ │
│                                                                 │
│  ┌───────────────────┐     ┌─────────────────────────────────┐ │
│  │  Memory Layer     │◀───▶│  Intelligence Engine            │ │
│  │  (session logs)   │     │  (pattern analysis)             │ │
│  └────────┬──────────┘     └─────────────────────────────────┘ │
│           │                                                     │
│  ┌────────▼──────────┐     ┌─────────────────────────────────┐ │
│  │  Telegram Bot     │     │  Config hot-reload              │ │
│  │  (pause/resume/   │     │  Trailing loss limit            │ │
│  │   status commands)│     │  Auto-compounding sizing        │ │
│  └───────────────────┘     └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Module Map

### Core (runtime — always loaded)

| Module | Lines | Role |
|--------|-------|------|
| `main.py` | ~240 | Entry point, daily reset loop, config reload, Telegram commands |
| `core/grid_manager.py` | 1600 | Order placement, fill detection, roll logic, pause/resume, position recovery |
| `core/calculator.py` | 180 | Capital safety check with ATR sizing, volatility config, compounding |
| `core/capital.py` | 460 | `CapitalMixin` — equity checks, position-aware sizing |
| `core/order_manager.py` | 205 | `OrderMixin` — order placement, cancellation, fill tracking |
| `api/lighter.py` | 320 | Lighter DEX WebSocket API wrapper (orders, fills, equity, balance) |
| `state/grid_state.json` | runtime | Live state: orders, trades, PnL, pause status, orphan sells |

### Analysis (runs at daily reset)

| Module | Lines | Role |
|--------|-------|------|
| `analysis/analyst.py` | 850 | AI Analyst — fetches OKX candles + market intel + indicators → LLM prompt → grid levels |
| `analysis/direction.py` | 56 | Direction score — EMA + price action → long/short/pause recommendation |

### Technical Indicators (supporting analyst)

| Module | Role |
|--------|------|
| `indicators/bollinger.py` | Bollinger Bands (SMA ± 2σ) |
| `indicators/atr.py` | Average True Range (volatility measure) |
| `indicators/adx.py` | ADX (trend strength) |
| `indicators/ema.py` | EMA calculation (single + multi-period) |
| `indicators/trend_skew.py` | Trend skew score (bullish/bearish bias) |
| `indicators/volume.py` | Volume profile + spike detection |
| `indicators/regime.py` | Market regime detection (trending/ranging/choppy) |
| `indicators/funding.py` | Funding rate adjustment multiplier |
| `indicators/time_adj.py` | Time-of-day session adjustment (Asian/London/NY) |
| `indicators/oi_divergence.py` | Open interest divergence detection |
| `indicators/direction_score.py` | Composite direction score from multiple indicators |
| `indicators/composite.py` | `gather_indicators()` — runs all indicators, returns summary |
| `indicators.py` | Root shim — re-exports entire indicators package |

### Market Data

| Module | Role |
|--------|------|
| `market/intel.py` | Coinalyze client — funding rates, open interest, liquidations |

### Notifications

| Module | Role |
|--------|------|
| `notifications/alerts.py` | Simple `send_alert()` → Telegram |
| `notifications/telegram_bot.py` | Interactive bot — `/pause`, `/resume`, `/cancel`, `/status` commands |

### Memory & Intelligence (new — 2026-04-05)

| Module | Role |
|--------|------|
| `core/memory_layer.py` | Session-end logging, pattern recommendations, AI feedback loop |
| `core/intelligence.py` | `PatternAnalyzer` — direction bias, timing patterns, roll costs, regime performance |
| `memory_query.py` | CLI tool: `python3 memory_query.py` to query historical patterns |
| `intelligence_dashboard.py` | Dashboard: `python3 intelligence_dashboard.py --days 14` |

### Legacy Shim Files (root dir — import redirects only)

| File | Redirects To | Why |
|------|-------------|-----|
| `analyst.py` | `analysis/analyst.py` | Backward compat |
| `calculator.py` | `core/calculator.py` | Backward compat |
| `grid.py` | `core/grid_manager.py` | Backward compat |
| `lighter_api.py` | `api/lighter.py` | Backward compat |
| `market_intel.py` | `market/intel.py` | Backward compat |
| `tg_alerts.py` | `notifications/alerts.py` | Backward compat |
| `indicators.py` | `indicators/` package | Backward compat |

### Services

| Service | Command | Status File |
|---------|---------|-------------|
| `btc-grid-bot` | `systemctl status btc-grid-bot` | `state/grid_state.json` |
| `btc-grid-telegram` | `systemctl status btc-grid-telegram` | Interactive bot |

---

## Data Flow (Detailed)

### Daily Reset (06:00 UTC)

```
main.py startup()
  ├── api.get_equity()           → check if account is safe
  ├── api.get_btc_price()        → current mark price
  ├── Check loss lockout         → 24h ban after -8% equity drop
  ├── Check BTC position         → dust/cleanup/recover/exit
  ├── Adopt existing orders      → if grid from yesterday still valid
  ├── sanity_cleanup()           → cancel orphaned/stale orders
  │
  └── run_analyst(cfg, equity, price, grid_manager)
       ├── fetch_candles("15m", 200)     → OKX public REST
       ├── fetch_candles("30m", 200)     → OKX public REST
       ├── gather_all_intel(cfg)         → Coinalyze (funding, OI, liquidations)
       ├── gather_indicators(candles)    → all 12 indicator modules
       ├── memory_layer.get_intel()      → historical pattern recommendations
       │
       ├── build_prompt(candles + intel + indicators + memory)
       ├── call_llm(prompt)              → OpenRouter → claude-sonnet
       └── parse_response()              → validate JSON → return levels
  │
  ├── check_direction(cfg, price)  → EMA + price action → long/short/pause
  ├── resolve_direction()          → AI + direction score → final decision
  ├── calculate_grid()             → ATR sizing + volatility + compounding + time + funding
  │
  └── gm.deploy(levels, equity, price, direction)
       ├── cancel_all()            → clear old orders
       ├── place_buy_orders()      → limit orders below current price
       ├── place_sell_orders()     → limit orders above current price
       ├── _save_state()           → persist to grid_state.json
       └── send_alert()            → Telegram: "Grid deployed"
```

### Monitor Loop (every 30s)

```
while True:
  ├── config hot-reload check
  ├── check_telegram_commands()  → pause/cancel/resume
  │
  ├── api.get_btc_price()
  ├── gm.check_fills(price)
  │    ├── Detect filled orders
  │    ├── Buy filled → place sell order one level up
  │    ├── Sell filled → place buy order one level down
  │    └── Detect orphan sells (no BTC to cover) → skip replacement
  │
  ├── Trailing loss limit check
  │    ├── peak_equity tracking
  │    ├── trailing_stop = peak × (1 - 0.04)
  │    ├── static_floor = reset × (1 - 0.08)
  │    └── if equity < max(trailing, static) → lockout 24h
  │
  ├── Daily PnL report (23:30 UTC)
  │
  └── Pause check (price outside range) → stop loop, alert
```

### Telegram Commands

```
User sends /pause  → write state/bot_command.json → next poll reads → cancel orders
User sends /resume → write state/bot_command.json → next poll reads → redeploy same levels
User sends /cancel → write state/bot_command.json → next poll reads → emergency cancel all
User sends /status → reply with current grid state, equity, PnL, pending orders
```

---

## Config (`config.yml`) — Full Reference

```yaml
bot:
  account_id: 719758
  margin_mode: cross

capital:
  starting_equity: 1000          # Historical reference
  max_exposure_multiplier: 3.0   # Max leverage
  margin_reserve_pct: 0.20       # 20% always untouched

grid:
  reset_time_utc: "06:00"
  min_levels: 4
  max_levels: 8
  poll_interval_seconds: 30      # Check fills every 30s

risk:
  daily_loss_limit_pct: 0.08     # Static floor: -8% from reset
  trailing_loss_pct: 0.04        # Trailing stop: -4% from peak

llm:
  model: "anthropic/claude-3.5-sonnet"
  max_tokens: 600
  temperature: 0.1

data:
  timeframes: ["15m", "30m"]
  candles_lookback: 200          # ~48h of 15m candles

telegram:
  daily_pnl_time_utc: "23:30"

volatility:                       # Optional — auto-adjusts sizing
  atr_multiplier: 1.5            # Size scales with volatility
  regime_adjustment: true        # Reduce size in choppy markets
```

---

## State File (`state/grid_state.json`) — Key Fields

```json
{
  "active": true,
  "paused": false,
  "levels": { "buy": [66450, ...], "sell": [67650, ...] },
  "range_low": 66450,
  "range_high": 68400,
  "size_per_level": 0.001593,
  "orders": [ ... ],
  "last_reset": "2026-04-05T19:18:51Z",
  "daily_pnl": -0.81,
  "fill_count": 4,
  "equity_at_reset": 85.19,
  "peak_equity": 85.19,
  "realized_pnl": -0.81,
  "trades": [ ... ],
  "grid_direction": "long",
  "roll_count": 5,
  "orphan_sells": [ ... ],
  "volume_spike_cooldown_until": "..."
}
```

---

## What This Bot Is NOT

- ❌ Not a signal generator / scanner
- ❌ Not connected to TradingView
- ❌ Not multi-coin
- ❌ Not a high-frequency bot
- ❌ Not the V1 autopilot-trader (`projects/autopilot-trader/`)
- ❌ Not the V2 trader (`projects/autopilot-trader-v2/`)

## What's Missing (not built yet)

- TradingView webhook integration for external signals
- Real DataCollector (Lighter API streaming)
- AIDecisionEngine with real LLM re-evaluation during the day
- Backtesting framework
- Multi-account support
