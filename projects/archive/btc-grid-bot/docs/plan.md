# BTC Smart Grid Bot — Plan

> Last updated: 2026-04-05 (rev 5 — live, operational)
> Author: John + Maaraa
> Status: ✅ Live — running since 2026-04-03 with daily resets

---

## Overview

A BTC-only AI-driven grid trading bot on Lighter DEX. Every morning the AI reads real market structure from BTC 15m/30m candles and sets grid levels at actual swing highs and lows — not arbitrary math. The bot farms daily BTC volatility by continuously buying low and selling high within that structure.

Layer 2 (macro grid) is planned but out of scope until Layer 1 is profitable and stable.

---

## How It Works

```
Every morning (06:00 UTC):
  1. AI Analyst reads last 48h of BTC 15m + 30m candles (OKX, free)
  2. Identifies swing highs → sell levels
                swing lows  → buy levels
  3. Capital Calculator checks: is account safe to deploy this grid?
  4. Bot cancels previous grid, places new limit orders

During the day (every 30s):
  5. Poll open orders for fills
  6. Buy filled → place sell one level up
  7. Sell filled → place buy one level down
  8. Price breaks outside range → pause, do not chase

Next morning: cancel all, start from step 1
```

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              btc-grid-bot                   │
│                                             │
│  ┌─────────────┐    ┌────────────────────┐  │
│  │  Analyst    │───▶│   Grid Manager     │  │
│  │  (06:00 UTC)│    │  place/fill/replace│  │
│  └─────────────┘    └─────────┬──────────┘  │
│                               │             │
│  ┌─────────────┐    ┌─────────▼──────────┐  │
│  │  OKX OHLCV  │    │   Lighter API      │  │
│  │  (free pub) │    │   (orders/fills)   │  │
│  └─────────────┘    └────────────────────┘  │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │  calculator.py  (safety check)       │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │  State: state/grid_state.json        │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │  Telegram Alerts                     │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

---

## AI Analyst

### Job
Run once per day at 06:00 UTC. Read BTC candles, identify real market structure, output grid levels as JSON.

### Input
- Last 48h of BTC 15m candles from OKX (free public API)
- Last 48h of BTC 30m candles from OKX

### Output
```json
{
  "date": "2026-03-31",
  "buy_levels":  [81200, 81800, 82400],
  "sell_levels": [83100, 83700, 84200],
  "range_low":   81200,
  "range_high":  84200,
  "confidence":  "high",
  "note":        "82,400 is key pivot. Clear swing structure.",
  "pause":       false,
  "pause_reason": null
}
```

### LLM prompt (what it asks)
```
You are a BTC market structure analyst.
Given the following 15m and 30m OHLCV candles for the last 48 hours,
identify the key swing highs and swing lows that would serve as
grid levels for a limit order grid bot.

Rules:
- Only return levels where price has clearly reversed or consolidated
- 4–8 buy levels, 4–8 sell levels
- No commentary, no filler — only structured JSON output

[candle data here]
```

### Fallback
If LLM fails or returns invalid JSON:
- Keep yesterday's levels
- Send Telegram alert: `⚠️ AI Analyst failed — using previous levels`
- Do not stop the bot

### LLM model
- OpenRouter (already in .env)
- Model: `anthropic/claude-3.5-sonnet` or `openai/gpt-4o`
- Cost: ~$0.001–0.003 per run (tiny)

---

## Capital Calculator

### Purpose
Before placing any order, verify the account can safely handle the full grid without margin risk.

### Inputs (from Lighter API + config)
```
account_equity     USDC balance
btc_price          current mark price
num_buy_levels     from analyst output
num_sell_levels    from analyst output
max_exposure_mult  from config (default 3.0×)
margin_reserve_pct from config (default 0.20)
```

### Core math
```python
max_notional      = account_equity * max_exposure_mult
reserved_margin   = account_equity * margin_reserve_pct
available_notional = max_notional - reserved_margin

# worst case: all buy levels fill simultaneously
worst_case_notional = num_buy_levels * size_per_level * btc_price

size_per_level = available_notional / (num_buy_levels + num_sell_levels) / btc_price
```

### Output (printed + logged)
```
═══════════════════════════════════════
  BTC Grid Bot — Capital Safety Check
═══════════════════════════════════════
  Account equity:      $1,000 USDC
  BTC mark price:      $83,000

  Max safe notional:   $3,000  (3× equity)
  Margin reserved:     $200    (20%)
  Available notional:  $2,800

  Grid: 5 buy + 5 sell levels
  Size per level:      0.0034 BTC  ($282/level)
  Worst case (all buys fill): $1,410

  ✅ SAFE — buffer: $1,390 remaining
═══════════════════════════════════════
```

