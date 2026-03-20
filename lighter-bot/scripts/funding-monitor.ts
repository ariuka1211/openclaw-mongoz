/**
 * Lighter.xyz Funding Rate Monitor
 *
 * Fetches current funding rates for all Lighter perp markets via their
 * public API and flags extreme rates.
 *
 * Lighter funding is hourly:
 *   - rate is per hour (e.g. 0.0001 = 0.01%/hr)
 *   - ±0.5%/hr clamp, 0.01% base interest
 *
 * We normalize to 8hr for comparison with CEX rates and annualize for display.
 */

// --- Types ---

interface FundingRateRaw {
  market_id: number;
  exchange: string;
  symbol: string;
  rate: number; // hourly rate as decimal (e.g. 0.0001 = 0.01%)
}

interface FundingRateEntry {
  symbol: string;
  exchange: string;
  marketId: number;
  hourlyRate: number;      // raw hourly rate (%)
  eightHrRate: number;     // normalized to 8hr (%)
  annualizedRate: number;  // annualized (%)
  signal: "🟢 BULLISH" | "🔴 BEARISH" | "⚪ NEUTRAL";
  isExtreme: boolean;
}

// --- Config ---

const API_URL = "https://mainnet.zklighter.elliot.ai/api/v1/funding-rates";
const EXTREME_THRESHOLD = 0.1; // ±0.1% per 8hr = extreme
const EIGHT_HR_MULTIPLIER = 8;
const HOURS_PER_YEAR = 24 * 365;

// Show Lighter's own rates (set to "" to show all exchanges combined)
const PRIMARY_EXCHANGE = "lighter";

// --- Helpers ---

function classify(eightHrRatePct: number): {
  signal: FundingRateEntry["signal"];
  isExtreme: boolean;
} {
  const abs8hr = Math.abs(eightHrRatePct);
  const isExtreme = abs8hr >= EXTREME_THRESHOLD;

  // Positive funding = longs pay shorts → bullish crowding
  // Negative funding = shorts pay longs → bearish crowding
  // Extreme negative = short squeeze potential (bullish reversal signal)
  if (eightHrRatePct >= 0.1) return { signal: "🟢 BULLISH", isExtreme };
  if (eightHrRatePct <= -0.1) return { signal: "🔴 BEARISH", isExtreme };
  if (eightHrRatePct > 0.02) return { signal: "🟢 BULLISH", isExtreme: false };
  if (eightHrRatePct < -0.02) return { signal: "🔴 BEARISH", isExtreme: false };
  return { signal: "⚪ NEUTRAL", isExtreme: false };
}

function fmt(pct: number, decimals = 4): string {
  const s = pct.toFixed(decimals);
  return pct >= 0 ? `+${s}%` : `${s}%`;
}

function pad(s: string, len: number): string {
  if (s.length >= len) return s;
  return s + " ".repeat(len - s.length);
}

function padLeft(s: string, len: number): string {
  if (s.length >= len) return s;
  return " ".repeat(len - s.length) + s;
}

// --- Main ---

async function fetchFundingRates(): Promise<{ entries: FundingRateEntry[]; allRates: FundingRateRaw[] }> {
  const res = await fetch(API_URL, {
    headers: { accept: "application/json" },
  });

  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }

  const data: { code: number; funding_rates: FundingRateRaw[] } = await res.json();

  if (data.code !== 200) {
    throw new Error(`API returned code ${data.code}`);
  }

  // Filter to primary exchange (Lighter's own rates)
  const rates = PRIMARY_EXCHANGE
    ? data.funding_rates.filter((r) => r.exchange === PRIMARY_EXCHANGE)
    : data.funding_rates;

  const entries = rates.map((r) => {
    const hourlyRate = r.rate * 100; // decimal → %
    const eightHrRate = hourlyRate * EIGHT_HR_MULTIPLIER;
    const annualizedRate = hourlyRate * HOURS_PER_YEAR;
    const { signal, isExtreme } = classify(eightHrRate);

    return {
      symbol: r.symbol,
      exchange: r.exchange,
      marketId: r.market_id,
      hourlyRate,
      eightHrRate,
      annualizedRate,
      signal,
      isExtreme,
    };
  });

  return { entries, allRates: data.funding_rates };
}

