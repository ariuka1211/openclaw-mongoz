# Grid Bot — Session Navigation Guide

> For Maaraa starting a new session. Read this first.

---

## 🗺️ Quick Path: "I need to change X, where do I look?"

| Task | Start here | Then read |
|------|-----------|-----------|
| **Change grid sizing / leverage** | `core/calculator.py` | `config.yml` → `capital:` section |
| **Change how AI picks levels** | `analysis/analyst.py` | `indicators/` for what data AI sees |
| **Change fill / order replacement logic** | `core/grid_manager.py` | `core/order_manager.py` |
| **Loss limits / risk rules** | `main.py` → trailing loss section | `config.yml` → `risk:` section |
| **Telegram commands / alerts** | `notifications/telegram_bot.py` | `notifications/alerts.py` |
| **Market data (funding, OI)** | `market/intel.py` | `indicators/funding.py` |
| **Technical indicators** | `indicators/__init__.py` | individual files in `indicators/` |
| **Direction score (long/short)** | `analysis/direction.py` | `indicators/direction_score.py` |
| **Memory layer / patterns** | `core/memory_layer.py` | `core/intelligence.py` |
| **Startup flow** | `main.py` → `startup()` | `core/grid_manager.py` → `deploy()` |
| **Monitor loop** | `main.py` → `run_loop()` | `core/grid_manager.py` → `check_fills()` |

---

## 📚 Reading Order (understand the full system)

1. **`config.yml`** — 20 lines. All config in one place.
2. **`docs/architecture.md`** — module map + data flow
3. **`main.py`** — startup → deploy → monitor loop
4. **`core/grid_manager.py`** — the heart (1600 lines, but mostly logic, not complex)
5. **`analysis/analyst.py`** — how AI decides levels
6. **`api/lighter.py`** — exchange communication

Everything else is supporting.

---

## 🔑 Key Classes (what you'll actually import)

```python
from core.grid_manager import GridManager   # Main orchestrator
from core.calculator import calculate_grid  # Sizing math
from api.lighter import LighterAPI          # Lighter DEX client
from analysis.analyst import run_analyst    # AI analyst entry point
from indicators import (                    # Technical analysis
    calc_atr, calc_adx, calc_ema_single,
    detect_regime, detect_volume_spike,
    funding_rate_adjustment, time_awareness_adjustment
)
```

---

## 🧩 GridManager Mixin Structure

`GridManager` inherits from two mixins — this is why the file is 1600 lines:

```
GridManager
├── CapitalMixin (core/capital.py)
│   ├── equity checks
│   ├── position-aware sizing
│   └── safety validation
│
├── OrderMixin (core/order_manager.py)
│   ├── place_buy_orders, place_sell_orders
│   ├── cancel_all, cancel_one
│   └── fill detection
│
└── GridManager (core/grid_manager.py)
    ├── deploy, check_fills, roll logic
    ├── pause/resume, session logging
    └── position recovery
```

When looking for order-related code, check `core/order_manager.py` first.

---

## 🪝 Hook Points (where external data enters)

| Hook | File | What it does |
|------|------|-------------|
| `gather_all_intel()` | `market/intel.py` | Pulls funding rate, OI, liquidations from Coinalyze |
| `gather_indicators()` | `indicators/composite.py` | Runs all 12 indicators, returns summary dict |
| `direction_score()` | `indicators/direction_score.py` | Composite long/short/pause signal |
| `MemoryLayer.get_recs()` | `core/memory_layer.py` | Historical pattern recommendations |

Analyst pulls all 4 hooks → builds LLM prompt.

---

## 📁 State Files (runtime, gitignored)

| File | Written by | Read by |
|------|-----------|---------|
| `state/grid_state.json` | `GridManager._save_state()` | Grid manager, telegram bot, memory layer |
| `state/bot_command.json` | `telegram_bot.py` (when user sends command) | `main.py` → `check_telegram_commands()` |
| `state/loss_lockout.json` | `main.py` → `set_loss_lockout()` | `main.py` → `check_loss_lockout()` |
| `state/bot-memory.json` | `MemoryLayer` | `PatternAnalyzer`, analyst feedback loop |
| `state/deployments/*.json` | `GridManager._log_deployment()` | Manual review, intelligence dashboard |

---

## 🐛 Common Debug Patterns

**Check if grid is deployed:**
```bash
cat state/grid_state.json | jq '.active, .paused, .levels'
```

**Last 5 fill events:**
```bash
sudo journalctl -u btc-grid-bot --since "today" | grep -i "fill detected"
```

**Orphan sells:**
```bash
cat state/grid_state.json | jq '.orphan_sells'
```

**Why did a roll/pause happen?**
```bash
sudo journalctl -u btc-grid-bot --since "today" | grep -i -E "skip|pause|cooldown|volume"
```

**Is the bot communicating with Lighter?**
```bash
sudo journalctl -u btc-grid-bot --since "5 min ago" | grep -c "HTTP"
```

---

## 📏 Line Counts (for estimating reading time)

| Module | Lines | Complexity |
|--------|-------|-----------|
| `core/grid_manager.py` | 1600 | Medium (long but straightforward) |
| `main.py` | 240 | Low |
| `analysis/analyst.py` | 850 | Medium (LLM prompt building) |
| `core/capital.py` | 460 | Low-Medium |
| `market/intel.py` | 420 | Low |
| `core/memory_layer.py` | 350 | Low |
| `api/lighter.py` | 320 | Low |
| `core/intelligence.py` | 270 | Low |
| `core/order_manager.py` | 205 | Low |
| everything else | <200 | Low |

**Total: ~5000 lines.**

---

## 🚨 Things That Have Bitten Us Before

1. **Restarting wrong service** — `bot` is V1 autopilot. `btc-grid-bot` is grid bot. ALWAYS verify with `systemctl list-units` first.
2. **Orphan sells** — sell fills without BTC to cover. Handled correctly now (logs + skips replacement). Not a bug.
3. **Volume spike cooldown** — sets 1h cooldown, disables rolls + trend checks during spike. Normal behavior.
4. **Loss lockout** — 24h ban after hitting loss limit. Check `state/loss_lockout.json`.
5. **Legacy shims in root** — 7 files that just `from package.module import ...`. Harmless, don't touch.