### Blocks deployment if unsafe
```
  ❌ UNSAFE — reduce levels or increase equity
  Worst case $3,200 exceeds available $2,800
```

---

## Grid Manager

### Order placement
- Place all buy levels as resting limit orders below current price
- Place all sell levels as resting limit orders above current price
- Tag every order: `{"order_id": "...", "price": 82400, "side": "buy", "layer": 1, "status": "open"}`

### Fill detection loop (every 30s)
```
for each tracked order:
  if status changed to FILLED:
    log the fill
    send Telegram alert
    if buy filled → place sell at next level up
    if sell filled → place buy at next level down
```

### Pause logic
```
if current_price < range_low OR current_price > range_high:
  cancel all open orders
  set status = PAUSED
  alert: "⚠️ BTC outside grid range. Paused until morning reset."
  do NOT place new orders until next daily reset
```

### Daily reset sequence
```
06:00 UTC:
  1. Cancel all open grid orders
  2. Run AI Analyst → get new levels
  3. Run Capital Calculator → verify safety
  4. Place new orders
  5. Alert: "📊 Grid reset. Range $X–$Y · N levels · BTC @ $Z"
```

---

## Risk Management

### Cross margin awareness
- No per-order leverage — account is cross margin
- Risk controlled entirely by total notional exposure
- Calculator runs before every deployment

### Hard limits
- Max total notional: 3× equity (configurable)
- Margin reserve: 20% always untouched
- Daily loss limit: if equity drops >8% in a day → cancel all, alert, do not resume until manual restart
- No chasing: if price trends hard outside range → pause, wait for reset

---

## Telegram Alerts

| Event | Message |
|-------|---------|
| Grid reset | `📊 Grid reset. Range $82,400–$84,200 · 5 buy + 5 sell · BTC @ $83,100` |
| Buy filled | `✅ Buy filled @ $82,400 · 0.0034 BTC · Sell placed @ $83,100` |
| Sell filled | `✅ Sell filled @ $83,100 · 0.0034 BTC · Buy placed @ $82,400` |
| Grid paused | `⚠️ BTC @ $80,900 — below grid. Paused until 06:00 UTC reset.` |
| AI failed | `⚠️ AI Analyst failed — using yesterday's levels` |
| Daily loss | `🚨 Equity down 8%. All orders cancelled. Manual restart required.` |
| Daily PnL | `📈 Daily PnL: +$34 · 12 fills · Equity $1,034` |

---

## File Structure (current — see `docs/architecture.md` for full module map)

```
projects/btc-grid-bot/
├── main.py                      # Entry point + daily reset loop
├── config.yml                   # All settings
├──
├── analysis/
│   ├── analyst.py               # AI Analyst (OKX + LLM + market intel + indicators)
│   └── direction.py             # Direction score (EMA + price action)
├── api/
│   └── lighter.py               # Lighter DEX WebSocket API wrapper
├── core/
│   ├── grid_manager.py          # Order placement, fills, rolls, pause, recovery
│   ├── calculator.py            # Capital safety check with ATR + volatility sizing
│   ├── capital.py               # CapitalMixin (equity checks, position-aware sizing)
│   ├── order_manager.py         # OrderMixin (place, cancel, track orders)
│   ├── memory_layer.py          # Session-end logging, AI feedback loop
│   └── intelligence.py          # PatternAnalyzer (historical recommendations)
├── indicators/                  # 12 technical indicator modules
│   ├── atr.py, adx.py, ema.py, bollinger.py, volume.py
│   ├── regime.py, trend_skew.py, oi_divergence.py, direction_score.py
│   ├── funding.py, time_adj.py, composite.py, helpers.py, format_indicators.py
│   └── __init__.py              # Re-exports all indicators
├── market/
│   └── intel.py                 # Coinalyze client (funding, OI, liquidations)
├── notifications/
│   ├── alerts.py                # send_alert() → Telegram
│   └── telegram_bot.py          # Interactive bot (/pause, /resume, /cancel, /status)
├── state/                       # Runtime state (gitignored)
│   ├── grid_state.json
│   └── deployments/
├── tools/
│   └── stress_test.py           # Stress test runner
├── memory_query.py              # CLI: query historical patterns
├── intelligence_dashboard.py    # Dashboard: visualize trading patterns
├── test_direction.py            # Direction score tests
├── docs/
│   ├── plan.md                  # This file
│   ├── architecture.md          # Full architecture map + data flow
│   ├── cheatsheet.md            # Quick reference
│   └── phase2-short-grid.md     # Short grid documentation
├── .env                         # Secrets (gitignored)
└── LEGACY SHIMS (import redirects only, kept for backward compat):
    analyst.py, calculator.py, grid.py, lighter_api.py,
    market_intel.py, tg_alerts.py, indicators.py
```

