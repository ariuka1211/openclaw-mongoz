/**
 * Correlation Guard — prevents opening correlated positions
 *
 * Calculates rolling Pearson correlation between assets using daily returns
 * from OKX klines. Used to block or downscore opportunities that would
 * over-concentrate risk in the same directional bet.
 *
 * Integration points:
 *   1. Scanner: add correlation check before ranking opportunities
 *   2. Safety layer: validate decisions against open positions
 *
 * Usage:
 *   import { fetchCorrelation, filterCorrelated } from "./correlation-guard";
 *
 *   const corr = await fetchCorrelation("BTC", "ETH", 30);
 *   // corr = 0.92 (highly correlated)
 *
 *   const filtered = await filterCorrelated(
 *     [{ symbol: "BTC", direction: "long" }],
 *     { symbol: "ETH", direction: "long", compositeScore: 85 },
 *     0.7
 *   );
 *   // filtered = null (blocked — same direction, high correlation)
 */

// ── Types ────────────────────────────────────────────────────────────

export interface OpenPosition {
  symbol: string;
  direction: "long" | "short";
}

export interface CandidateTrade {
  symbol: string;
  direction: "long" | "short";
  compositeScore: number;
}

export interface CorrelationResult {
  symbolA: string;
  symbolB: string;
  correlation: number;   // -1 to +1
  period: number;        // days used
  dataPoints: number;    // actual overlapping points
  blocked: boolean;      // true if this pair should block the trade
}

// ── Config ───────────────────────────────────────────────────────────

const OKX_API = "https://www.okx.com/api/v5/market/candles";
const DEFAULT_LOOKBACK = 30;  // 30 daily candles
const RATE_LIMIT_DELAY = 120; // ms between OKX requests

// Cache: instId → daily closes (avoid re-fetching)
const dailyCache = new Map<string, number[]>();

// ── OKX symbol mapping (reuse from scanner) ──────────────────────────

const OKX_MARKETS = new Set([
  "MET","LTC","FIL","WIF","EIGEN","ROBO","PROVE","PENGU","CRO","TON",
  "ONDO","XRP","ZORA","LINEA","ZEC","JTO","LIT","HBAR","SUI","STRK",
  "IP","TRX","SOL","DYDX","POL","BNB","XPL","LINK","BONK","BERA",
  "AXS","TIA","JUP","ENA","PUMP","BCH","SKY","ADA","ETHFI","CC",
  "APT","DOGE","MORPHO","ZK","WLD","AVNT","OP","BTC","LDO","AVAX",
  "TRUMP","GMX","ASTER","RESOLV","DASH","DOT","ICP","NEAR","CRV","FLOKI",
  "2Z","SHIB","AAVE","SEI","PENDLE","PAXG","PYTH","NMR","WLFI","UNI",
  "ZRO","S","TOSHI","ETH","VIRTUAL","ARB","HYPE","KAITO",
]);

function getOkxInstId(symbol: string): string | null {
  let base = symbol;
  if (symbol.startsWith("1000")) base = symbol.slice(4);
  if (base === "XBT") base = "BTC";
  if (OKX_MARKETS.has(base)) return `${base}-USDT-SWAP`;
  return null;
}

// ── Fetch daily closes from OKX ──────────────────────────────────────

async function fetchDailyCloses(instId: string, limit: number): Promise<number[]> {
  if (dailyCache.has(instId)) return dailyCache.get(instId)!;

  const url = `${OKX_API}?instId=${instId}&bar=1D&limit=${limit + 5}`; // +5 buffer for gaps

  try {
    const res = await fetch(url);
    if (!res.ok) {
      console.error(`[correlation] OKX ${instId}: ${res.status}`);
      return [];
    }
    const json = await res.json();
    if (!json.data || json.data.length === 0) return [];

    // OKX returns [ts, o, h, l, c, vol, ...] newest first
    const closes = json.data.map((candle: string[]) => parseFloat(candle[4])).reverse();
    dailyCache.set(instId, closes);
    return closes;
  } catch (err) {
    console.error(`[correlation] OKX ${instId}: ${err}`);
    return [];
  }
}

// ── Pearson correlation from price series ────────────────────────────

function computeReturns(prices: number[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < prices.length; i++) {
    if (prices[i - 1] > 0) {
      returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
    }
  }
  return returns;
}

function pearsonCorrelation(x: number[], y: number[]): number {
  const n = Math.min(x.length, y.length);
  if (n < 5) return 0; // not enough data

  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
  for (let i = 0; i < n; i++) {
    sumX += x[i];
    sumY += y[i];
    sumXY += x[i] * y[i];
    sumX2 += x[i] * x[i];
    sumY2 += y[i] * y[i];
  }

  const numerator = n * sumXY - sumX * sumY;
  const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));

  if (denominator === 0) return 0;
  return numerator / denominator;
}

// ── Public API ───────────────────────────────────────────────────────

