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
  qualified: MarketOpportunity[],
): void {
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
  console.log("  LIGHTER.OPPORTUNITY SCANNER");
  console.log(`  ${new Date().toISOString()}`);
  console.log(`  Min score: ${CONFIG.minConfidenceScore}`);
  console.log(`  Scanned: ${liquidMarkets} liquid markets | Qualified: ${qualified.length}`);
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");

  // --- Qualified opportunities ---
  if (qualified.length > 0) {
    console.log("");
    console.log("  ✅ QUALIFIED OPPORTUNITIES");
    console.log("");

    const COL = {
      sym: 10, dir: 5, score: 6, fund: 10, spread: 10, oiTr: 8, mom: 6,
      ma: 10, ob: 14, vol: 10,
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
      padL("DAILYVOL", COL.vol);

    console.log(`  ${header}`);
    console.log(`  ${"─".repeat(header.length)}`);

    for (const o of qualified) {
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
        padL(fmtPct(o.dailyVolatility * 100, 2), COL.vol)
      );
    }
  } else {
    console.log("");
    console.log("  No opportunities qualified (below min score).");
  }

  // --- Summary ---
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
  console.log("  SUMMARY");
  console.log(`  Total markets scanned:       ${liquidMarkets}`);
  console.log(`  Score ≥ ${CONFIG.minConfidenceScore} (qualified):    ${qualified.length}`);
  console.log(`  Below score threshold:       ${opportunities.length - qualified.length}`);

  console.log("");
  console.log("  Signal weights: Funding 35% | MA Alignment 25% | Order Block 15% | Momentum 15% | OI Trend 10%");

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
    config: {
      minConfidenceScore: CONFIG.minConfidenceScore,
      minDailyVolume: CONFIG.minDailyVolume,
    },
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
      dailyVolatility: o.dailyVolatility,
      detectedAt: o.detectedAt,
    })),
  };
  await Bun.write("../ipc/signals.json.tmp", JSON.stringify(signalsOutput, null, 2));
  await rename("../ipc/signals.json.tmp", "../ipc/signals.json");
  console.log("\n  💾 Written: ../ipc/signals.json (atomic write)");
}