---

## Config (`config.yml`)

```yaml
bot:
  account_id: 719758
  margin_mode: cross

capital:
  starting_equity: 1000        # USDC
  max_exposure_multiplier: 3.0
  margin_reserve_pct: 0.20

grid:
  reset_time_utc: "06:00"
  min_levels: 4
  max_levels: 8
  poll_interval_seconds: 30

risk:
  daily_loss_limit_pct: 0.08   # pause all at -8% equity drop

llm:
  model: "anthropic/claude-3.5-sonnet"
  max_tokens: 600
  temperature: 0.1

data:
  timeframes: ["15m", "30m"]
  candles_lookback: 200        # ~48h of 15m candles

telegram:
  daily_pnl_time_utc: "23:30"
```

---

## Implementation Phases

### Phase 0 — Capital Calculator ✅
- [x] `core/calculator.py` — reads equity from Lighter, computes safe sizing
- [x] Prints safety table to terminal
- [x] Returns `size_per_level` for use by grid manager
- [x] Run standalone: `python calculator.py`

### Phase 1 — Foundation ✅
- [x] Project structure + `config.yml`
- [x] `api/lighter.py` — adapted from v1 (get equity, place order, cancel order, get open orders)
- [x] `state/grid_state.json` — read/write order state
- [x] `notifications/alerts.py` — alerts

### Phase 2 — Grid Manager ✅
- [x] `core/grid_manager.py` — place buy/sell levels, tag orders
- [x] Fill detection loop (poll every 30s)
- [x] Fill → replacement order logic
- [x] Pause logic (price outside range)
- [x] Manual test: place one level, verify fill detection works

### Phase 3 — AI Analyst ✅
- [x] `analysis/analyst.py` — fetch OKX candles
- [x] Build LLM prompt from candles
- [x] Parse LLM response → validated level list
- [x] Fallback: use previous levels if LLM fails
- [x] Manual test: run analyst, inspect output levels

### Phase 4 — Main Loop ✅
- [x] `main.py` — ties everything together
- [x] Daily reset scheduler (06:00 UTC)
- [x] Daily PnL report (23:30 UTC)
- [x] Daily loss limit check
- [x] systemd service: `btc-grid-bot.service`

### Phase 5 — Hardening ✅
- [x] Edge cases: gap fill, LLM timeout, Lighter API error
- [x] Orphan sell detection and handling
- [x] BTC position recovery logic (dust / small / large)
- [x] Config hot-reload
- [x] Loss lockout with 24h cooldown
- [x] Telegram `/status` via state file
- [x] Running live since 2026-04-03

### Phase 6 — Intelligence (added post-launch) ✅
- [x] 12 technical indicator modules (ATR, ADX, EMA, BB, volume, regime, OI divergence, etc.)
- [x] Direction score engine (long/short/pause recommendations)
- [x] Market intel: Coinalyze integration (funding rates, open interest, liquidations)
- [x] Volatility-adaptive sizing (ATR-based)
- [x] Time-of-day adjustment (Asian/London/NY sessions)
- [x] Funding rate multiplier
- [x] Auto-compounding position sizing
- [x] Trailing stop loss (4% from peak, 8% static floor)

### Phase 7 — Memory Layer ✅
- [x] `core/memory_layer.py` — session-end logging with JSON fallback
- [x] `core/intelligence.py` — PatternAnalyzer (direction bias, timing, roll costs, regime)
- [x] `memory_query.py` — CLI: `python3 memory_query.py`
- [x] `intelligence_dashboard.py` — Dashboard: `python3 intelligence_dashboard.py --days 14`
- [x] AI feedback loop — analyst adjusts confidence based on historical patterns
- [x] Auto-stores session PnL, trades, rolls, issues before each deploy

### Phase 8 — Telegram Integration ✅
- [x] `notifications/telegram_bot.py` — interactive bot with commands
- [x] `/pause`, `/resume`, `/cancel`, `/status` commands
- [x] Command file communication (`state/bot_command.json`)
- [x] Service: `btc-grid-telegram`

---

## What We're NOT Building (yet)
- Layer 2 macro grid (separate account, later)
- Multi-coin support
- TradingView integration
- Scanner / signal system

## Relationship to Existing Bots
- **V1 bot** (`projects/autopilot-trader/`) — leave running as-is
- **V2 bot** (`projects/autopilot-trader-v2/`) — leave as-is, not touched
- **This bot** — completely independent, new repo, new service
