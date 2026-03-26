import type { OrderBookDetail } from "./types";
import { CONFIG } from "./config";

/**
 * Calculate position size using risk-based formula and run safety checks.
 *
 * RISK-BASED SIZING:
 *   riskAmountUsd = accountEquity × riskPct
 *   dailyVolatility = (dailyHigh - dailyLow) / lastPrice
 *   stopLossDistance = dailyVolatility × stopLossMultiple
 *   positionSizeUsd = riskAmountUsd / stopLossDistance
 *
 * Example: $1000 equity, 5% risk ($50), 2% SL distance → position = $50/0.02 = $2500
 *
 * SAFETY RULES:
 *   - NaN guards on all inputs
 *   - Max position capped at fixed USD per position
 */
export function calculatePosition(market: OrderBookDetail): {
  positionSizeUsd: number;
  riskAmountUsd: number;
  stopLossDistanceAbs: number;
  stopLossDistancePct: number;
  pass: boolean;
  reason: string;
} {
  const { accountEquity, riskPct, stopLossMultiple } = CONFIG;

  // Validate all required numeric fields are finite and positive
  const requiredFields = [
    market.last_trade_price,
    market.daily_price_high,
    market.daily_price_low,
    market.daily_quote_token_volume,
  ];
  if (requiredFields.some(f => !Number.isFinite(f) || f < 0)) {
    return {
      positionSizeUsd: 0,
      riskAmountUsd: 0,
      stopLossDistanceAbs: 0,
      stopLossDistancePct: 0,
      pass: false,
      reason: "Invalid numeric data from API",
    };
  }

  // --- Risk-based position sizing ---
  const riskAmountUsd = accountEquity * riskPct;  // $ risked per trade
  const dailyVolatility = (market.daily_price_high - market.daily_price_low) / market.last_trade_price;
  const stopLossDistancePct = dailyVolatility * stopLossMultiple * 100;  // as %
  const stopLossDistanceAbs = dailyVolatility * stopLossMultiple * market.last_trade_price;  // in $

  if (stopLossDistancePct <= 0) {
    return {
      positionSizeUsd: 0,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct: 0,
      pass: false, reason: "No stop-loss range data (zero volatility)",
    };
  }

  // Core formula: position = riskAmount / SL_distance
  let positionSizeUsd = riskAmountUsd / (stopLossDistancePct / 100);

  // Hard cap by fixed USD per position
  positionSizeUsd = Math.min(positionSizeUsd, CONFIG.maxPositionUsd);

  if (positionSizeUsd <= 0) {
    return {
      positionSizeUsd: 0,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct,
      pass: false, reason: "Zero position",
    };
  }

  return {
    positionSizeUsd,
    riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct,
    pass: true, reason: "PASS",
  };
}
