import type { MaResult } from "../types";

export function computeMA(closes: number[], period: number): number | null {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  const sum = slice.reduce((s, v) => s + v, 0);
  const avg = sum / period;
  return Number.isFinite(avg) ? avg : null;
}

export function scoreMA(closes: number[], currentPrice: number): MaResult {
  if (currentPrice <= 0 || !Number.isFinite(currentPrice)) {
    return { score: 50, direction: "↔", ma50: null, ma99: null, ma200: null };
  }
  if (closes.length < 200) return { score: 50, direction: "↔", ma50: null, ma99: null, ma200: null };

  const ma50 = computeMA(closes, 50);
  const ma99 = computeMA(closes, 99);
  const ma200 = computeMA(closes, 200);

  if (ma50 === null || ma99 === null || ma200 === null) {
    return { score: 50, direction: "↔", ma50, ma99, ma200 };
  }

  const bullAlignment = currentPrice > ma50 && ma50 > ma99 && ma99 > ma200;
  const bearAlignment = currentPrice < ma50 && ma50 < ma99 && ma99 < ma200;

  if (bullAlignment) {
    // Score based on how clean: measure spread as % of price
    const spread = (currentPrice - ma200) / currentPrice;
    const score = Math.min(100, Math.round(80 + spread * 200));
    return { score, direction: "↑", ma50, ma99, ma200 };
  }

  if (bearAlignment) {
    const spread = (ma200 - currentPrice) / currentPrice;
    const score = Math.min(100, Math.round(80 + spread * 200));
    return { score, direction: "↓", ma50, ma99, ma200 };
  }

  // Choppy: price between MAs
  return { score: 30, direction: "↔", ma50, ma99, ma200 };
}
