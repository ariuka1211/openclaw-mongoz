# Unified Dashboard — Implementation Plan

**Status:** Ready to build
**Created:** 2026-03-24
**Replaces:** `signals/dashboard/app.py` (port 8080), `signals/ai-trader/dashboard.py` (port 8080)

---

## 1. File Structure

```
signals/dashboard/
├── app.py                    # ← REPLACE existing (new unified FastAPI app)
├── index.html                # ← REPLACE existing (new tabbed frontend)
├── start.sh                  # ← MODIFY (update port/path if needed)
├── js/                       # ← NEW directory (frontend modules)
│   ├── store.js              # Reactive data store (subscribe/publish)
│   ├── providers.js          # Data providers (poll today, WS later)
│   ├── portfolio.js          # Portfolio tab renderer
│   ├── trader.js             # AI Trader + Performance tab renderers
│   ├── scanner.js            # Scanner tab renderer
│   └── system.js             # System tab renderer
└── api/                      # ← NEW directory (modular endpoint handlers)
    ├── __init__.py
    ├── portfolio.py          # /api/portfolio — bot_state.json reader
    ├── trader.py             # /api/trader/* — SQLite queries via db.py
    ├── scanner.py            # /api/scanner/* — signals.json + IPC files
    └── system.py             # /api/system/* — health/status checks
```

**Key decisions:**
- Replaces `signals/dashboard/app.py` in-place (single ownership)
- New `api/` subdirectory keeps endpoints modular but still one FastAPI app
- New `js/` directory with Store-based data layer — renderers subscribe to Store, providers populate it
  - Today: poll-based provider populates Store via `setInterval` + `fetch`
  - Future: swap to WebSocket provider — zero changes to render functions
- `signals/ai-trader/dashboard.py` is left in place but **not started** — its port will conflict, so stop the ai-trader dashboard service

**Files NOT changed (read-only):**
- `signals/ai-trader/db.py` — reused as-is (import DecisionDB)
- `executor/state/bot_state.json` — read-only
- `signals/signals.json` — read-only
- `signals/ai-decision.json`, `ai-result.json` — read-only

---

## 2. API Endpoint Specifications

### 2.1 Portfolio (`api/portfolio.py`)

| Endpoint | Method | Source | Description |
|---|---|---|---|
| `/api/portfolio` | GET | `bot_state.json` | All positions with computed unrealized PnL, exposure, DSL state |
| `/api/portfolio/summary` | GET | `bot_state.json` + `ai-result.json` | Equity, balance, total exposure, volume quota |

**Data source:** Read `executor/state/bot_state.json` directly. Current price comes from `lastPrice` in each position's data OR from the most recent signals.json entry for that symbol. Compute unrealized PnL = `(current - entry) / entry * size * leverage` for longs, inverted for shorts.

**Response shape:**
```json
{
  "equity": 59.64,
  "balance": 13.03,
  "positions": [
    {
      "market_id": 141,
      "symbol": "HYUNDAI",
      "side": "long",
      "entry_price": 496552.0,
      "current_price": 498499.0,
      "size": 0.000003,
      "leverage": 10.0,
      "unrealized_pnl": 0.056,
      "roe_pct": 3.92,
      "dsl": {
        "high_water_roe": 3.92,
        "current_tier_trigger": null,
        "breach_count": 0,
        "stagnation_active": false
      }
    }
  ],
  "total_exposure_usd": 45.20,
  "max_concurrent": 3,
  "volume_quota_remaining": 85000
}
```

### 2.2 Trader (`api/trader.py`)

| Endpoint | Method | Source | Description |
|---|---|---|---|
| `/api/trader/status` | GET | `db.py` + `ai-decision.json` | Uptime, last cycle, model, equity |
| `/api/trader/decisions?n=50` | GET | `db.py` | Recent decisions (existing) |
| `/api/trader/performance` | GET | `db.py` | Win rate, PnL, drawdown (existing) |
| `/api/trader/alerts?limit=20` | GET | `db.py` | Recent alerts (existing) |
| `/api/trader/equity-curve` | GET | `db.py` | NEW — cumulative PnL over time from outcomes |
| `/api/trader/confidence-stats` | GET | `db.py` | NEW — confidence bracket calibration |
| `/api/trader/per-symbol` | GET | `db.py` | NEW — PnL breakdown by symbol |
| `/api/trader/by-exit-reason` | GET | `db.py` | NEW — PnL grouped by exit_reason |

