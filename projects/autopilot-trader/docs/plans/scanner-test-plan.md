# Scanner Test Plan

## Overview
- **Language**: TypeScript (Bun test runner — built-in, no config needed)
- **Structure**: `scanner/tests/` directory
- **Goal**: 80%+ coverage on scoring/sizing logic, integration test for full pipeline

## Test Setup
- `scanner/tests/unit/` — pure function tests
- `scanner/tests/integration/` — full pipeline with mocked APIs
- `scanner/tests/fixtures/` — test data (sample market, OHLC, funding rates)
- Run: `bun test` from `scanner/` directory

## Unit Tests

### 1. `funding-spread.test.ts` — `scoreFunding()`
- Zero spread → score 0
- 0.15%/8h spread → score 100
- Mixed CEX rates → averages correctly
- Only Lighter rate (no CEX) → cexAvg8h = 0
- Negative spread (Lighter underpay) → score from abs value

### 2. `price-momentum.test.ts` — `scoreMomentum()`
- 0% change → score 10
- 15%+ change → score 100
- Aligned with MA → 1.3× boost (capped at 100)
- Opposing MA → 0.5× penalty
- Neutral MA ("↔") → no adjustment

### 3. `moving-average-alignment.test.ts` — `computeMA()`, `scoreMA()`
- Insufficient data → null
- Bull alignment (price > MA50 > MA99 > MA200) → score ≥80, direction "↑"
- Bear alignment → score ≥80, direction "↓"
- Choppy → score 30, direction "↔"
- Price = 0 or negative → score 50, direction "↔"

### 4. `order-block.test.ts` — `detectOrderBlocks()`, `scoreOrderBlock()`
- No impulse moves → both null
- Bullish OB detected (down candle + 3 up candles)
- Bearish OB detected (up candle + 3 down candles)
- Price near bullish OB (≤1%) → high score (70-100), type "support"
- Price near bearish OB (≤1%) → low score (20-30), type "resistance"
- Price far from OB → score 50

### 5. `direction-vote.test.ts` — `computeDirection()`
- All 3 bullish → "long"
- All 3 bearish → "short"
- 2 long + 1 short → "long"
- 2 short + 1 long → "short"
- MA neutral, OB none, spread 0 → "long" (fallback)
- MA tiebreaker works when 1-1-1 split

### 6. `position-sizing.test.ts` — `calculatePosition()`
- NaN fields → fail with "Invalid numeric data"
- Zero volatility → fail with "No stop-loss range"
- Normal case → correct position size = riskAmount / SL%
- Max leverage cap applied (20×)
- Liquidation too close → fail with liq dist reason
- Actual leverage calculation correct

### 7. `oi-trend.test.ts` — `scoreOiTrend()`
- No previous data → score 50, changePct 0
- OI up >10% → score 80-100
- OI up 3-10% → score 60-70
- OI flat ±3% → score 50
- OI down >10% → score 10-20

### 8. `output.test.ts` — `fmtPct()`, `fmtUsd()`, `pad()`, `padL()`
- `fmtPct(5.5)` → "+5.500%"
- `fmtPct(-2.1)` → "-2.100%"
- `fmtUsd(1500000)` → "$1.5M"
- `fmtUsd(1500)` → "$2K"
- `pad("hi", 5)` → "hi   "
- `padL("hi", 5)` → "   hi"

## Integration Test

### `pipeline.test.ts` — full scan with mocked APIs
- Mock `fetch()` for Lighter APIs (orderBookDetails, funding-rates, account)
- Mock `fetch()` for OKX klines
- Run main() with `--max-positions 1`
- Verify `signals.json` written with correct structure:
  - Has `timestamp`, `config`, `opportunities`
  - Each opportunity has required fields
  - Composite scores are in range 0-100
  - Position sizing fields present
- Verify signal cleanup removes stale opportunities (>20min)

## Fixtures

### `fixtures/test-data.ts`
- `mockMarket`: sample OrderBookDetail with realistic values
- `mockFundingRates`: Lighter + 3 CEX rates
- `mockKlines`: 200 candles with bull/bear/choppy patterns
- `mockMarketList`: 3-5 liquid markets

## Constraints
- **No live API calls** — all fetch() mocked
- **No file system writes** — mock Bun.write() / Bun.file()
- **Fast** — all tests should run in <5s
- **Bun test runner** — use `describe()`, `it()`, `expect()`
