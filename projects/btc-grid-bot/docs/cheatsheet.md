# BTC Grid Bot — Quick Reference

> Last updated: 2026-04-01  
> Project: btc-grid-bot  
> Location: `/root/.openclaw/workspace/projects/btc-grid-bot/`

---

## 🚀 Quick Start

```bash
# Run the bot
cd /root/.openclaw/workspace/projects/btc-grid-bot/
./run.sh

# Check status
sudo systemctl status btc-grid-bot

# View logs
journalctl -u btc-grid-bot -f
```

---

## 📁 File Structure

```
btc-grid-bot/
├── main.py                 # Entry point + daily reset loop
├── config.yml              # All settings (see below)
├── analyst.py              # AI Analyst (06:00 UTC)
├── grid.py                 # Grid Manager (orders/fills)
├── calculator.py           # Capital safety check
├── lighter_api.py          # Lighter API wrapper
├── telegram.py             # Telegram alerts
├── market_intel.py         # OKX data fetch
├── indicators.py           # Technical indicators
├── state/                  # Runtime state (gitignored)
│   └── grid_state.json
├── logs/                   # Log files
└── docs/
    ├── plan.md            # Full project plan
    └── cheatsheet.md      # You are here
```

---

## ⚙️ Config (`config.yml`)

```yaml
bot:
  account_id: 719758
  margin_mode: cross

capital:
  starting_equity: 1000
  max_exposure_multiplier: 3.0
  margin_reserve_pct: 0.20

grid:
  reset_time_utc: "06:00"
  min_levels: 4
  max_levels: 8
  poll_interval_seconds: 30

risk:
  daily_loss_limit_pct: 0.08

llm:
  model: "anthropic/claude-3.5-sonnet"
  max_tokens: 600
  temperature: 0.1

data:
  timeframes: ["15m", "30m"]
  candles_lookback: 200

telegram:
  daily_pnl_time_utc: "23:30"
```

**Edit config:**
```bash
nano config.yml
```

---

## 🧠 AI Analyst (`analyst.py`)

**Run daily at 06:00 UTC:**
```bash
python analyst.py
```

**Outputs JSON:**
```json
{
  "date": "2026-04-01",
  "buy_levels": [81200, 81800, 82400],
  "sell_levels": [83100, 83700, 84200],
  "range_low": 81200,
  "range_high": 84200,
  "confidence": "high",
  "note": "82,400 is key pivot.",
  "pause": false
}
```

**Test manually:**
```bash
python analyst.py --test
```

---

## 🧮 Capital Calculator (`calculator.py`)

**Run before placing orders:**
```bash
python calculator.py
```

**Safety check formula:**
```
max_notional = equity × 3.0
reserved = equity × 0.20
available = max_notional - reserved
size_per_level = available / (num_buy + num_sell) / btc_price
```

**Test with custom equity:**
```bash
python calculator.py --equity 1500
```

---

## 📊 Grid Manager (`grid.py`)

**Place grid orders:**
```python
from grid import GridManager

gm = GridManager()
gm.place_grid(buy_levels, sell_levels)
```

**Check fills (run every 30s):**
```python
gm.check_fills()
```

**Pause if price outside range:**
```python
if price < range_low or price > range_high:
    gm.pause()
```

---

## 🔄 Main Loop (`main.py`)

**Scheduler:**
- 06:00 UTC: Daily reset (cancel + new grid)
- 23:30 UTC: Daily PnL report
- Every 30s: Check fills
- Equity drop >8%: Cancel all, alert

**Run as service:**
```bash
sudo systemctl start btc-grid-bot
sudo systemctl enable btc-grid-bot
```

**Service file:** `/root/.openclaw/workspace/projects/btc-grid-bot/btc-grid-bot.service`

---

## 🚨 Telegram Alerts

**Key alerts:**
- `📊 Grid reset. Range $X–$Y · N buy + N sell · BTC @ $Z`
- `✅ Buy filled @ $X · Size · Sell placed @ $Y`
- `✅ Sell filled @ $X · Size · Buy placed @ $Y`
- `⚠️ BTC @ $X — below/above grid. Paused until 06:00 UTC.`
- `🚨 Equity down 8%. All orders cancelled. Manual restart required.`

**Send custom alert:**
```python
from telegram import send_alert
send_alert("Your message here")
```

---

## 🔧 Common Operations

**Restart bot:**
```bash
sudo systemctl restart btc-grid-bot
```

**Check bot status:**
```bash
sudo systemctl status btc-grid-bot
```

**View recent logs:**
```bash
tail -f /var/log/btc-grid-bot.log
```

**Manual grid reset:**
```bash
python main.py --reset
```

**Force pause:**
```bash
echo "pause" > state/grid_state.json
```

**Force resume:**
```bash
echo "running" > state/grid_state.json
```

---

## 🐛 Troubleshooting

| Issue | Check |
|-------|-------|
| Bot not running | `systemctl status btc-grid-bot` |
| No orders placed | Check API keys in config, Lighter connection |
| AI fails | Check LLM credentials, candle data fetch |
| Orders not filling | BTC price outside grid range |
| Balance issues | `calculator.py` safety check failed |

**API Keys:** Stored in `.env` file (gitignored)

**State file:** `state/grid_state.json` (contains current grid levels, order IDs, pause status)

---

## 📈 Performance Metrics

**Track in logs:**
- Daily PnL
- Number of fills
- Win rate
- Max drawdown

**View daily PnL:**
```bash
grep "Daily PnL" /var/log/btc-grid-bot.log | tail -5
```

---

## 🔒 Security Notes

- Never commit `.env` or `state/` to git
- Use cross margin with 20% reserve
- Daily loss limit: 8% equity drop auto-stop
- Grid pauses if price breaks range

---

## 📚 Full Documentation

- Project Plan: `/root/.openclaw/workspace/projects/btc-grid-bot/docs/plan.md`
- This Cheatsheet: `/root/.openclaw/workspace/projects/btc-grid-bot/docs/cheatsheet.md`
- AGENTS.md: `/root/.openclaw/workspace/AGENTS.md`