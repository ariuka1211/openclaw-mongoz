import { describe, it, expect } from "bun:test";
import { scoreOiTrend } from "../../src/signals/oi-trend";

describe("scoreOiTrend", () => {
  it("prev undefined → score 50, changePct 0", () => {
    const result = scoreOiTrend(100000, undefined);
    expect(result.score).toBe(50);
    expect(result.changePct).toBe(0);
  });

  it("prev <= 0 → score 50", () => {
    expect(scoreOiTrend(100000, 0).score).toBe(50);
    expect(scoreOiTrend(100000, -100).score).toBe(50);
  });

  it("current <= 0 → score 50", () => {
    expect(scoreOiTrend(0, 100000).score).toBe(50);
    expect(scoreOiTrend(-100, 100000).score).toBe(50);
  });

  it("OI up 15% → score ~87-100", () => {
    // changePct = 15, score = min(100, 80 + (15-10)*1.5) = min(100, 87.5) = 88
    const result = scoreOiTrend(115000, 100000);
    expect(result.changePct).toBeCloseTo(15, 1);
    expect(result.score).toBeGreaterThanOrEqual(87);
    expect(result.score).toBeLessThanOrEqual(100);
  });

  it("OI up 5% → score ~63-70", () => {
    // changePct = 5, score = round(60 + ((5-3)/7)*10) = round(60 + 2.857) = 63
    const result = scoreOiTrend(105000, 100000);
    expect(result.changePct).toBeCloseTo(5, 1);
    expect(result.score).toBeGreaterThanOrEqual(63);
    expect(result.score).toBeLessThanOrEqual(70);
  });

  it("OI flat (0%) → score 50", () => {
    const result = scoreOiTrend(100000, 100000);
    expect(result.score).toBe(50);
    expect(result.changePct).toBe(0);
  });

  it("OI down 5% → score ~36-40", () => {
    // changePct = -5, score = round(30 + ((-5+10)/7)*10) = round(30 + 7.143) = 37
    const result = scoreOiTrend(95000, 100000);
    expect(result.changePct).toBeCloseTo(-5, 1);
    expect(result.score).toBeGreaterThanOrEqual(36);
    expect(result.score).toBeLessThanOrEqual(40);
  });

  it("OI down 15% → score ~10-17", () => {
    // changePct = -15, score = round(max(10, 20 + (-15+10)*0.5)) = round(max(10, 17.5)) = 18
    // Actually: max(10, 20 + (-5)*0.5) = max(10, 17.5) = 17.5 → round = 18
    const result = scoreOiTrend(85000, 100000);
    expect(result.changePct).toBeCloseTo(-15, 1);
    expect(result.score).toBeGreaterThanOrEqual(10);
    expect(result.score).toBeLessThanOrEqual(20);
  });

  it("OI up 20% → capped score", () => {
    // changePct = 20, score = min(100, 80 + (20-10)*1.5) = min(100, 95) = 95
    const result = scoreOiTrend(120000, 100000);
    expect(result.score).toBe(95);
    expect(result.changePct).toBeCloseTo(20, 1);
  });

  it("OI up 50% → capped at 100", () => {
    // changePct = 50, score = min(100, 80 + (50-10)*1.5) = min(100, 140) = 100
    const result = scoreOiTrend(150000, 100000);
    expect(result.score).toBe(100);
  });

  it("OI down 50% → score near 10", () => {
    // changePct = -50, score = max(10, 20 + (-50+10)*0.5) = max(10, 0) = 10
    const result = scoreOiTrend(50000, 100000);
    expect(result.score).toBe(10);
  });
});