**`/api/trader/equity-curve`** — Query outcomes ordered by timestamp, compute cumulative sum of `pnl_usd`. Return `[{timestamp, cumulative_pnl, trade_pnl}]`.

**`/api/trader/confidence-stats`** — Reuse existing `DecisionDB.get_confidence_bracket_stats()`. Returns direction_stats, hold_time_stats, confidence_stats, loss_patterns.

**`/api/trader/per-symbol`** — SQL: `SELECT symbol, COUNT(*), SUM(pnl_usd), AVG(pnl_usd), AVG(hold_time_seconds) FROM outcomes GROUP BY symbol ORDER BY SUM(pnl_usd) DESC`.

**`/api/trader/by-exit-reason`** — SQL: `SELECT exit_reason, COUNT(*), SUM(pnl_usd), AVG(pnl_usd) FROM outcomes GROUP BY exit_reason`.

### 2.3 Scanner (`api/scanner.py`)

| Endpoint | Method | Source | Description |
|---|---|---|---|
| `/api/scanner/opportunities` | GET | `signals.json` | Top N opportunities with full score breakdown |
| `/api/scanner/funding` | GET | `signals.json` | Funding spread tracker (CEX vs Lighter) |
| `/api/scanner/distribution` | GET | `signals.json` | Score distribution histogram data |
| `/api/scanner/stats` | GET | `signals.json` | Total count, timestamp, age, config |

**`/api/scanner/opportunities?n=20`** — Read `signals/signals.json`, return top N opportunities sorted by compositeScore. Include all 5 sub-scores + funding + volume + safetyPass.

**`/api/scanner/funding`** — Extract `fundingSpread8h` from each opportunity. Return sorted by absolute spread (highest arbitrage first). Include `lighterFundingRate8h` and `cexAvgFundingRate8h`.

**`/api/scanner/distribution`** — Bucket composite scores into 0-10, 10-20, ..., 90-100. Return counts per bucket. Also return per-component averages.

### 2.4 System (`api/system.py`)

| Endpoint | Method | Source | Description |
|---|---|---|---|
| `/api/system/health` | GET | process checks + timestamps | All service statuses |
| `/api/system/errors` | GET | `db.py` alerts table | Recent errors across services |

**Process checks:** `pgrep -f bot.py`, `pgrep -f ai_trader.py`, `pgrep -f opportunity-scanner`. Check `signals.json` timestamp staleness (>10 min = stale). Check `bot_state.json` file modification time.

```json
{
  "services": {
    "bot": {"running": true, "pid": 12345, "last_state_update": "2026-03-24T17:05:00Z"},
    "ai_trader": {"running": true, "pid": 12346, "last_cycle": "2026-03-24T17:04:30Z", "model": "gemini-2.5-pro"},
    "scanner": {"running": true, "pid": 12347, "last_scan": "2026-03-24T17:03:47Z", "scan_interval_sec": 300}
  },
  "dashboard": {"started_at": "...", "uptime_seconds": 3600},
  "port": 8080
}
```

---

## 3. Frontend Page Layout

### Navigation: Tab bar at top

```
[Portfolio] [AI Trader] [Performance] [Scanner] [System]
```

Single `index.html` with JS-based tab switching. Each tab is a `<div>` shown/hidden on click.

### Tab 1: Portfolio

**Top row — KPI cards (4 columns, responsive):**
- **Equity** — current account equity (big number, green/red delta from yesterday)
- **Active Positions** — count + max (e.g., "2/3")
- **Total Exposure** — USD value
- **Volume Quota** — remaining quota bar

**Main content — Positions table:**
| Symbol | Side | Entry | Current | PnL | ROE% | Lev | DSL Tier | Breaches |
|---|---|---|---|---|---|---|---|---|
| HYUNDAI | LONG | $496,552 | $498,499 | +$0.06 | +3.92% | 10x | — | 0 |
| JTO | SHORT | $0.318 | $0.320 | -$0.09 | -1.67% | 3x | T1 | 1 |

Side color-coded (green/red). ROE colored by sign. DSL tier badge.

**Data source:** `/api/portfolio` — auto-refresh every **5s**.

### Tab 2: AI Trader

**Top row — KPI cards:**
- **Last Cycle** — time since last decision (e.g., "2m ago")
- **Decisions Today** — count
- **Safety Rejections** — count (last 30min)
- **LLM Latency** — avg ms

