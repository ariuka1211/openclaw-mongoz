# Correlation Guard — Integration Spec

## What
Prevents opening correlated positions (e.g. long BTC + long ETH) that concentrate risk.

## How It Works
1. Fetches daily OHLCV from OKX (same API the scanner already uses)
2. Computes 30-day Pearson correlation from daily returns
3. Blocks trades where: same direction + correlation ≥ threshold (default 0.75)
4. Allows opposite-direction trades (hedging is fine)

## File
`signals/scripts/correlation-guard.ts`

## Integration Points

### Option A: Scanner-side (recommended)
Add `filterOpportunities()` call in `opportunity-scanner.ts` after scoring, before outputting `signals.json`.
- Pros: Correlations computed once per scan, candidates pre-filtered
- Cons: Adds ~1-2s to scan time (OKX rate limits)

### Option B: Safety layer (Python)
Import OKX data in `safety.py` and check before approving open decisions.
- Pros: Catches all decisions, not just scanner output
- Cons: Duplicates OKX fetching, Python needs HTTP client

**Recommendation:** Option A (scanner-side). The scanner already has OKX klines cached. Add a `--correlation` flag and filter before ranking.

## Threshold Tuning
- 0.75 default — blocks highly correlated same-direction trades
- 0.60 — more aggressive, blocks moderate correlation too
- 0.90 — permissive, only blocks near-identical assets
- Start at 0.75, adjust after reviewing which pairs get blocked

## Open Positions Format
The scanner/bot needs to track open positions as:
```typescript
interface OpenPosition {
  symbol: string;
  direction: "long" | "short";
}
```

This can be read from the bot's position state or `signals.json`.

## Testing
```bash
bun run signals/scripts/correlation-guard.ts BTC ETH 30
bun run signals/scripts/correlation-guard.ts SOL AVAX 30
bun run signals/scripts/correlation-guard.ts BTC DOGE 30
```

## Next Steps
- [ ] Test CLI against known correlated pairs
- [ ] Add `--correlation` flag to opportunity-scanner.ts
- [ ] Wire open positions from bot state into scanner
- [ ] Log blocked pairs for analysis
- [ ] Update MEMORY.md after integration