function printSummary(entries: FundingRateEntry[], allRates: FundingRateRaw[]): void {
  // Sort by absolute 8hr rate descending (extremes first)
  const sorted = [...entries].sort(
    (a, b) => Math.abs(b.eightHrRate) - Math.abs(a.eightHrRate)
  );

  const extremes = sorted.filter((e) => e.isExtreme);
  const nonExtremes = sorted.filter((e) => !e.isExtreme);

  // Summary stats
  const bullish = extremes.filter((e) => e.eightHrRate > 0).length;
  const bearish = extremes.filter((e) => e.eightHrRate < 0).length;
  const avg = entries.reduce((s, e) => s + e.eightHrRate, 0) / entries.length;
  const mostBullish = extremes.filter((e) => e.eightHrRate > 0).sort((a, b) => b.eightHrRate - a.eightHrRate)[0];
  const mostBearish = extremes.filter((e) => e.eightHrRate < 0).sort((a, b) => a.eightHrRate - b.eightHrRate)[0];

  console.log("═══════════════════════════════════════════════════════════════════════");
  console.log("  LIGHTER.FUNDING RATE MONITOR");
  console.log(`  ${new Date().toISOString()}`);
  console.log(`  Markets: ${entries.length} | Extremes: ${extremes.length} (🟢 ${bullish} 🔴 ${bearish})`);
  console.log(`  Avg 8H rate: ${fmt(avg, 4)}`);
  if (mostBullish) console.log(`  Most bullish: ${mostBullish.symbol} ${fmt(mostBullish.eightHrRate)}/8h`);
  if (mostBearish) console.log(`  Most bearish: ${mostBearish.symbol} ${fmt(mostBearish.eightHrRate)}/8h`);
  console.log("═══════════════════════════════════════════════════════════════════════");
  console.log("");

  // Table header
  const COL = { sym: 12, rate: 12, ann: 14, sig: 14 };
  const header =
    pad("ASSET", COL.sym) +
    padLeft("8H RATE", COL.rate) +
    padLeft("ANNUALIZED", COL.ann) +
    padLeft("SIGNAL", COL.sig);
  const divider = "─".repeat(header.length);

  if (extremes.length > 0) {
    console.log("  ⚠️  EXTREME FUNDING RATES");
  }
  console.log(`  ${header}`);
  console.log(`  ${divider}`);

  if (extremes.length === 0) {
    console.log("  (none)");
  }

  for (const e of extremes) {
    console.log(
      `  ${pad(e.symbol, COL.sym)}${padLeft(fmt(e.eightHrRate), COL.rate)}${padLeft(fmt(e.annualizedRate), COL.ann)}  ${e.signal}`
    );
  }

  if (extremes.length > 0) {
    console.log("");
  }

  // Top/bottom 10 from non-extremes, excluding floor rate (0.000096/hr = base interest)
  const FLOOR_RATE = 0.0768; // 8hr rate that equals Lighter's base interest
  const meaningfulNonExtremes = nonExtremes.filter(
    (e) => Math.abs(e.eightHrRate - FLOOR_RATE) > 0.001
  );
  const topPositive = meaningfulNonExtremes
    .filter((e) => e.eightHrRate > 0)
    .slice(0, 10);
  const topNegative = meaningfulNonExtremes
    .filter((e) => e.eightHrRate < 0)
    .slice(0, 10)
    .reverse(); // least negative first from the bottom slice

  if (topPositive.length > 0 || topNegative.length > 0) {
    console.log("  NOTABLE RATES (non-extreme)");
    console.log(`  ${header}`);
    console.log(`  ${divider}`);
  }

  for (const e of [...topPositive, ...topNegative]) {
    console.log(
      `  ${pad(e.symbol, COL.sym)}${padLeft(fmt(e.eightHrRate), COL.rate)}${padLeft(fmt(e.annualizedRate), COL.ann)}  ${e.signal}`
    );
  }

  // Cross-exchange comparison for extreme assets
  const otherExchanges = allRates.filter((r) => r.exchange !== PRIMARY_EXCHANGE && PRIMARY_EXCHANGE);
  const lighterExts = new Set(extremes.map((e) => e.symbol));

  if (lighterExts.size > 0 && otherExchanges.length > 0) {
    console.log("  📊 CROSS-EXCHANGE COMPARISON (extreme assets)");
    const CEX_COLS = { sym: 10, lt: 12, bn: 12, by: 12, hl: 12 };
    const cexHeader =
      pad("ASSET", CEX_COLS.sym) +
      padLeft("LIGHTER", CEX_COLS.lt) +
      padLeft("BINANCE", CEX_COLS.bn) +
      padLeft("BYBIT", CEX_COLS.by) +
      padLeft("HYPERLIQ", CEX_COLS.hl);
    console.log(`  ${cexHeader}`);
    console.log(`  ${"─".repeat(cexHeader.length)}`);

    for (const sym of [...lighterExts].slice(0, 20)) {
      const lt = allRates.find((r) => r.symbol === sym && r.exchange === "lighter");
      const bn = allRates.find((r) => r.symbol === sym && r.exchange === "binance");
      const bby = allRates.find((r) => r.symbol === sym && r.exchange === "bybit");
      const hl = allRates.find((r) => r.symbol === sym && r.exchange === "hyperliquid");
      console.log(
        `  ${pad(sym, CEX_COLS.sym)}${padLeft(lt ? fmt(lt.rate * 100 * 8, 3) : "—", CEX_COLS.lt)}${padLeft(bn ? fmt(bn.rate * 100 * 8, 3) : "—", CEX_COLS.bn)}${padLeft(bby ? fmt(bby.rate * 100 * 8, 3) : "—", CEX_COLS.by)}${padLeft(hl ? fmt(hl.rate * 100 * 8, 3) : "—", CEX_COLS.hl)}`
      );
    }
    console.log("");
  }

  console.log(`  ${divider}`);
  console.log("  Source: Lighter API (mainnet.zklighter.elliot.ai)");
  console.log("  Rates are hourly; 8H = hourly × 8; Annualized = hourly × 8760");
  console.log("  Extreme threshold: ±0.1% per 8hr | Floor rate: 0.0768%/8h (base interest)");
  const atFloor = nonExtremes.filter((e) => Math.abs(e.eightHrRate - FLOOR_RATE) < 0.001).length;
  if (atFloor > 0) console.log(`  ${atFloor} markets at floor rate (neutral, excluded from notable)`);
  console.log("");
}

// --- Run ---

async function main(): Promise<void> {
  try {
    const { entries, allRates } = await fetchFundingRates();
    printSummary(entries, allRates);
  } catch (err) {
    console.error("❌ Failed to fetch funding rates:", (err as Error).message);
    process.exit(1);
  }
}

main();
