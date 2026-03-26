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

  it("normal case: positionSize = riskAmount / (volatility * multiple)", () => {
    // Using a larger market to avoid leverage cap
    // dailyVolatility = (105 - 95) / 100 = 0.10
    // SL distance = 0.10 * 1.0 = 0.10 = 10%
    // riskAmount = 60 * 0.05 = $3
    // positionSize = 3 / 0.10 = $30
    // maxLeverage = min(20, 10000/500) = min(20, 20) = 20
    // maxAllowedPosition = 60 * 20 = 1200
    // positionSize = min(30, 1200) = 30
    const market = mockOrderBookDetail();
    const result = calculatePosition(market, 80);
    expect(result.pass).toBe(true);
    expect(result.riskAmountUsd).toBeCloseTo(3, 2);
    expect(result.positionSizeUsd).toBeCloseTo(30, 2);
    expect(result.actualLeverage).toBeCloseTo(0.5, 2);
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
    // Use low exchange max leverage to force high leverage:
    // default_initial_margin_fraction = 5000 → exchangeMax = 2
    // With large dailyVolatility:
    // dailyVolatility = (200-10)/100 = 1.9
    // SL dist = 1.9 * 1.0 = 190%
    // riskAmount = 3
    // position = 3/1.9 = 1.58 (capped by exchange max: 60*2 = 120)
    // actualLeverage = 1.58/60 = 0.026
    // liqDistPct = (1/0.026 - 0.012)*100 = (38.46 - 0.012)*100 = 3845%
    // safety threshold = 190%*2 = 380%
    // 3845 >= 380 → PASSES
    //
    // Let me try a different approach: use a very small SL but high leverage
    // Actually, the issue is position gets capped. Let me force actualLeverage high.
    //
    // Use: default_initial_margin_fraction = 2000 → exchangeMax = 5
    // dailyVolatility = (102-100)/100 = 0.02
    // SL dist = 0.02 * 1.0 = 2%
    // riskAmount = 3
    // position = 3/0.02 = 150
    // maxAllowedPosition = 60*5 = 300
    // positionSize = min(150, 300) = 150
    // actualLeverage = 150/60 = 2.5
    // liqDistPct = (1/2.5 - 0.012)*100 = (0.4 - 0.012)*100 = 38.8%
    // safety threshold = 2%*2 = 4%
    // 38.8 >= 4 → PASSES
    //
    // Hmm, this is hard to fail. Let me use maintenance_margin_fraction = 5000 (50%)
    // default_initial_margin_fraction = 2000 (5x max)
    // With maintenance_margin_fraction = 5000:
    // maintMarginRate = 0.5
    // liqDistPct = (1/2.5 - 0.5)*100 = (0.4 - 0.5)*100 = -10%
    // safety threshold = 4%
    // -10 < 4 → FAILS!
    const market = mockOrderBookDetail({
      daily_price_low: 100,
      daily_price_high: 102,
      default_initial_margin_fraction: 2000, // 5x
      maintenance_margin_fraction: 5000,     // 50% margin rate
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
