# BTC Smart Grid Bot — Plan

> Last updated: 2026-03-31 (rev 3 — Layer 1 only, clean scope)
> Author: John + Maaraa
> Status: Ready to build

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

## File Structure

```
projects/btc-grid-bot/
├── docs/
│   └── plan.md              # this file
├── config.yml               # all settings
├── main.py                  # entry point + daily reset loop
├── analyst.py               # OKX data fetch + LLM → grid levels
├── grid.py                  # place/cancel/track orders on Lighter
├── calculator.py            # capital safety check
├── lighter_api.py           # Lighter API wrapper (adapted from v1)
├── telegram.py              # alerts
├── state/
│   └── grid_state.json      # runtime state (gitignored)
└── tests/
    ├── test_analyst.py
    ├── test_grid.py
    └── test_calculator.py
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

### Phase 0 — Capital Calculator
- [ ] `calculator.py` — reads equity from Lighter, computes safe sizing
- [ ] Prints safety table to terminal
- [ ] Returns `size_per_level` for use by grid manager
- [ ] Run standalone: `python calculator.py`

### Phase 1 — Foundation
- [ ] Project structure + `config.yml`
- [ ] `lighter_api.py` — adapted from v1 (get equity, place order, cancel order, get open orders)
- [ ] `state/grid_state.json` — read/write order state
- [ ] `telegram.py` — alerts

### Phase 2 — Grid Manager
- [ ] `grid.py` — place buy/sell levels, tag orders
- [ ] Fill detection loop (poll every 30s)
- [ ] Fill → replacement order logic
- [ ] Pause logic (price outside range)
- [ ] Manual test: place one level, verify fill detection works

### Phase 3 — AI Analyst
- [ ] `analyst.py` — fetch OKX candles
- [ ] Build LLM prompt from candles
- [ ] Parse LLM response → validated level list
- [ ] Fallback: use previous levels if LLM fails
- [ ] Manual test: run analyst, inspect output levels

### Phase 4 — Main Loop
- [ ] `main.py` — ties everything together
- [ ] Daily reset scheduler (06:00 UTC)
- [ ] Daily PnL report (23:30 UTC)
- [ ] Daily loss limit check
- [ ] systemd service: `btc-grid.service`

### Phase 5 — Hardening
- [ ] Tests for calculator, analyst, grid logic
- [ ] Edge cases: gap fill, LLM timeout, Lighter API error
- [ ] `/status` Telegram command
- [ ] Run live on small size for 1 week, review PnL

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