**Decision pipeline visualization:**
```
[Hold: 12] → [Open: 5] → [Executed: 3] → [Rejected: 2]
```
Simple horizontal bar or flow indicator.

**Recent decisions table:**
| Time | Action | Symbol | Direction | Confidence | Safety | Executed | Latency |
|---|---|---|---|---|---|---|---|
| 17:04 | open | APT | long | 0.82 | ✅ | ✅ | 1240ms |
| 17:02 | hold | — | — | — | — | — | 890ms |

**Data source:** `/api/trader/decisions?n=30` — auto-refresh every **10s**.

### Tab 3: Performance

**#1 feature: Equity curve chart (full width, ~300px height)**

Line chart with Chart.js:
- X-axis: timestamp
- Y-axis: cumulative PnL (or equity if starting balance known)
- Gradient fill under curve (green above 0, red below)

**KPI row (4 columns):**
- **Win Rate** — % with trade count
- **Total PnL** — USD
- **Avg Win / Avg Loss** — ratio
- **Max Drawdown** — USD

**Sub-sections (grid below chart):**

**PnL by Direction** — two-column breakdown:
```
Longs:  5 trades | +$2.34 | WR: 60%
Shorts: 3 trades | -$0.89 | WR: 33%
```

**PnL by Exit Reason** — table:
| Exit Reason | Trades | Total PnL | Avg PnL |
|---|---|---|---|
| DSL trailing | 4 | +$1.20 | +$0.30 |
| AI close | 3 | -$0.45 | -$0.15 |
| stop_loss | 1 | -$1.68 | -$1.68 |

**Per-Symbol Performance** — table sorted by total PnL:
| Symbol | Trades | Total PnL | Avg PnL | Avg Hold |
|---|---|---|---|---|
| APT | 3 | +$2.10 | +$0.70 | 45min |
| JTO | 2 | -$0.89 | -$0.45 | 22min |

**Confidence Calibration** — bar chart:
- X-axis: confidence brackets (low <0.4, medium 0.4-0.7, high ≥0.7)
- Y-axis: win rate %
- Shows whether high confidence correlates with better outcomes

**Data sources:**
- Equity curve: `/api/trader/equity-curve` — refresh every **30s**
- Stats: `/api/trader/performance` — refresh every **30s**
- Breakdowns: `/api/trader/per-symbol`, `/api/trader/by-exit-reason`, `/api/trader/confidence-stats` — refresh every **60s**

### Tab 4: Scanner

**Top row — KPI:**
- **Total Opportunities** — count (e.g., 107)
- **Signal Age** — "3m ago" with staleness indicator
- **Avg Composite Score** — mean of all opportunities

**Top Opportunities table (top 15):**
| # | Symbol | Direction | Score | Funding | Volume | Momentum | MA | OB | Size |
|---|---|---|---|---|---|---|---|---|---|
| 1 | APT | LONG | 73 | 100 | 6 | 40 | 91 | 87 | $30.00 |

Score cell has a small inline bar showing component breakdown (stacked colored segments for each sub-score).

**Score Distribution chart** — histogram:
- X-axis: score buckets (0-10, 10-20, ..., 90-100)
- Y-axis: count of opportunities
- Blue bars

**Funding Spread Tracker** — table of top arbitrage opportunities:
| Symbol | Lighter Rate | CEX Avg Rate | Spread | Direction |
|---|---|---|---|---|
| APT | +7.68% | -7.51% | 15.19% | long |

**Data source:** `/api/scanner/opportunities?n=15`, `/api/scanner/distribution`, `/api/scanner/stats` — auto-refresh every **60s**.

### Tab 5: System

**Service status grid:**
| Service | Status | PID | Last Activity | Uptime |
|---|---|---|---|---|
| Bot | 🟢 Running | 12345 | 5s ago | 3h 22m |
| AI Trader | 🟢 Running | 12346 | 2m ago | 3h 22m |
| Scanner | 🟢 Running | 12347 | 3m ago | 3h 22m |
| Dashboard | 🟢 Running | — | — | 1h 05m |

**Recent errors** — from alerts table (last 20):
| Time | Level | Message |
|---|---|---|
| 17:01 | WARN | Position JTO approaching stagnation |

**Data source:** `/api/system/health`, `/api/trader/alerts?limit=20` — auto-refresh every **30s**.

---

## 4. Data Flow

