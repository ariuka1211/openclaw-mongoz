import { rename } from "node:fs/promises";
import type { MarketOpportunity } from "./types";
import { CONFIG } from "./config";

// --- Helpers ---

export function fmtPct(pct: number, decimals = 3): string {
  const s = pct.toFixed(decimals);
  return pct >= 0 ? `+${s}%` : `${s}%`;
}

export function fmtUsd(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

export function pad(s: string, len: number): string {
  return s.length >= len ? s : s + " ".repeat(len - s.length);
}

export function padL(s: string, len: number): string {
  return s.length >= len ? s : " ".repeat(len - s.length) + s;
}

export function displayResults(
  liquidMarkets: number,
  opportunities: MarketOpportunity[],
  passedSafety: MarketOpportunity[],
  allPassedSafety: MarketOpportunity[],
  qualified: MarketOpportunity[],
  failedSafety: MarketOpportunity[],
): void {
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
  console.log("  LIGHTER.OPPORTUNITY SCANNER");
  console.log(`  ${new Date().toISOString()}`);
  console.log(`  Equity: $${CONFIG.accountEquity} | Risk/trade: ${(CONFIG.riskPct * 100).toFixed(0)}% ($${(CONFIG.accountEquity * CONFIG.riskPct).toFixed(0)}) | SL multiple: ${CONFIG.stopLossMultiple}× daily vol`);
  console.log(`  Max positions: ${CONFIG.maxConcurrentPositions} | Max leverage: ${CONFIG.maxLeverageCap}× | Min score: ${CONFIG.minConfidenceScore}`);
  console.log(`  Scanned: ${liquidMarkets} liquid markets | Qualified: ${qualified.length} | Safety passed: ${allPassedSafety.length} | Selected: ${passedSafety.length}`);
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");

  // --- Opportunities that passed safety (capped by max positions) ---
  if (passedSafety.length > 0) {
    console.log("");
    console.log("  ✅ SELECTED POSITIONS (risk-based sizing)");
    console.log("");

    const COL = {
      sym: 10, dir: 5, score: 6, fund: 10, spread: 10, oiTr: 8, mom: 6,
      ma: 10, ob: 14,
      risk: 8, slPct: 8, slAbs: 10, posSize: 10, lev: 6, liqD: 8,
    };

    const header =
      pad("SYMBOL", COL.sym) +
      padL("DIR", COL.dir) +
      padL("SCORE", COL.score) +
      padL("FUND8H", COL.fund) +
      padL("SPREAD8H", COL.spread) +
      padL("OI TREND", COL.oiTr) +
      padL("CHG%", COL.mom) +
      padL("MA", COL.ma) +
      padL("OB", COL.ob) +
      padL("RISK $", COL.risk) +
      padL("SL %", COL.slPct) +
      padL("SL $", COL.slAbs) +
      padL("POS USD", COL.posSize) +
      padL("LEV×", COL.lev) +
      padL("LIQ D", COL.liqD);

    console.log(`  ${header}`);
    console.log(`  ${"─".repeat(header.length)}`);

    for (const o of passedSafety) {
      const maStr = `${o.maDirection}${o.maAlignmentScore}`;
      const obDistStr = o.obDistancePct !== null ? `${o.obDistancePct.toFixed(1)}%` : "—";
      const obStr = o.obType !== "none" ? `${o.obType === "support" ? "S" : "R"} ${obDistStr}` : "none";
      const oiStr = `${o.oiTrendScore}(${o.oiChangePct >= 0 ? "+" : ""}${o.oiChangePct.toFixed(0)}%)`;

      console.log(
        `  ${pad(o.symbol, COL.sym)}` +
        padL(o.direction === "long" ? "L" : "S", COL.dir) +
        padL(String(o.compositeScore), COL.score) +
        padL(fmtPct(o.lighterFundingRate8h, 3), COL.fund) +
        padL(fmtPct(o.fundingSpread8h, 3), COL.spread) +
        padL(oiStr, COL.oiTr) +
        padL(fmtPct(o.dailyPriceChange, 1), COL.mom) +
        padL(maStr, COL.ma) +
        padL(obStr, COL.ob) +
        padL(fmtUsd(o.riskAmountUsd), COL.risk) +
        padL(`${o.stopLossDistancePct.toFixed(2)}%`, COL.slPct) +
        padL(fmtUsd(o.stopLossDistanceAbs), COL.slAbs) +
        padL(fmtUsd(o.positionSizeUsd), COL.posSize) +
        padL(`${o.actualLeverage.toFixed(1)}×`, COL.lev) +
        padL(`${o.liquidationDistancePct.toFixed(1)}%`, COL.liqD)
      );
    }

    // Total risk exposure
    const totalRiskUsd = passedSafety.reduce((s, o) => s + o.riskAmountUsd, 0);
    const totalRiskPct = (totalRiskUsd / CONFIG.accountEquity) * 100;
    const totalExposureUsd = passedSafety.reduce((s, o) => s + o.positionSizeUsd, 0);
    console.log("");
    console.log(`  📊 Total risk exposure: ${fmtUsd(totalRiskUsd)} (${totalRiskPct.toFixed(1)}% of equity across ${passedSafety.length} positions)`);
    console.log(`  📊 Total position exposure: ${fmtUsd(totalExposureUsd)} (${(totalExposureUsd / CONFIG.accountEquity * 100).toFixed(1)}% of equity)`);
  } else {
    console.log("");
    console.log("  No opportunities passed safety checks.");
  }

  // Show truncated positions if any were cut by max-positions cap
  const truncatedByCap = allPassedSafety.length - passedSafety.length;
  if (truncatedByCap > 0) {
    console.log("");
    console.log(`  ⚠️  ${truncatedByCap} more positions available but capped at ${CONFIG.maxConcurrentPositions} (--max-positions)`);
    for (const o of allPassedSafety.slice(CONFIG.maxConcurrentPositions)) {
      console.log(`     ${o.direction === "long" ? "L" : "S"} ${o.symbol} score=${o.compositeScore} pos=${fmtUsd(o.positionSizeUsd)} lev=${o.actualLeverage.toFixed(1)}×`);
    }
  }

  // --- Opportunities that failed safety ---
  if (failedSafety.length > 0) {
    console.log("");
    console.log("  ❌ QUALIFIED BUT FAILED SAFETY CHECK");
    console.log("");

    for (const o of failedSafety.slice(0, 10)) {
      console.log(
        `  ${o.direction === "long" ? "L" : "S"} ${pad(o.symbol, 10)} score=${o.compositeScore}  ` +
        `liq=${o.liquidationDistancePct.toFixed(1)}%  ` +
        `sl=${o.stopLossDistancePct.toFixed(1)}%  ` +
        `→ ${o.safetyReason}`
      );
    }
    if (failedSafety.length > 10) {
      console.log(`  ... and ${failedSafety.length - 10} more`);
    }
  }

  // --- Summary ---
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
  console.log("  SUMMARY");
  console.log(`  Total markets scanned:       ${liquidMarkets}`);
  console.log(`  Score ≥ ${CONFIG.minConfidenceScore} (qualified):    ${qualified.length}`);
  console.log(`  Passed safety:               ${allPassedSafety.length}`);
  console.log(`  Selected (max ${CONFIG.maxConcurrentPositions}):              ${passedSafety.length}`);
  console.log(`  Failed safety:               ${failedSafety.length}`);
  console.log(`  Below score threshold:       ${opportunities.length - qualified.length}`);

  if (passedSafety.length > 0) {
    const totalRiskUsd = passedSafety.reduce((s, o) => s + o.riskAmountUsd, 0);
    const totalExposure = passedSafety.reduce((s, o) => s + o.positionSizeUsd, 0);
    console.log(`  Risk per trade:              ${fmtUsd(CONFIG.accountEquity * CONFIG.riskPct)} (${(CONFIG.riskPct * 100).toFixed(0)}% of equity)`);
    console.log(`  Total risk (all positions):  ${fmtUsd(totalRiskUsd)} (${(totalRiskUsd / CONFIG.accountEquity * 100).toFixed(1)}% of equity)`);
    console.log(`  Total exposure:              ${fmtUsd(totalExposure)} (${(totalExposure / CONFIG.accountEquity * 100).toFixed(1)}% of equity)`);
  }

  console.log("");
  console.log("  Signal weights: Funding 35% | MA Alignment 25% | Order Block 15% | Momentum 15% | OI Trend 10%");
  console.log("  Sizing: risk-based (equity × riskPct / SL distance) | Max 20× leverage | Liq ≥ 2× SL");

  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
}

export async function writeSignalsJson(opportunities: MarketOpportunity[]): Promise<void> {
  // === Signal Cleanup: Remove opportunities older than 20 minutes ===
  const MAX_SIGNAL_AGE_MS = 20 * 60 * 1000; // 20 minutes
  const cleanupNow = new Date();
  const freshOpportunities = opportunities.filter(o => {
    if (!o.detectedAt) return true; // Keep if no timestamp (legacy)
    const age = cleanupNow.getTime() - new Date(o.detectedAt).getTime();
    return age <= MAX_SIGNAL_AGE_MS;
  });
  const removedCount = opportunities.length - freshOpportunities.length;
  if (removedCount > 0) {
    console.log(`  🗑️ Cleanup: Removed ${removedCount} stale opportunities (>20min old)`);
  }

  // Write signals.json with fresh opportunities only
  const signalsOutput = {
    timestamp: new Date().toISOString(),
    config: { ...CONFIG },
    opportunities: freshOpportunities.map(o => ({
      symbol: o.symbol,
      marketId: o.marketId,
      compositeScore: o.compositeScore,
      fundingSpreadScore: o.fundingSpreadScore,
      oiTrendScore: o.oiTrendScore,
      oiChangePct: o.oiChangePct,
      momentumScore: o.momentumScore,
      maAlignmentScore: o.maAlignmentScore,
      orderBlockScore: o.orderBlockScore,
      lighterFundingRate8h: o.lighterFundingRate8h,
      cexAvgFundingRate8h: o.cexAvgFundingRate8h,
      fundingSpread8h: o.fundingSpread8h,
      dailyVolumeUsd: o.dailyVolumeUsd,
      dailyPriceChange: o.dailyPriceChange,
      lastPrice: o.lastPrice,
      maDirection: o.maDirection,
      ma50: o.ma50,
      ma99: o.ma99,
      ma200: o.ma200,
      obType: o.obType,
      obDistancePct: o.obDistancePct,
      obPrice: o.obPrice,
      direction: o.direction,
      positionSizeUsd: o.positionSizeUsd,
      actualLeverage: o.actualLeverage,
      riskAmountUsd: o.riskAmountUsd,
      stopLossDistancePct: o.stopLossDistancePct,
      liquidationDistancePct: o.liquidationDistancePct,
      safetyPass: o.safetyPass,
      safetyReason: o.safetyReason,
      detectedAt: o.detectedAt,
    })),
  };
  await Bun.write("../ipc/signals.json.tmp", JSON.stringify(signalsOutput, null, 2));
  await rename("../ipc/signals.json.tmp", "../ipc/signals.json");
  console.log("\n  💾 Written: ../ipc/signals.json (atomic write)");
}
