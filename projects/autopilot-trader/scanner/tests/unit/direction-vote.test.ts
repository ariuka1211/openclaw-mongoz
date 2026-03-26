import { describe, it, expect } from "bun:test";
import { computeDirection } from "../../src/direction-vote";

describe("computeDirection", () => {
  it("↑ + support + negative spread → long (3 votes)", () => {
    // MA↑ = 1 long, support = 1 long, negative spread = 1 long → 3 long, 0 short
    expect(computeDirection("↑", "support", -0.05)).toBe("long");
  });

  it("↓ + resistance + positive spread → short (3 votes)", () => {
    expect(computeDirection("↓", "resistance", 0.05)).toBe("short");
  });

  it("↑ + support + positive spread → long (2 votes)", () => {
    // MA↑ = 1 long, support = 1 long, positive spread = 1 short → 2 long, 1 short
    expect(computeDirection("↑", "support", 0.05)).toBe("long");
  });

  it("↓ + resistance + negative spread → short (2 votes)", () => {
    expect(computeDirection("↓", "resistance", -0.05)).toBe("short");
  });

  it("↔ + none + 0 → long (fallback)", () => {
    // No votes, MA neutral → fallback to long
    expect(computeDirection("↔", "none", 0)).toBe("long");
  });

  it("↓ + none + negative spread → tiebreaker by MA → short", () => {
    // MA↓ = 1 short, none = 0, negative = 1 long → 1-1 tie → MA tiebreaker → short
    expect(computeDirection("↓", "none", -0.05)).toBe("short");
  });

  it("↔ + support + 0 → long (tiebreaker: OB → long)", () => {
    // MA↔ = 0, support = 1 long, 0 spread = 0 → 1 long, 0 short → 1 long wins
    expect(computeDirection("↔", "support", 0)).toBe("long");
  });

  it("↔ + none + negative spread → long (funding decides)", () => {
    // All neutral except negative spread → long
    expect(computeDirection("↔", "none", -0.01)).toBe("long");
  });

  it("↔ + none + positive spread → short (funding decides)", () => {
    expect(computeDirection("↔", "none", 0.01)).toBe("short");
  });

  it("↑ + resistance + 0 → 1 long 1 short tie → MA tiebreaker → long", () => {
    // MA↑ = 1 long, resistance = 1 short, 0 spread = 0 → tie → MA → long
    expect(computeDirection("↑", "resistance", 0)).toBe("long");
  });

  it("↓ + support + 0 → 1 short 1 long tie → MA tiebreaker → short", () => {
    expect(computeDirection("↓", "support", 0)).toBe("short");
  });
});
