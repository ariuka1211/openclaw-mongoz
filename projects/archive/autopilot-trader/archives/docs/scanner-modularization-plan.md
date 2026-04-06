# Scanner Modularization Plan

## Current State
- **1 file**: `opportunity-scanner.ts` (1120 lines)
- 1 bash daemon wrapper
- Single-file monolith: types, config, API calls, 5 scoring signals, position sizing, direction logic, output formatting, signal writing — all mixed together

## Target: 7 focused modules + slim orchestrator

### Module Map

| Module | Lines (est) | Purpose |
|--------|------------|---------|
| `types.ts` | ~90 | All interfaces: `OrderBookDetail`, `FundingRateRaw`, `MarketOpportunity`, `OiSnapshot`, `KlineData`, `ObLevel`, scoring result types |
| `config.ts` | ~30 | `CONFIG` object, `BASE_URL`, `LIGHTER_ACCOUNT_INDEX`, `EIGHT_HR_MULTIPLIER`, OKX market set, symbol mapping |
| `api/lighter.ts` | ~50 | `fetchBalance()`, `fetchOrderBookDetails()`, `fetchFundingRates()` |
| `api/okx.ts` | ~100 | `fetchOkxKlines()`, klines cache, rate-limited batch fetcher |
| `signals/funding.ts` | ~25 | `scoreFunding()` — Lighter vs CEX rate arbitrage |
| `signals/momentum.ts` | ~30 | `scoreMomentum()` — direction-aware daily price change |
| `signals/ma.ts` | ~55 | `computeMA()`, `scoreMA()` — 50/99/200 MA alignment |
| `signals/order-block.ts` | ~85 | `detectOrderBlocks()`, `scoreOrderBlock()` — OB detection + scoring |
| `signals/oi-trend.ts` | ~40 | `loadOiSnapshot()`, `saveOiSnapshot()`, `scoreOiTrend()` |
| `sizing.ts` | ~100 | `calculatePosition()` — risk-based sizing + safety checks |
| `direction.ts` | ~55 | `computeDirection()` — majority vote logic |
| `output.ts` | ~200 | Console output formatting, `signals.json` atomic write, signal cleanup |
| `opportunity-scanner.ts` | ~100 | CLI parsing, orchestration, wiring — the "main" file |

**Total**: ~860 lines across 13 files (vs 1120 in 1 file). Net ~260 extra lines from imports/exports/headers, but each file is focused and testable.

## Target Layout

```
scanner/
├── src/
│   ├── main.ts                            # entry point, wiring
│   ├── types.ts                           # interfaces
│   ├── config.ts                          # constants + settings
│   ├── api/
│   │   ├── lighter.ts                     # Lighter REST calls
│   │   └── okx.ts                         # OKX klines + cache
│   ├── signals/
│   │   ├── funding-spread.ts              # Lighter vs CEX rate spread score
│   │   ├── price-momentum.ts              # daily price change score
│   │   ├── moving-average-alignment.ts    # 50/99/200 MA structure score
│   │   ├── order-block.ts                 # order block detection + score
│   │   └── oi-trend.ts                    # open interest change score
│   ├── position-sizing.ts                 # risk-based sizing + safety checks
│   ├── direction-vote.ts                  # long/short majority vote
│   └── output.ts                          # console display + signal.json write
├── scanner-daemon.sh                      # unchanged
└── .gitignore                             # unchanged
```

### Key Decisions

1. **Single responsibility** — each module does one thing, exports one clear function
2. **No shared state between modules** — pure functions where possible, klines cache stays in `okx.ts`
3. **Types extracted** — prevents circular deps, any module can import types
4. **Signals folder** — groups the 5 scoring signals together (natural category)
5. **API folder** — separates external data fetching from scoring logic
6. **Bun-native** — all imports use `Bun.file()` / `Bun.write()` / `Bun.sleep()` as before
7. **Backward compatible** — `opportunity-scanner.ts` remains the entry point, `scanner-daemon.sh` unchanged

### Execution Order

1. Create `types.ts` — extract all interfaces (no deps, foundation)
2. Create `config.ts` — extract CONFIG + constants + OKX market set
3. Create `api/lighter.ts` — extract Lighter API fetchers
4. Create `api/okx.ts` — extract OKX klines + cache + symbol mapping
5. Create `signals/funding.ts` — extract `scoreFunding()`
6. Create `signals/momentum.ts` — extract `scoreMomentum()`
7. Create `signals/ma.ts` — extract `computeMA()`, `scoreMA()`
8. Create `signals/order-block.ts` — extract `detectOrderBlocks()`, `scoreOrderBlock()`
9. Create `signals/oi-trend.ts` — extract OI snapshot + scoring
10. Create `sizing.ts` — extract `calculatePosition()`
11. Create `direction.ts` — extract `computeDirection()`
12. Create `output.ts` — extract console formatting + signal writing
13. Rewrite `opportunity-scanner.ts` — slim orchestrator wiring all modules

### Verification
- Run scanner end-to-end after modularization: `bun run scanner/opportunity-scanner.ts --max-positions 3`
- Confirm `signals.json` output matches previous format
- Confirm console output matches previous format
- No tests exist currently — could add later if needed

### Risks
- **Bun imports**: TS files use bare imports (`import { ... } from "./types"`), need `.ts` extensions or `bunfig.toml` config. Bun resolves `.ts` by default so should work.
- **Circular deps**: unlikely with this layout since types are separate and signals are leaf modules.
- **Side effects**: klines cache + OI snapshot file I/O are the only side effects. Contained to their modules.
