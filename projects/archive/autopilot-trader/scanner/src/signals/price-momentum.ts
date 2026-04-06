/**
 * C. Price Momentum Score (0-100) — direction-aware
 *
 * Uses daily_price_change from orderBookDetails.
 * If MA direction is aligned with momentum → boost score by 1.3× (capped at 100).
 * If MA direction opposes momentum → penalize by 0.5×.
 * If MA is neutral ("↔") → use base scoring unchanged.
 *
 * Base scoring by absolute magnitude:
 *   |change| ≥ 15% → 100 (extreme momentum)
 *   |change| ≥ 10% → 80
 *   |change| ≥ 5%  → 60
 *   |change| ≥ 3%  → 40
 *   |change| ≥ 1%  → 20
 *   |change| < 1%  → 10
 */
export function scoreMomentum(dailyPriceChange: number, maDir: "↑" | "↓" | "↔"): number {
  const absChange = Math.abs(dailyPriceChange);

  // Base score from magnitude
  let base: number;
  if (absChange >= 15) base = 100;
  else if (absChange >= 10) base = 80;
  else if (absChange >= 5) base = 60;
  else if (absChange >= 3) base = 40;
  else if (absChange >= 1) base = 20;
  else base = 10;

  // Direction-aware adjustment
  if (maDir === "↔") {
    return base; // neutral MA → no adjustment
  }

  // Check alignment: momentum (price change sign) vs MA direction
  const momentumUp = dailyPriceChange > 0;
  const maUp = maDir === "↑";
  const aligned = (momentumUp && maUp) || (!momentumUp && !maUp);

  if (aligned) {
    return Math.min(100, Math.round(base * 1.3));
  } else {
    return Math.max(1, Math.round(base * 0.5));
  }
}
