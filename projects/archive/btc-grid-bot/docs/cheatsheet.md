# BTC Grid Bot — Quick Reference (Updated 2026-04-05)

---

## 🚀 Quick Start

```bash
# Check status
sudo systemctl status btc-grid-bot
sudo systemctl status btc-grid-telegram

# View logs
sudo journalctl -u btc-grid-bot -f --since "10 min ago"

# Restart service
sudo systemctl restart btc-grid-bot
```

---

## 📁 Current File Structure

```
projects/btc-grid-bot/
├── main.py                  # Entry point + daily reset loop
├── config.yml               # All settings (see below)
│
├── analysis/
│   ├── analyst.py           # AI Analyst (06:00 UTC) — OKX + market intel + indicators → LLM
│   └── direction.py         # Direction score (EMA + price action)
├── api/
│   └── lighter.py           # Lighter DEX WebSocket API wrapper
├── core/
│   ├── grid_manager.py      # Order placement, fills, rolls, pause, recovery
│   ├── calculator.py        # Capital safety check with ATR + volatility sizing
│   ├── capital.py           # CapitalMixin (equity, position-aware sizing)
│   ├── order_manager.py     # OrderMixin (place, cancel, track)
│   ├── memory_layer.py      # Session-end logging + AI feedback loop
│   └── intelligence.py      # PatternAnalyzer (historical recommendations)
├── indicators/              # 12 modules: ATR, ADX, EMA, BB, volume, regime, OI, etc.
├── market/
│   └── intel.py             # Coinalyze (funding, OI, liquidations)
├── notifications/
│   ├── alerts.py            # send_alert() → Telegram
│   └── telegram_bot.py      # Interactive bot (/pause, /resume, /cancel, /status)
├── state/                   # Runtime state (gitignored)
│   ├── grid_state.json
│   └── deployments/
│
├── memory_query.py          # CLI: python3 memory_query.py
├── intelligence_dashboard.py # Dashboard: python3 intelligence_dashboard.py --days 14
└── docs/
    ├── plan.md              # Full project plan (phases + checkboxes)
    ├── architecture.md      # Module map + data flow diagrams
    └── cheatsheet.md        # You are here
```

---

## 🧠 Architecture (simplified)

```
Daily Reset (06:00 UTC):
  OKX candles (15m/30m)  →  AI Analyst  →  Grid Levels  →  Grid Manager
  Market Intel (Coinalyze) ↗               ↗               ↗
  Technical Indicators   ↗               ↗                ↗
  Memory Layer           ↗               ↗                ↗

Monitor Loop (every 30s):
  Check fills → Replace orders → Trailing loss check → PnL alert (23:30)

Telegram Commands:
  /pause   → Cancel all orders
  /resume  → Redeploy same levels (no AI)
  /cancel  → Emergency cancel + pause
  /status  → Current grid state
```

**See `docs/architecture.md` for full module map and data flow.**

---

## ⚙️ Config (`config.yml`)

```yaml
bot:
  account_id: 719758
  margin_mode: cross

capital:
  starting_equity: 1000        # Historical reference
  max_exposure_multiplier: 3.0
  margin_reserve_pct: 0.20

grid:
  reset_time_utc: "06:00"
  min_levels: 4
  max_levels: 8
  poll_interval_seconds: 30

risk:
  daily_loss_limit_pct: 0.08   # Static floor: -8% from reset
  trailing_loss_pct: 0.04      # Trailing stop: -4% from peak

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

## 📊 Grid Manager

### Place grid orders
```python
from core.grid_manager import GridManager

# main.py does this automatically in startup()
gm = GridManager(cfg, api)
await gm.deploy(levels, equity, price, direction="long")
```

### Monitor fills
```python
await gm.check_fills(price)
# Buy filled → place sell one level up
# Sell filled → place buy one level down
# Orphan sell (no BTC) → log, skip replacement
```

### Pause if price outside range
```python
if price < range_low or price > range_high:
    gm._pause("Price outside grid range")
```

### Position recovery
```python
# Detected BTC position at startup:
#   <1% equity → dust, cleanup and deploy fresh
#   1-10%      → build grid around existing position
#   >10%       → close position via recovery mode
```

---

## 🧮 Capital Calculator

```python
from core.calculator import calculate_grid

