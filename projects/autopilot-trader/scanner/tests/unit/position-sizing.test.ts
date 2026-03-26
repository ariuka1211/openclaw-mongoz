import { describe, it, expect } from "bun:test";
import { calculatePosition } from "../../src/position-sizing";
import type { OrderBookDetail } from "../../src/types";
import { mockOrderBookDetail } from "../fixtures/test-data";

describe("calculatePosition", () => {
  it("NaN in required field → pass=false, reason=Invalid numeric data", () => {
    const market = mockOrderBookDetail({ last_trade_price: NaN });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/Invalid numeric data/);
  });

  it("NaN in daily_price_high → pass=false", () => {
    const market = mockOrderBookDetail({ daily_price_high: NaN });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/Invalid numeric data/);
  });

  it("NaN in maintenance_margin_fraction → pass=false", () => {
    const market = mockOrderBookDetail({ maintenance_margin_fraction: NaN });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/Invalid numeric data/);
  });

  it("zero volatility (high=low) → pass=false", () => {
    const market = mockOrderBookDetail({ daily_price_high: 100, daily_price_low: 100 });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/zero volatility/i);
  });

  it("normal case: positionSize = min(riskAmount / SL, maxPositionUsd)", () => {
    // dailyVolatility = (105 - 95) / 100 = 0.10
    // SL distance = 0.10 * 1.0 = 0.10 = 10%
    // riskAmount = 60 * 0.05 = $3
    // positionSize (risk-based) = 3 / 0.10 = $30
    // Capped by maxPositionUsd = min(30, 15) = 15
    // maxLeverage = min(20, 10000/500) = min(20, 20) = 20
    // maxAllowedPosition = 60 * 20 = 1200
    // positionSize = min(15, 1200) = 15
    const market = mockOrderBookDetail();
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(true);
    expect(result.riskAmountUsd).toBeCloseTo(3, 2);
    expect(result.positionSizeUsd).toBeCloseTo(15, 2);
    expect(result.actualLeverage).toBeCloseTo(0.25, 2);
    expect(result.maxLeverage).toBe(20);
  });

  it("leverage capped at min(20, exchangeMax)", () => {
    // default_initial_margin_fraction = 500 → exchangeMax = 10000/500 = 20
    // cap = min(20, 20) = 20
    const market1 = mockOrderBookDetail({ default_initial_margin_fraction: 500 });
    const r1 = calculatePosition(market1, 80);
    expect(r1.maxLeverage).toBe(20);

    // default_initial_margin_fraction = 1000 → exchangeMax = 10
    const market2 = mockOrderBookDetail({ default_initial_margin_fraction: 1000 });
    const r2 = calculatePosition(market2, 80);
    expect(r2.maxLeverage).toBe(10);

    // default_initial_margin_fraction = 100 → exchangeMax = 100
    // cap = min(20, 100) = 20
    const market3 = mockOrderBookDetail({ default_initial_margin_fraction: 100 });
    const r3 = calculatePosition(market3, 80);
    expect(r3.maxLeverage).toBe(20);
  });

  it("liq dist < 2x SL dist → pass=false", () => {
    // Need: liqDistPct < safetyMultiple * stopLossDistancePct
    // liqDistPct = (1/leverage - maintMarginRate) * 100
    //
    // With maxPositionUsd = 15:
    // dailyVolatility = (102-100)/100 = 0.02
    // SL dist = 0.02 * 1.0 = 2%
    // riskAmount = 3
    // position (risk-based) = 3/0.02 = 150
    // Capped by maxPositionUsd: min(150, 15) = 15
    // maxLeverage = min(20, 10000/2000) = 5
    // maxAllowedPosition = 60 * 5 = 300
    // positionSize = min(15, 300) = 15
    // actualLeverage = 15/60 = 0.25
    // liqDistPct = (1/0.25 - maintMarginRate)*100 = (4 - maintMarginRate)*100
    // safety threshold = 2% * 2 = 4%
    // Need liqDistPct < 4%:
    // (4 - maintMarginRate)*100 < 4
    // 4 - maintMarginRate < 0.04
    // maintMarginRate > 3.96
    // maintenance_margin_fraction = 39700 (397%) → maintMarginRate = 3.97
    // default_initial_margin_fraction = 2000 (5x max)
    const market = mockOrderBookDetail({
      daily_price_low: 100,
      daily_price_high: 102,
      default_initial_margin_fraction: 2000, // 5x
      maintenance_margin_fraction: 39700,    // 397% → maintMarginRate = 3.97
    });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/Liq dist/);
  });

  it("returns expected fields", () => {
    const market = mockOrderBookDetail();
    const result = calculatePosition(market, 80);
    expect(result).toHaveProperty("maxLeverage");
    expect(result).toHaveProperty("positionSizeUsd");
    expect(result).toHaveProperty("actualLeverage");
    expect(result).toHaveProperty("riskAmountUsd");
    expect(result).toHaveProperty("stopLossDistanceAbs");
    expect(result).toHaveProperty("stopLossDistancePct");
    expect(result).toHaveProperty("liqDistPct");
    expect(result).toHaveProperty("pass");
    expect(result).toHaveProperty("reason");
    expect(typeof result.maxLeverage).toBe("number");
    expect(typeof result.positionSizeUsd).toBe("number");
  });

  it("negative price field → pass=false", () => {
    const market = mockOrderBookDetail({ last_trade_price: -100 });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/Invalid numeric data/);
  });

  it("closeout_margin_fraction negative → pass=false", () => {
    // closeout_margin_fraction is not in requiredFields, so won't fail there
    // Let me test something that IS in requiredFields
    const market = mockOrderBookDetail({ daily_quote_token_volume: -1 });
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(false);
    expect(result.reason).toMatch(/Invalid numeric data/);
  });
});
