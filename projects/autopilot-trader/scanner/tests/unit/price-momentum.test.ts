import { describe, it, expect } from "bun:test";
import { scoreMomentum } from "../../src/signals/price-momentum";

describe("scoreMomentum", () => {
  describe("base scoring", () => {
    it("0% change → 10", () => {
      expect(scoreMomentum(0, "↔")).toBe(10);
    });

    it("1% change → 20", () => {
      expect(scoreMomentum(1, "↔")).toBe(20);
      expect(scoreMomentum(-1, "↔")).toBe(20);
    });

    it("5% change → 60", () => {
      expect(scoreMomentum(5, "↔")).toBe(60);
      expect(scoreMomentum(-5, "↔")).toBe(60);
    });

    it("10% change → 80", () => {
      expect(scoreMomentum(10, "↔")).toBe(80);
    });

    it("15%+ change → 100", () => {
      expect(scoreMomentum(15, "↔")).toBe(100);
      expect(scoreMomentum(20, "↔")).toBe(100);
    });

    it("3% change → 40", () => {
      expect(scoreMomentum(3, "↔")).toBe(40);
    });
  });

  describe("direction-aware adjustment", () => {
    it("5% up with ↑ MA → min(100, 60*1.3) = 78", () => {
      expect(scoreMomentum(5, "↑")).toBe(78);
    });

    it("5% up with ↓ MA → max(1, 60*0.5) = 30", () => {
      expect(scoreMomentum(5, "↓")).toBe(30);
    });

    it("5% down with ↓ MA → aligned, boost", () => {
      // base = 60, aligned (both down), 60*1.3 = 78
      expect(scoreMomentum(-5, "↓")).toBe(78);
    });

    it("5% down with ↑ MA → anti-aligned, penalize", () => {
      // base = 60, anti-aligned, 60*0.5 = 30
      expect(scoreMomentum(-5, "↑")).toBe(30);
    });

    it("↔ MA → base score unchanged", () => {
      expect(scoreMomentum(0, "↔")).toBe(10);
      expect(scoreMomentum(1, "↔")).toBe(20);
      expect(scoreMomentum(5, "↔")).toBe(60);
      expect(scoreMomentum(15, "↔")).toBe(100);
    });

    it("score capped at 100 when boosted", () => {
      // 15% change with aligned MA: base = 100, 100*1.3 = 130 → capped at 100
      expect(scoreMomentum(15, "↑")).toBe(100);
    });

    it("score floored at 1 when penalized", () => {
      // 0.5% change: base = 10, anti-aligned: 10*0.5 = 5
      expect(scoreMomentum(0.5, "↓")).toBe(5);
    });
  });
});
