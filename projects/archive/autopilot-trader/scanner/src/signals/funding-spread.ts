import { EIGHT_HR_MULTIPLIER } from "../config";

/**
 * A. Funding Rate Arbitrage Score (0-100)
 *
 * Compares Lighter's hourly funding rate against the average of CEX rates
 * (Binance, Bybit, Hyperliquid) for the same market.
 *
 * The funding SPREAD is what you arbitrage:
 *   - If Lighter longs pay 0.05%/8h and Binance charges 0.01%/8h, spread = 0.04%
 *   - Positive spread = Lighter longs overpay → short on Lighter, long on CEX
 *   - Negative spread = Lighter longs underpay → long on Lighter, short on CEX
 *
 * Bigger absolute spread = higher score.
 * Score range: 0 = no spread, 100 = extreme spread (≥0.15%/8hr).
 */
export function scoreFunding(
  lighterRate: number,    // hourly decimal
  cexRates: number[]      // hourly decimals from other exchanges
): { score: number; lighter8h: number; cexAvg8h: number; spread8h: number } {
  const lighter8h = lighterRate * 100 * EIGHT_HR_MULTIPLIER;  // % per 8hr
  const validCexRates = cexRates.filter(r => Number.isFinite(r));
  let cexAvg8h = 0;

  if (validCexRates.length > 0) {
    const avgHourly = validCexRates.reduce((s, r) => s + r, 0) / validCexRates.length;
    cexAvg8h = avgHourly * 100 * EIGHT_HR_MULTIPLIER;
  }

  const spread8h = lighter8h - cexAvg8h;
  const absSpread = Math.abs(spread8h);

  // Score: 0.15%/8hr spread = 100 points, linear below that
  const score = Math.min(100, (absSpread / 0.15) * 100);

  return { score: Math.round(score), lighter8h, cexAvg8h, spread8h };
}
