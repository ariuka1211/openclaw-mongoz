/**
 * Lighter.xyz Opportunity Scanner
 *
 * Scans all Lighter perp markets for actionable trading opportunities
 * based on funding rate arbitrage, OI trend, and price momentum.
 *
 * Signal breakdown:
 *   A. Funding Rate Arbitrage — Lighter rate vs CEX average (PRIMARY signal)
 *   B. OI Trend               — open interest change over ~24h (replaces volume anomaly)
 *   C. Price Momentum          — daily_price_change (direction-aware)
 *   D. MA Alignment            — 50/99/200 MA structure
 *   E. Order Block             — nearest S/R order block
 *
 * Position sizing uses risk-based formula:
 *   positionSizeUsd = (equity × riskPct) / stopLossDistance
 *   stopLossDistance = lastPrice × dailyVolatility × stopLossMultiple
 *
 * Safety rules: liq distance ≥ 2× SL, max leverage cap, NaN guards.
 *
 * Usage: bun run src/main.ts [--equity 1000] [--min-score 60] [--max-positions 3]
 */

import type { MarketOpportunity, OiSnapshot } from "./types";
import { CONFIG } from "./config";
import { fetchBalance, fetchOrderBookDetails, fetchFundingRates } from "./api/lighter";
import { fetchOkxKlines, klinesCache } from "./api/okx";
import { getOkxInstId } from "./config";
import { scoreFunding } from "./signals/funding-spread";
import { scoreMomentum } from "./signals/price-momentum";
import { scoreMA } from "./signals/moving-average-alignment";
import { scoreOrderBlock } from "./signals/order-block";
import { loadOiSnapshot, saveOiSnapshot, scoreOiTrend } from "./signals/oi-trend";
import { calculatePosition } from "./position-sizing";
import { computeDirection } from "./direction-vote";
import { displayResults, writeSignalsJson } from "./output";

