import { describe, it, expect } from "bun:test";
import { detectOrderBlocks, scoreOrderBlock } from "../../src/signals/order-block";

describe("detectOrderBlocks", () => {
  it("all flat (no impulse) → both null", () => {
    const n = 20;
    const closes = Array(n).fill(100);
    const opens = Array(n).fill(100);
    const highs = Array(n).fill(100.1);
    const lows = Array(n).fill(99.9);
    const result = detectOrderBlocks(closes, highs, lows, opens);
    expect(result.bullish).toBeNull();
    expect(result.bearish).toBeNull();
  });

  it("insufficient data (< 10) → both null", () => {
    const closes = [100, 99, 101, 100, 99];
    const opens = [100, 100, 100, 100, 100];
    const highs = [101, 100, 102, 101, 100];
    const lows = [99, 98, 100, 99, 98];
    const result = detectOrderBlocks(closes, highs, lows, opens);
    expect(result.bullish).toBeNull();
    expect(result.bearish).toBeNull();
  });

  it("bullish pattern: down candle then 3+ up candles → bullish OB detected", () => {
    // Build candles: flat then a down candle followed by 3 up candles with >2% move
    const closes = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100];
    const opens = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100];
    const highs = [100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1];
    const lows = [99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9];

    // Make index 5 a down candle (close < open)
    closes[5] = 99;
    opens[5] = 100;
    lows[5] = 99;

    // Make indices 6, 7, 8 up candles with >2% move from closes[5]=99
    // Need closes[j] > closes[5] * 1.02 = 100.98
    for (let j = 6; j <= 8; j++) {
      closes[j] = 102;
      opens[j] = 99.5;
      highs[j] = 102.1;
      lows[j] = 99.4;
    }

    const result = detectOrderBlocks(closes, highs, lows, opens);
    expect(result.bullish).not.toBeNull();
    expect(result.bullish!.type).toBe("bullish");
    expect(result.bullish!.price).toBeCloseTo(99, 2);
  });

  it("bearish pattern: up candle then 3+ down candles → bearish OB detected", () => {
    const n = 12;
    const closes = new Array(n).fill(100);
    const opens = new Array(n).fill(100);
    const highs = new Array(n).fill(100.1);
    const lows = new Array(n).fill(99.9);

    // Make index 5 an up candle (close > open)
    closes[5] = 101;
    opens[5] = 100;
    highs[5] = 101;

    // Make indices 6, 7, 8 down candles with >2% move from closes[5]=101
    // Need closes[5] - closes[j] > 101*0.02 = 2.02, so closes[j] < 98.98
    for (let j = 6; j <= 8; j++) {
      closes[j] = 98;
      opens[j] = 101;
      highs[j] = 101.1;
      lows[j] = 97.9;
    }

    const result = detectOrderBlocks(closes, highs, lows, opens);
    expect(result.bearish).not.toBeNull();
    expect(result.bearish!.type).toBe("bearish");
    expect(result.bearish!.price).toBeCloseTo(101, 2);
  });
});

describe("scoreOrderBlock", () => {
  it("no OB detected → score 50, type none", () => {
    const n = 20;
    const closes = Array(n).fill(100);
    const opens = Array(n).fill(100);
    const highs = Array(n).fill(100.1);
    const lows = Array(n).fill(99.9);
    const result = scoreOrderBlock(closes, highs, lows, opens, 100);
    expect(result.score).toBe(50);
    expect(result.obType).toBe("none");
  });

  it("price within 1% of bullish OB → high score, type support", () => {
    const n = 12;
    const closes = new Array(n).fill(100);
    const opens = new Array(n).fill(100);
    const highs = new Array(n).fill(100.1);
    const lows = new Array(n).fill(99.9);

    // Create bullish OB at index 5
    closes[5] = 99;
    opens[5] = 100;
    lows[5] = 99;

    // Impulse up from index 6
    for (let j = 6; j <= 8; j++) {
      closes[j] = 102;
      opens[j] = 99.5;
      highs[j] = 102.1;
      lows[j] = 99.4;
    }

    // Price near the bullish OB (price = 99.5, OB price = 99)
    const result = scoreOrderBlock(closes, highs, lows, opens, 99.5);
    expect(result.obType).toBe("support");
    expect(result.score).toBeGreaterThan(50);
    expect(result.distancePct).not.toBeNull();
    expect(result.obPrice).not.toBeNull();
  });

  it("price within 1% of bearish OB → low score, type resistance", () => {
    const n = 12;
    const closes = new Array(n).fill(100);
    const opens = new Array(n).fill(100);
    const highs = new Array(n).fill(100.1);
    const lows = new Array(n).fill(99.9);

    // Create bearish OB at index 5
    closes[5] = 101;
    opens[5] = 100;
    highs[5] = 101;

    // Impulse down from index 6
    for (let j = 6; j <= 8; j++) {
      closes[j] = 98;
      opens[j] = 101;
      highs[j] = 101.1;
      lows[j] = 97.9;
    }

    // Price near the bearish OB (price = 100.5, OB price = 101)
    const result = scoreOrderBlock(closes, highs, lows, opens, 100.5);
    expect(result.obType).toBe("resistance");
    expect(result.score).toBeLessThan(50);
  });

  it("price far from any OB → score 50", () => {
    const n = 12;
    const closes = new Array(n).fill(100);
    const opens = new Array(n).fill(100);
    const highs = new Array(n).fill(100.1);
    const lows = new Array(n).fill(99.9);

    // Create bullish OB
    closes[5] = 99;
    opens[5] = 100;
    lows[5] = 99;
    for (let j = 6; j <= 8; j++) {
      closes[j] = 102;
      opens[j] = 99.5;
      highs[j] = 102.1;
      lows[j] = 99.4;
    }

    // Price far away (> 5% from OB at 99)
    const result = scoreOrderBlock(closes, highs, lows, opens, 120);
    expect(result.score).toBe(50);
    expect(result.obType).toBe("support"); // still detected but far
  });

  it("both bullish and bearish OBs → picks nearest", () => {
    const n = 20;
    const closes = new Array(n).fill(100);
    const opens = new Array(n).fill(100);
    const highs = new Array(n).fill(100.1);
    const lows = new Array(n).fill(99.9);

    // Bullish OB at index 5
    closes[5] = 99;
    opens[5] = 100;
    lows[5] = 99;
    for (let j = 6; j <= 8; j++) {
      closes[j] = 102;
      opens[j] = 99.5;
      highs[j] = 102.1;
      lows[j] = 99.4;
    }

    // Bearish OB at index 12
    closes[12] = 103;
    opens[12] = 100;
    highs[12] = 103;
    for (let j = 13; j <= 15; j++) {
      closes[j] = 99;
      opens[j] = 103;
      highs[j] = 103.1;
      lows[j] = 98.9;
    }

    // Price at 102 — closer to bullish OB (99, dist ~3%) than bearish OB (103, dist ~1%)
    // Bearish is closer → score should be low
    const result = scoreOrderBlock(closes, highs, lows, opens, 102);
    // Distance to bullish: |102-99|/102 ≈ 2.94%
    // Distance to bearish: |102-103|/102 ≈ 0.98%
    // Bearish is nearest → resistance
    expect(result.obType).toBe("resistance");
  });
});
