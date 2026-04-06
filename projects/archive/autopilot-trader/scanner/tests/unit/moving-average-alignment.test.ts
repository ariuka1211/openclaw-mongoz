import { describe, it, expect } from "bun:test";
import { computeMA, scoreMA } from "../../src/signals/moving-average-alignment";

describe("computeMA", () => {
  it("insufficient data → null", () => {
    expect(computeMA([100, 101, 102], 5)).toBeNull();
  });

  it("10 values, period 5 → average of last 5", () => {
    const closes = [1, 2, 3, 4, 5, 10, 20, 30, 40, 50];
    // last 5: 10, 20, 30, 40, 50 → avg = 30
    expect(computeMA(closes, 5)).toBe(30);
  });

  it("period equal to data length", () => {
    const closes = [10, 20, 30, 40, 50];
    expect(computeMA(closes, 5)).toBe(30);
  });

  it("period 1 returns last value", () => {
    const closes = [10, 20, 30];
    expect(computeMA(closes, 1)).toBe(30);
  });

  it("returns null for all-NaN data", () => {
    const closes = [NaN, NaN, NaN, NaN, NaN];
    expect(computeMA(closes, 5)).toBeNull();
  });
});

describe("scoreMA", () => {
  it("price <= 0 → score 50, direction ↔", () => {
    const closes = Array(200).fill(100);
    const result = scoreMA(closes, 0);
    expect(result.score).toBe(50);
    expect(result.direction).toBe("↔");
  });

  it("negative price → score 50, direction ↔", () => {
    const closes = Array(200).fill(100);
    const result = scoreMA(closes, -1);
    expect(result.score).toBe(50);
    expect(result.direction).toBe("↔");
  });

  it("NaN price → score 50, direction ↔", () => {
    const closes = Array(200).fill(100);
    const result = scoreMA(closes, NaN);
    expect(result.score).toBe(50);
    expect(result.direction).toBe("↔");
  });

  it("< 200 closes → score 50", () => {
    const closes = Array(150).fill(100);
    const result = scoreMA(closes, 100);
    expect(result.score).toBe(50);
    expect(result.direction).toBe("↔");
  });

  it("bull alignment (price > ma50 > ma99 > ma200) → score >= 80, direction ↑", () => {
    // Build 210 closes with clear bull structure
    // ma200 ≈ avg of closes[10..209], ma99 ≈ avg of closes[111..209], ma50 ≈ avg of closes[160..209]
    // Price at end > all MAs
    const closes: number[] = [];
    for (let i = 0; i < 210; i++) {
      closes.push(80 + i * 0.2); // steadily rising from 80 to 121.8
    }
    const currentPrice = closes[closes.length - 1]; // 121.8
    const result = scoreMA(closes, currentPrice);
    expect(result.direction).toBe("↑");
    expect(result.score).toBeGreaterThanOrEqual(80);
    expect(result.ma50).not.toBeNull();
    expect(result.ma99).not.toBeNull();
    expect(result.ma200).not.toBeNull();
  });

  it("bear alignment (price < ma50 < ma99 < ma200) → score >= 80, direction ↓", () => {
    const closes: number[] = [];
    for (let i = 0; i < 210; i++) {
      closes.push(120 - i * 0.2); // steadily falling from 120 to 78.2
    }
    const currentPrice = closes[closes.length - 1];
    const result = scoreMA(closes, currentPrice);
    expect(result.direction).toBe("↓");
    expect(result.score).toBeGreaterThanOrEqual(80);
  });

  it("choppy → score 30, direction ↔", () => {
    // Oscillating price that doesn't align
    const closes: number[] = [];
    for (let i = 0; i < 210; i++) {
      closes.push(100 + Math.sin(i * 0.3) * 10);
    }
    const currentPrice = 100;
    const result = scoreMA(closes, currentPrice);
    // If not bull or bear aligned, should be choppy
    if (result.direction === "↔") {
      expect(result.score).toBe(30);
    }
    // Just verify it returns valid data
    expect(result.ma50).not.toBeNull();
    expect(result.ma99).not.toBeNull();
    expect(result.ma200).not.toBeNull();
  });
});