calc = calculate_grid(
    equity, price, num_buy, num_sell,
    max_exposure_mult=3.0,
    margin_reserve_pct=0.20,
    atr_pct=atr_value,        # Scales with volatility
    volatility=vol_cfg,       # Volatility adjustments
    compounding_mult=1.05,    # Auto-compound sizing
    time_adj=1.0,             # Session multiplier
    funding_adj=1.0,          # Funding rate multiplier
    direction="long",         # Affects sizing
)
# Returns {"safe": True, "size_per_level": 0.001593, ...}
```

---

## 🔒 Risk Management

| Rule | Value | Action |
|------|-------|--------|
| Max leverage | 3× equity | Sizing limiter |
| Margin reserve | 20% | Never used |
| Static loss limit | -8% from reset | Cancel all, 24h lockout |
| Trailing loss | -4% from peak | Cancels all, 24h lockout |
| Daily reset | 06:00 UTC | Full grid rebuild |

---

## 📈 Memory Layer

### Session logging (automatic)
Before each deploy, `_log_session_end()` saves to `state/bot-memory.json`:
- Session PnL, trades, rolls, issues
- Equity at reset, realized PnL
- Direction score, confidence
- Grid range, fill count

### Query historical patterns
```bash
python3 memory_query.py              # Latest session
python3 memory_query.py --last 5     # Last 5 sessions
```

### Intelligence dashboard
```bash
python3 intelligence_dashboard.py --days 14
# Outputs: direction bias, timing patterns, roll costs,
#          regime performance, losing streak analysis
```

### AI feedback loop
Analyst pulls memory intel each run → adjusts confidence based on what's been working:
```python
# In analysis/analyst.py
from core.memory_layer import MemoryLayer
intel = MemoryLayer().get_recs()
# Applies directional bias correction, timing adjustments
```

---

## 🚨 Telegram Alerts

| Event | Message |
|-------|---------|
| Grid deployed | `✅ Grid deployed (LONG) · BTC @ $X · Direction score: +3 (high)` |
| Buy filled | `✅ Buy filled @ $X · Sell placed @ $Y` |
| Sell filled | `✅ Sell filled @ $X · Buy placed @ $Y` |
| Orphan sell | `⚠️ Sell filled without BTC — orphan fill at $X` |
| Grid paused | `⏸ Grid paused — price outside range` |
| Loss limit | `🚨 LOSS LIMIT REACHED! 🔒 Locked for 24h` |
| Daily PnL | `📊 Daily PnL Report · Win rate: X% · Equity: $Y` |
| Volume spike | `📊 Volume spike: 3.2x avg, direction=bullish` |

### Telegram Commands
```
/pause    → Cancel all orders, pause grid
/resume   → Redeploy same levels (no new AI analysis)
/cancel   → Emergency cancel all + pause
/status   → Current grid state, equity, pending orders
```

---

## 🔧 Troubleshooting

| Issue | Check |
|-------|-------|
| Bot not running | `systemctl status btc-grid-bot` |
| No orders placed | Check API keys in `.env`, Lighter connection |
| AI fails | Check OpenRouter credentials, OKX candle fetch |
| Orders not filling | Price outside grid range, or bot paused |
| Orphan sells | Price moved through sells faster than buys could fill — bot handles this, no action needed |
| Loss lockout | Check `state/loss_lockout.json` — auto-unlocks after 24h |

**Log files:**
```bash
# Recent bot logs
sudo journalctl -u btc-grid-bot --since "1 hour ago"

# Fills only
sudo journalctl -u btc-grid-bot --since "today" | grep -i fill

# Errors only
sudo journalctl -u btc-grid-bot --since "today" | grep -i error
```

**State files:**
- `state/grid_state.json` — current grid state
- `state/bot-memory.json` — session-end logs
- `state/loss_lockout.json` — if locked out
- `state/deployments/` — historical deployment logs

---

## 🔒 Security

- `.env` contains secrets — never committed
- `state/` runtime files — never committed
- Cross margin with 20% reserve
- Lighter API only (no external order routing)
- OKX candles only (public API, no keys needed)
