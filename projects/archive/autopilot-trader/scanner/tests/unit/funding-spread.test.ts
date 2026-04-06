import { describe, it, expect } from "bun:test";
import { scoreFunding } from "../../src/signals/funding-spread";

describe("scoreFunding", () => {
  it("zero spread → score 0", () => {
    const result = scoreFunding(0.0001, [0.0001, 0.0001]);
    expect(result.score).toBe(0);
    expect(result.spread8h).toBe(0);
  });

  it("0.001875 hourly rate (0.15%/8h) vs 0 CEX → score 100", () => {
    // 0.001875 * 100 * 8 = 1.5
    // cex: 0 * 100 * 8 = 0
    // spread = 1.5, abs(1.5)/0.15*100 = 1000 → min(100, 1000) = 100
    const result = scoreFunding(0.001875, [0]);
    expect(result.score).toBe(100);
    expect(result.lighter8h).toBeCloseTo(1.5, 5);
    expect(result.cexAvg8h).toBe(0);
    expect(result.spread8h).toBeCloseTo(1.5, 5);
  });

  it("mixed CEX rates average correctly", () => {
    // lighterRate = 0.0001 → lighter8h = 0.0001 * 100 * 8 = 0.08
    // cexRates = [0.00005, 0.0001, 0.00015] → avg = 0.0001 → cexAvg8h = 0.08
    const result = scoreFunding(0.0001, [0.00005, 0.0001, 0.00015]);
    expect(result.score).toBe(0);
    expect(result.cexAvg8h).toBeCloseTo(0.08, 5);
  });

  it("empty cexRates → cexAvg8h = 0", () => {
    const result = scoreFunding(0.001, []);
    expect(result.cexAvg8h).toBe(0);
    expect(result.lighter8h).toBeCloseTo(0.8, 5);
    // spread = 0.8, score = min(100, 0.8/0.15*100) = min(100, 533.33) = 100
    expect(result.score).toBe(100);
  });

  it("negative lighter rate → score from abs(spread)", () => {
    // lighterRate = -0.001 → lighter8h = -0.8
    // cexRates = [0.0001] → cexAvg8h = 0.08
    // spread = -0.8 - 0.08 = -0.88, abs = 0.88
    // score = min(100, 0.88/0.15*100) = min(100, 586.67) = 100
    const result = scoreFunding(-0.001, [0.0001]);
    expect(result.score).toBe(100);
    expect(result.spread8h).toBeCloseTo(-0.88, 5);
  });

  it("small spread produces proportional score", () => {
    // lighterRate = 0.0001 → lighter8h = 0.08
    // cexRates = [0] → cexAvg8h = 0
    // spread = 0.08, score = 0.08/0.15*100 = 53.33 → round = 53
    const result = scoreFunding(0.0001, [0]);
    expect(result.score).toBe(53);
    expect(result.spread8h).toBeCloseTo(0.08, 5);
  });

  it("filters non-finite CEX rates", () => {
    const result = scoreFunding(0.001, [NaN, 0.0001, Infinity]);
    // Only 0.0001 is valid → cexAvg8h = 0.0001 * 100 * 8 = 0.08
    expect(result.cexAvg8h).toBeCloseTo(0.08, 5);
  });
});
