import type { ObLevel, ObResult } from "../types";

export function detectOrderBlocks(closes: number[], highs: number[], lows: number[], opens: number[]): { bullish: ObLevel | null; bearish: ObLevel | null } {
  const lookback = Math.min(100, closes.length);
  if (lookback < 10) return { bullish: null, bearish: null };

  let bullishOb: ObLevel | null = null;
  let bearishOb: ObLevel | null = null;

  // Scan from oldest to newest within lookback window
  for (let i = closes.length - lookback; i < closes.length - 4; i++) {
    // Bullish OB: last down candle before impulse up
    if (closes[i] < opens[i]) { // down candle
      // Check for impulse up after
      let bullishCount = 0;
      let movePct = 0;
      for (let j = i + 1; j < Math.min(i + 6, closes.length); j++) {
        if (closes[j] > opens[j]) bullishCount++;
        movePct = (closes[j] - closes[i]) / closes[i];
        if (bullishCount >= 3 || movePct > 0.02) {
          bullishOb = { type: "bullish", price: lows[i] };
          break;
        }
      }
    }

    // Bearish OB: last up candle before impulse down
    if (closes[i] > opens[i]) { // up candle
      let bearishCount = 0;
      let movePct = 0;
      for (let j = i + 1; j < Math.min(i + 6, closes.length); j++) {
        if (closes[j] < opens[j]) bearishCount++;
        movePct = (closes[i] - closes[j]) / closes[i];
        if (bearishCount >= 3 || movePct > 0.02) {
          bearishOb = { type: "bearish", price: highs[i] };
          break;
        }
      }
    }
  }

  return { bullish: bullishOb, bearish: bearishOb };
}

export function scoreOrderBlock(closes: number[], highs: number[], lows: number[], opens: number[], currentPrice: number): ObResult {
  const { bullish, bearish } = detectOrderBlocks(closes, highs, lows, opens);

  if (!bullish && !bearish) return { score: 50, obType: "none", distancePct: null, obPrice: null };

  // Find nearest OB and compute score
  let bestOb: ObLevel | null = null;
  let bestDistPct = Infinity;

  if (bullish) {
    const dist = Math.abs(currentPrice - bullish.price) / currentPrice;
    if (dist < bestDistPct) { bestDistPct = dist; bestOb = bullish; }
  }
  if (bearish) {
    const dist = Math.abs(currentPrice - bearish.price) / currentPrice;
    if (dist < bestDistPct) { bestDistPct = dist; bestOb = bearish; }
  }

  if (!bestOb) return { score: 50, obType: "none", distancePct: null, obPrice: null };

  const distPct = (bestDistPct * 100);

  if (bestOb.type === "bullish") {
    // Price near bullish OB (support) → high score (70-100)
    if (distPct <= 1) {
      const score = Math.round(100 - (distPct / 1) * 30); // closer = higher
      return { score, obType: "support", distancePct: distPct, obPrice: bestOb.price };
    }
    return { score: 50, obType: "support", distancePct: distPct, obPrice: bestOb.price };
  } else {
    // Price near bearish OB (resistance) → low score (20-30)
    if (distPct <= 1) {
      const score = Math.round(20 + (distPct / 1) * 10);
      return { score, obType: "resistance", distancePct: distPct, obPrice: bestOb.price };
    }
    return { score: 50, obType: "resistance", distancePct: distPct, obPrice: bestOb.price };
  }
}