/**
 * Fetch correlation between two symbols.
 * Returns null if either symbol has no OKX equivalent.
 */
export async function fetchCorrelation(
  symbolA: string,
  symbolB: string,
  lookbackDays: number = DEFAULT_LOOKBACK
): Promise<CorrelationResult | null> {
  if (symbolA === symbolB) {
    return { symbolA, symbolB, correlation: 1.0, period: lookbackDays, dataPoints: lookbackDays, blocked: true };
  }

  const instIdA = getOkxInstId(symbolA);
  const instIdB = getOkxInstId(symbolB);
  if (!instIdA || !instIdB) return null;

  const [closesA, closesB] = await Promise.all([
    fetchDailyCloses(instIdA, lookbackDays + 1),
    fetchDailyCloses(instIdB, lookbackDays + 1),
  ]);

  if (closesA.length < 6 || closesB.length < 6) return null;

  const returnsA = computeReturns(closesA);
  const returnsB = computeReturns(closesB);

  // Align to same length (use the shorter one, from the end)
  const minLen = Math.min(returnsA.length, returnsB.length, lookbackDays);
  if (minLen < 10) {
    console.log(`[correlation] ${symbolA}/${symbolB}: only ${minLen} overlapping points, skipping`);
    return null;
  }
  const sliceA = returnsA.slice(-minLen);
  const sliceB = returnsB.slice(-minLen);

  const correlation = pearsonCorrelation(sliceA, sliceB);

  return {
    symbolA,
    symbolB,
    correlation,
    period: lookbackDays,
    dataPoints: minLen,
    blocked: false, // caller decides
  };
}

/**
 * Check if a candidate trade should be blocked due to correlation
 * with existing positions.
 *
 * Rules:
 *   - Same direction + correlation > threshold → BLOCK
 *   - Opposite direction + correlation > threshold → ALLOW (hedge)
 *   - Either symbol has no OKX mapping → ALLOW (can't check)
 */
export async function filterCorrelated(
  existingPositions: OpenPosition[],
  candidate: CandidateTrade,
  threshold: number = 0.75
): Promise<{ allowed: boolean; blockingPosition?: string; correlation?: number }> {
  if (existingPositions.length === 0) {
    return { allowed: true };
  }

  for (const pos of existingPositions) {
    // Skip if opposite direction — that's a hedge, not concentration
    if (pos.direction !== candidate.direction) continue;

    const result = await fetchCorrelation(pos.symbol, candidate.symbol);
    if (!result) continue; // can't check — allow by default

    result.blocked = Math.abs(result.correlation) >= threshold;

    if (result.blocked) {
      return {
        allowed: false,
        blockingPosition: pos.symbol,
        correlation: result.correlation,
      };
    }
  }

  return { allowed: true };
}

/**
 * Batch-check: filter a list of candidate opportunities against open positions.
 * Returns only the opportunities that pass the correlation check.
 */
export async function filterOpportunities(
  openPositions: OpenPosition[],
  candidates: CandidateTrade[],
  threshold: number = 0.75
): Promise<{ passed: CandidateTrade[]; blocked: { candidate: CandidateTrade; reason: string }[] }> {
  const passed: CandidateTrade[] = [];
  const blocked: { candidate: CandidateTrade; reason: string }[] = [];

  for (const c of candidates) {
    const check = await filterCorrelated(openPositions, c, threshold);
    if (check.allowed) {
      passed.push(c);
    } else {
      blocked.push({
        candidate: c,
        reason: `${c.symbol} ${c.direction} correlates ${check.correlation!.toFixed(2)} with ${check.blockingPosition} ${openPositions.find(p => p.symbol === check.blockingPosition)!.direction}`,
      });
    }
    // Rate limit between OKX requests
    await new Promise(r => setTimeout(r, RATE_LIMIT_DELAY));
  }

  return { passed, blocked };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.log("Usage: npx tsx correlation-guard.ts <SYMBOL_A> <SYMBOL_B> [LOOKBACK_DAYS]");
    process.exit(1);
  }

  const [symA, symB, lb] = args;
  const lookback = parseInt(lb) || 30;

  fetchCorrelation(symA, symB, lookback).then((result) => {
    if (result) {
      console.log(`\n${symA} ↔ ${symB} correlation (${lookback}d):`);
      console.log(`  Pearson r: ${result.correlation.toFixed(4)}`);
      console.log(`  Data points: ${result.dataPoints}`);
      console.log(`  Interpretation: ${
        Math.abs(result.correlation) >= 0.8 ? "🔴 HIGH — likely blocked for same-direction trades" :
        Math.abs(result.correlation) >= 0.5 ? "🟡 MODERATE" :
        "🟢 LOW — likely allowed"
      }`);
    } else {
      console.log(`Could not compute correlation (no OKX data for one or both symbols)`);
    }
  }).catch(console.error);
}

// Run if executed directly
if (require.main === module) {
  main();
}
