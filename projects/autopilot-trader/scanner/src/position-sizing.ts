import type { OrderBookDetail } from "../types";
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
 *   - Max leverage capped at 20×
 *   - Liquidation distance must be ≥ 2× stop-loss distance
 *   - NaN guards on all inputs
 */
export function calculatePosition(market: OrderBookDetail, compositeScore: number): {
  maxLeverage: number;
  positionSizeUsd: number;
  actualLeverage: number;
  riskAmountUsd: number;
  stopLossDistanceAbs: number;
  stopLossDistancePct: number;
  liqDistPct: number;
  pass: boolean;
  reason: string;
} {
  const { accountEquity, riskPct, stopLossMultiple, maxLeverageCap, safetyMultiple } = CONFIG;

  // Validate all required numeric fields are finite and positive
  const requiredFields = [
    market.last_trade_price,
    market.daily_price_high,
    market.daily_price_low,
    market.maintenance_margin_fraction,
    market.default_initial_margin_fraction,
    market.daily_quote_token_volume,
  ];
  if (requiredFields.some(f => !Number.isFinite(f) || f < 0)) {
    return {
      maxLeverage: 0,
      positionSizeUsd: 0,
      actualLeverage: 0,
      riskAmountUsd: 0,
      stopLossDistanceAbs: 0,
      stopLossDistancePct: 0,
      liqDistPct: 0,
      pass: false,
      reason: "Invalid numeric data from API",
    };
  }

  // Max leverage: exchange-allowed capped by our hard limit
  const exchangeMaxLeverage = 10000 / market.default_initial_margin_fraction;
  const maxLeverage = Math.min(maxLeverageCap, exchangeMaxLeverage);

  // --- Risk-based position sizing ---
  const riskAmountUsd = accountEquity * riskPct;  // $ risked per trade
  const dailyVolatility = (market.daily_price_high - market.daily_price_low) / market.last_trade_price;
  const stopLossDistancePct = dailyVolatility * stopLossMultiple * 100;  // as %
  const stopLossDistanceAbs = dailyVolatility * stopLossMultiple * market.last_trade_price;  // in $

  if (stopLossDistancePct <= 0) {
    return {
      maxLeverage, positionSizeUsd: 0, actualLeverage: 0,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct: 0, liqDistPct: 0,
      pass: false, reason: "No stop-loss range data (zero volatility)",
    };
  }

  // Core formula: position = riskAmount / SL_distance
  let positionSizeUsd = riskAmountUsd / (stopLossDistancePct / 100);

  // Hard cap: never exceed maxLeverage × equity
  const maxAllowedPosition = accountEquity * maxLeverage;
  positionSizeUsd = Math.min(positionSizeUsd, maxAllowedPosition);

  // Actual leverage = position / equity
  const actualLeverage = positionSizeUsd / accountEquity;

  if (actualLeverage <= 0) {
    return {
      maxLeverage, positionSizeUsd: 0, actualLeverage: 0,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct: 0,
      pass: false, reason: "Zero position",
    };
  }

  // Liquidation distance (how far price must move to liquidate, as % of entry)
  const maintMarginRate = market.maintenance_margin_fraction / 10000;
  const liqDistPct = (1 / actualLeverage - maintMarginRate) * 100;

  // SAFETY CHECK: liquidation must be ≥ 2× stop-loss distance
  if (liqDistPct < stopLossDistancePct * safetyMultiple) {
    return {
      maxLeverage, positionSizeUsd, actualLeverage,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct,
      pass: false,
      reason: `Liq dist (${liqDistPct.toFixed(2)}%) < ${safetyMultiple}× SL dist (${(stopLossDistancePct * safetyMultiple).toFixed(2)}%)`,
    };
  }

  return {
    maxLeverage, positionSizeUsd, actualLeverage,
    riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct,
    pass: true, reason: "PASS",
  };
}
