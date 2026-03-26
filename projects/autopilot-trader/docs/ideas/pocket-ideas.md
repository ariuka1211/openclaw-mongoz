# Pocket Ideas — Autopilot Trader

Quick ideas worth considering, parked for later.

---

## 1. Signal History Table (2026-03-24)

**Problem:** Scanner overwrites `signals.json` every 5 min. No history of what it saw between decisions. Signal snapshots only saved when ai-decisions makes a decision (top 10 embedded in `decisions` table).

**Idea:** Store raw scanner output per cycle in a SQLite table.

**Schema sketch:**
```sql
CREATE TABLE signal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cycle_scan TEXT NOT NULL,  -- ISO timestamp of scan
    symbol TEXT NOT NULL,
    direction TEXT,
    composite_score REAL,
    funding_spread_score REAL,
    volume_anomaly_score REAL,
    momentum_score REAL,
    ma_alignment_score REAL,
    order_block_score REAL,
    lighter_funding_8h REAL,
    cex_funding_8h REAL,
    funding_spread_8h REAL,
    daily_volume_usd REAL,
    daily_price_change REAL,
    last_price REAL,
    rank INTEGER  -- position in scan (1=top)
);
CREATE INDEX idx_sig_history_ts ON signal_history(timestamp);
CREATE INDEX idx_sig_history_sym ON signal_history(symbol);
```

**Size estimate:**
- ~3,500 rows/day (288 scans × 12 signals avg)
- ~31 MB/month, ~375 MB/year
- Purge: keep 7 days (~21 MB) or 30 days (~31 MB) — trivial

**Benefits:**
- Backtesting: "what if score threshold was 65 instead of 60?"
- Missed signals: was that DUSK score=71 actually a good trade?
- Scanner drift detection: fewer qualifying signals over time?
- Discrepancy debugging: rewind to exact scan that produced a bad signal

**Where to hook in:** Either scanner appends a JSON log, or ai-decisions snapshots signals every cycle (not just on decisions). The ai-decisions approach is cleaner since it already reads signals.json.

**Effort:** Small. Add one table + one insert per cycle. ~20 lines of code.

---

## 2. Rolling Average Volatility for Stop Loss (2026-03-24)

**Problem:** SL distance = single-day (high - low) / price. Spikes give absurdly wide stops (CRCL had 26.7% SL), quiet days give stops that trigger on noise.

**Idea:** Use 7-day average daily range instead of today's single candle. Already have 210 hours of OKX klines — compute rolling average of (high-low)/close over last 7 daily candles.

**Where:** `calculatePosition()` in `opportunity-scanner.ts`. Replace:
```ts
const dailyVolatility = (market.daily_price_high - market.daily_price_low) / market.last_trade_price;
```
With:
```ts
const avgVolatility7d = computeAvgRange(klines, 7); // from OKX data
```

**Impact:** Smoother, more realistic stops. Won't get 26% stops on spike days or 2% stops on quiet days.

---

<!-- Add new ideas below this line -->
