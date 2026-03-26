/**
 * Determine trade direction from majority vote of:
 *   1. MA direction:  ↑ → long, ↓ → short, ↔ → no vote
 *   2. OB type:       support → long, resistance → short, none → no vote
 *   3. Funding spread: negative → long (longs receive), positive → short (shorts receive)
 *
 * Rules:
 *   - 2+ signals agree → that direction
 *   - 0-1 votes or all 3 disagree → use MA as tiebreaker
 *   - MA also neutral → use funding spread direction if nonzero
 *   - Everything neutral → default "long" (rare fallback)
 */
export function computeDirection(
  maDir: "↑" | "↓" | "↔",
  obType: "support" | "resistance" | "none",
  fundingSpread8h: number,
): "long" | "short" {
  let longVotes = 0;
  let shortVotes = 0;
  let maVote: "long" | "short" | null = null;

  // MA direction
  if (maDir === "↑") { longVotes++; maVote = "long"; }
  else if (maDir === "↓") { shortVotes++; maVote = "short"; }

  // OB type
  if (obType === "support") longVotes++;
  else if (obType === "resistance") shortVotes++;

  // Funding spread: negative = longs receive → go long; positive = shorts receive → go short
  if (fundingSpread8h < 0) longVotes++;
  else if (fundingSpread8h > 0) shortVotes++;

  // Majority vote
  if (longVotes >= 2) return "long";
  if (shortVotes >= 2) return "short";

  // Tiebreaker: MA direction
  if (maVote) return maVote;

  // No clear signal — use funding spread direction if nonzero
  if (fundingSpread8h < 0) return "long";   // negative spread → longs receive → go long
  if (fundingSpread8h > 0) return "short";  // positive spread → shorts receive → go short

  // Everything neutral → rare fallback
  return "long";
}