```
┌─────────────────────────────────────────────────────┐
│                   Browser (index.html)               │
│  Tab switcher + Chart.js + fetch() auto-refresh      │
└───────────┬──────────────┬──────────────┬────────────┘
            │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
    │  /api/portfolio │/api/trader│/api/scanner│ /api/system
    └───────┬──────┘ └────┬─────┘ └─────┬──────┘
            │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
    │  bot_state.json│  trader.db │ signals.json│  pgrep + mtime
    │  (read-only)   │  (via db.py)│ (read-only) │  checks
    └──────────────┘ └──────────┘ └────────────┘
```

**Read patterns:**
1. `/api/portfolio` → open + JSON.parse `executor/state/bot_state.json` (every tick from bot ~5s)
2. `/api/trader/*` → `DecisionDB("state/trader.db").get_*()` methods (SQLite WAL reads, no lock contention)
3. `/api/scanner/*` → open + JSON.parse `signals/signals.json` (updated every 5min)
4. `/api/system/*` → subprocess `pgrep` + `os.path.getmtime()` for file freshness

**No new data is created by the dashboard.** It is purely a read layer.

---

## 5. Migration Path

### Step 1: Stop old dashboards
```bash
# Find and kill existing dashboard processes
pkill -f "signals/dashboard/app.py"
pkill -f "signals/ai-trader/dashboard.py"
# Check they're gone
lsof -i :8080
```

### Step 2: Deploy new unified dashboard
The new `signals/dashboard/app.py` replaces the old one. It reads from all three data sources. Start it:
```bash
cd /root/.openclaw/workspace/projects/autopilot-trader
python3 signals/dashboard/app.py
# Or via start.sh
```

### Step 3: Disable ai-trader dashboard startup
If `ai-trader.service` or `ai_trader.py` starts its own dashboard on port 8080, either:
- Remove the dashboard startup from `ai_trader.py` (it calls `init_dashboard()` + `uvicorn.run()`)
- Or change ai-trader dashboard to a different port (9090) as backup

**Recommended:** In `ai_trader.py`, conditionally skip dashboard startup (env var `DISABLE_DASHBOARD=1` or config flag).

### Step 4: Update start.sh
```bash
#!/bin/bash
cd /root/.openclaw/workspace/projects/autopilot-trader
exec python3 signals/dashboard/app.py
```

### Step 5: Verify
- Open `http://localhost:8080` — should see tabbed interface
- Click each tab — data should load
- Wait 5s — portfolio tab should auto-refresh positions
- Check browser console for no errors

**Rollback:** If issues, restore old `signals/dashboard/app.py` from git and restart. The old dashboard is self-contained and doesn't depend on new endpoints.

---

## 6. Testing Approach

### 6.1 Unit-level (manual, during development)

**API smoke tests:**
```bash
# Each endpoint returns valid JSON
curl -s localhost:8080/api/portfolio | python3 -m json.tool
curl -s localhost:8080/api/trader/performance | python3 -m json.tool
curl -s localhost:8080/api/trader/equity-curve | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} points')"
curl -s localhost:8080/api/scanner/opportunities?n=5 | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} opps')"
curl -s localhost:8080/api/system/health | python3 -m json.tool
```

**Data integrity checks:**
- Portfolio positions count matches `bot_state.json` positions count
- Equity curve points count matches outcomes count
- Confidence stats sums add up to total trades
- Scanner opportunities count matches `signals.json` count

### 6.2 Integration-level

- Load dashboard in browser, verify all 5 tabs render
- Open a position via bot → watch it appear in Portfolio tab within 10s
- Wait for a scanner cycle → verify signals tab updates
- Check that charts render without errors in browser console

### 6.3 Regression

- Screenshot old dashboard before migration
- Screenshot new dashboard after
- Verify all data shown in old dashboard is available in new one
- Verify mobile viewport (375px width) doesn't break layout

---

## 7. Estimated Complexity