async function main(): Promise<void> {
  // Parse CLI args for overrides
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--equity" && args[i + 1]) CONFIG.accountEquity = parseFloat(args[++i]);
    if (args[i] === "--min-score" && args[i + 1]) CONFIG.minConfidenceScore = parseFloat(args[++i]);
    if (args[i] === "--max-positions" && args[i + 1]) {
      const val = parseInt(args[++i], 10);
      if (Number.isFinite(val) && val >= 1 && val <= 5) {
        CONFIG.maxConcurrentPositions = val;
      } else {
        console.error("❌ --max-positions must be 1-5");
        process.exit(1);
      }
    }
  }

  if (!Number.isFinite(CONFIG.accountEquity) || CONFIG.accountEquity <= 0) {
    console.error("❌ Invalid account equity:", CONFIG.accountEquity);
    process.exit(1);
  }

  // Load previous OI snapshot for trend comparison
  const prevOiSnapshot = await loadOiSnapshot();
  console.log(`  OI snapshot: ${Object.keys(prevOiSnapshot).length} markets loaded`);

  // Fetch actual balance from Lighter API
  const liveBalance = await fetchBalance();
  if (liveBalance > 0) {
    CONFIG.accountEquity = liveBalance;
    console.log(`  Live balance: $${liveBalance.toFixed(2)} (fetched from Lighter API)`);
  } else {
    console.log(`  Using fallback equity: $${CONFIG.accountEquity}`);
  }

  console.log("Fetching Lighter market data...");
  const [markets, fundingRates] = await Promise.all([
    fetchOrderBookDetails(),
    fetchFundingRates(),
  ]);

  console.log(`  Markets: ${markets.length} | Funding rates: ${fundingRates.length}`);

  // Filter liquid markets
  const liquidMarkets = markets.filter(
    (m) => m.daily_quote_token_volume >= CONFIG.minDailyVolume && m.last_trade_price > 0
  );
  console.log(`  Liquid markets (≥$${(CONFIG.minDailyVolume / 1000).toFixed(0)}K/day): ${liquidMarkets.length}`);

  // Index funding rates by market_id
  const lighterRates = new Map<number, typeof fundingRates[0]>();
  const cexRatesByMarket = new Map<number, number[]>();

  for (const fr of fundingRates) {
    if (!Number.isFinite(fr.rate)) continue;  // skip NaN rates
    if (fr.exchange === "lighter") {
      lighterRates.set(fr.market_id, fr);
    } else {
      const arr = cexRatesByMarket.get(fr.market_id) ?? [];
      arr.push(fr.rate);
      cexRatesByMarket.set(fr.market_id, arr);
    }
  }

  // Score and size each market
  // First pass: identify which markets need OKX klines
  const marketsNeedingKlines: { instId: string; symbol: string }[] = [];
  const scoredMarkets: { market: typeof markets[0]; ltRate: typeof fundingRates[0]; cexRates: number[] }[] = [];

  for (const m of liquidMarkets) {
    const ltRate = lighterRates.get(m.market_id);
    if (!ltRate || !Number.isFinite(ltRate.rate)) continue;
    const cexRates = cexRatesByMarket.get(m.market_id) ?? [];
    scoredMarkets.push({ market: m, ltRate, cexRates });
  }

  // Collect OKX klines with rate limiting (100ms between requests)
  for (const sm of scoredMarkets) {
    const instId = getOkxInstId(sm.market.symbol);
    if (instId && !klinesCache.has(instId)) {
      marketsNeedingKlines.push({ instId, symbol: sm.market.symbol });
    }
  }

  console.log(`  OKX klines to fetch: ${marketsNeedingKlines.length} markets`);
  for (let i = 0; i < marketsNeedingKlines.length; i++) {
    await fetchOkxKlines(marketsNeedingKlines[i].instId);
    if (i < marketsNeedingKlines.length - 1) {
      await Bun.sleep(100); // rate limit: 100ms between requests
    }
  }

  const opportunities: MarketOpportunity[] = [];
  // Build new OI snapshot as we scan
  const newOiSnapshot: OiSnapshot = {};

  for (const { market: m, ltRate, cexRates } of scoredMarkets) {
    // A. Funding arbitrage
    const funding = scoreFunding(ltRate.rate, cexRates);

    // B. OI trend (replaces volume anomaly)
    const prevEntry = prevOiSnapshot[m.symbol];
    const oiResult = scoreOiTrend(m.open_interest, prevEntry?.oi);

    // Record current OI for snapshot
    newOiSnapshot[m.symbol] = {
      oi: m.open_interest,
      timestamp: new Date().toISOString(),
    };

    // C. Momentum (direction-aware — needs MA direction first)
    // D. MA alignment + E. Order Block (from OKX klines)
    let maScore = 50;
    let maDir: "↑" | "↓" | "↔" = "↔";
    let ma50: number | null = null, ma99: number | null = null, ma200: number | null = null;
    let obScore = 50;
    let obType: "support" | "resistance" | "none" = "none";
    let obDistPct: number | null = null;
    let obPrice: number | null = null;

    const instId = getOkxInstId(m.symbol);
    if (instId) {
      const klines = klinesCache.get(instId);
      if (klines && klines.closes.length >= 200) {
        // MA scoring
        const maResult = scoreMA(klines.closes, m.last_trade_price);
        maScore = maResult.score;
        maDir = maResult.direction;
        ma50 = maResult.ma50;
        ma99 = maResult.ma99;
        ma200 = maResult.ma200;

        // OB scoring
        const obResult = scoreOrderBlock(klines.closes, klines.highs, klines.lows, klines.opens, m.last_trade_price);
        obScore = obResult.score;
        obType = obResult.obType;
        obDistPct = obResult.distancePct;
        obPrice = obResult.obPrice;
      }
    }

    // C. Momentum — now direction-aware (needs maDir)
    const momScore = scoreMomentum(m.daily_price_change, maDir);

    // Composite: Funding 35% | MA 25% | OB 15% | Momentum 15% | OI Trend 10%
    const composite = Math.round(
      funding.score * 0.35 + maScore * 0.25 + obScore * 0.15 + momScore * 0.15 + oiResult.score * 0.10
    );

    // Position sizing + safety check
    const pos = calculatePosition(m);

    // Direction: majority vote of MA + OB + funding spread
    const direction = computeDirection(maDir, obType, funding.spread8h);

    opportunities.push({
      symbol: m.symbol,
      marketId: m.market_id,
      fundingSpreadScore: funding.score,
      oiTrendScore: oiResult.score,
      oiChangePct: oiResult.changePct,
      momentumScore: momScore,
      maAlignmentScore: maScore,
      orderBlockScore: obScore,
      compositeScore: composite,
      lighterFundingRate8h: funding.lighter8h,
      cexAvgFundingRate8h: funding.cexAvg8h,
      fundingSpread8h: funding.spread8h,
      dailyVolumeUsd: m.daily_quote_token_volume,
      dailyPriceChange: m.daily_price_change,
      lastPrice: m.last_trade_price,
      maDirection: maDir,
      ma50, ma99, ma200,
      obType,
      obDistancePct: obDistPct,
      obPrice,
      direction,
      maxLeverage: pos.maxLeverage,
      positionSizeUsd: pos.positionSizeUsd,
      actualLeverage: pos.actualLeverage,
      riskAmountUsd: pos.riskAmountUsd,
      stopLossDistanceAbs: pos.stopLossDistanceAbs,
      stopLossDistancePct: pos.stopLossDistancePct,
      liquidationDistancePct: pos.liqDistPct,
      safetyPass: pos.pass,
      safetyReason: pos.reason,
      detectedAt: new Date().toISOString(),
    });
  }

  // Save OI snapshot for next run
  await saveOiSnapshot(newOiSnapshot);
  console.log(`  OI snapshot saved: ${Object.keys(newOiSnapshot).length} markets`);

  // Sort by composite score descending
  opportunities.sort((a, b) => b.compositeScore - a.compositeScore);

  // Filter to minimum score
  const qualified = opportunities.filter((o) => o.compositeScore >= CONFIG.minConfidenceScore);
  const allPassedSafety = qualified.filter((o) => o.safetyPass);
  const failedSafety = qualified.filter((o) => !o.safetyPass);

  // Apply max concurrent positions cap — take only top N
  const passedSafety = allPassedSafety.slice(0, CONFIG.maxConcurrentPositions);

  // Display results
  displayResults(
    liquidMarkets.length,
    opportunities,
    passedSafety,
    allPassedSafety,
    qualified,
    failedSafety,
  );

  // Write signals.json
  await writeSignalsJson(opportunities);
}

main().catch((err) => {
  console.error("❌ Scanner failed:", (err as Error).message);
  process.exit(1);
});