| Section | Complexity | Reason |
|---|---|---|
| **API: Portfolio** | 🟢 Simple | Read JSON file, compute PnL, return. ~50 lines. |
| **API: Trader** | 🟡 Medium | Reuse existing db.py methods + add 3 new queries (equity-curve, per-symbol, by-exit-reason). ~120 lines. |
| **API: Scanner** | 🟢 Simple | Read signals.json, slice/filter/aggregate. ~80 lines. |
| **API: System** | 🟢 Simple | pgrep + mtime checks. ~60 lines. |
| **Frontend: Layout + Tabs** | 🟡 Medium | Tab switching, responsive grid, dark theme. Reuse existing CSS. ~200 lines HTML/CSS/JS. |
| **Frontend: Store + Providers** | 🟢 Simple | Reactive data store + poll provider. ~60 lines JS. |
| **Frontend: Portfolio tab** | 🟢 Simple | Table + KPI cards. Subscribes to Store. ~80 lines JS. |
| **Frontend: AI tab** | 🟡 Medium | Decisions table + pipeline viz. ~100 lines JS. |
| **Frontend: Performance tab** | 🔴 Complex | Equity curve chart (Chart.js), calibration chart, multiple breakdown tables. ~250 lines JS. |
| **Frontend: Scanner tab** | 🟡 Medium | Table with inline score bars, histogram chart. ~120 lines JS. |
| **Frontend: System tab** | 🟢 Simple | Status table + error list. ~60 lines JS. |
| **app.py rewrite** | 🟡 Medium | Mount all api submodules, serve index.html, keep alive. ~80 lines. |
| **Migration + testing** | 🟢 Simple | Stop old, start new, verify. ~30 min. |

**Total estimated effort:** ~4-6 hours for a competent developer. The bulk is the Performance tab charts.

**Risk:** The equity curve requires enough outcome data to be meaningful. With only 10 outcomes currently, the chart will be sparse. This is expected — it improves as the bot trades more.

---

## 8. Implementation Order

Recommended sequence to minimize risk:

1. **app.py rewrite** — scaffold FastAPI with tab routes, serve static HTML
2. **api/portfolio.py** + Portfolio tab — highest value (live positions)
3. **api/trader.py** — wrap existing db.py, add equity-curve endpoint
4. **AI tab + Performance tab** — decisions table, then equity curve chart
5. **api/scanner.py** + Scanner tab — read signals.json
6. **api/system.py** + System tab — health checks
7. **Polish** — mobile responsive, auto-refresh timing, error handling
8. **Migration** — stop old dashboards, start new one

---

## 9. Technical Notes

### Chart.js integration
Use CDN: `<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>`. No npm/bundler needed for vanilla JS.

### Shared state file paths
Use absolute paths resolved from the project root:
```python
PROJECT_ROOT = Path("/root/.openclaw/workspace/projects/autopilot-trader")
BOT_STATE_PATH = PROJECT_ROOT / "executor" / "state" / "bot_state.json"
TRADER_DB_PATH = PROJECT_ROOT / "state" / "trader.db"
SIGNALS_PATH = PROJECT_ROOT / "signals" / "signals.json"
AI_DECISION_PATH = PROJECT_ROOT / "signals" / "ai-decision.json"
AI_RESULT_PATH = PROJECT_ROOT / "signals" / "ai-result.json"
```

### Concurrent file reads
- `bot_state.json` is small (~3KB), safe to read on every request
- `signals.json` is larger (~50KB), read on every request is fine at <1 RPS
- SQLite reads use WAL mode — no blocking with writes

### Store-based data layer (future-proof, zero overhead today)

Renderers never call `fetch` directly. They subscribe to Store, which gets populated by providers.

```js
// store.js (~30 lines)
const Store = {
  _cache: {}, _listeners: {},
  get(key) { return this._cache[key]; },
  set(key, value) {
    this._cache[key] = value;
    (this._listeners[key] || []).forEach(fn => fn(value));
  },
  subscribe(key, fn) {
    (this._listeners[key] ||= []).push(fn);
    if (this._cache[key]) fn(this._cache[key]);
  }
};

// providers.js — today: poll, tomorrow: WebSocket (swap one function, nothing else changes)
function startPolling(key, url, intervalMs) {
  async function tick() {
    try {
      const data = await fetch(url).then(r => r.json());
      Store.set(key, data);
    } catch(e) { /* keep last known data */ }
  }
  tick();
  return setInterval(tick, intervalMs);
}

// Tab code — renderers don't know how data arrives
Store.subscribe("portfolio", renderPositions);
startPolling("portfolio", "/api/portfolio", 5000);
```

**Future upgrade path:**
- WebSocket: add `ws.onmessage → Store.set(channel, data)`, remove polling. Zero renderer changes.
- Optimistic updates: `Store.set("portfolio", [...pending, ...current])` before server confirms, reconcile on response.
- Historical caching: Store.set for equity curve only when new trades detected, skip redundant fetches.

### Error handling
Each API endpoint should catch file-not-found and JSON decode errors, returning a `{"error": "..."}` response rather than 500. The frontend should show "—" for missing data.
