import type { OiSnapshot } from "../types";
import { OI_SNAPSHOT_PATH } from "../config";

export type { OiSnapshot };

export async function loadOiSnapshot(): Promise<OiSnapshot> {
  try {
    const file = Bun.file(OI_SNAPSHOT_PATH);
    if (await file.exists()) {
      return await file.json();
    }
  } catch { /* ignore */ }
  return {};
}

export async function saveOiSnapshot(snapshot: OiSnapshot): Promise<void> {
  await Bun.write(OI_SNAPSHOT_PATH, JSON.stringify(snapshot, null, 2));
}

/**
 * Score based on OI change percentage over ~24h.
 *
 * OI rising >10%  → 80-100 (conviction building)
 * OI rising 3-10% → 60-70  (steady accumulation)
 * OI flat ±3%     → 50     (neutral)
 * OI falling 3-10%→ 30-40  (unwinding)
 * OI falling >10% → 10-20  (capitulation)
 * No previous data→ 50     (neutral)
 */
export function scoreOiTrend(currentOi: number, prevOi: number | undefined): { score: number; changePct: number } {
  if (prevOi === undefined || prevOi <= 0 || currentOi <= 0) {
    return { score: 50, changePct: 0 };
  }

  const changePct = ((currentOi - prevOi) / prevOi) * 100;

  let score: number;
  if (changePct > 10) {
    // Rising >10%: 80-100, linearly capped
    score = Math.round(Math.min(100, 80 + (changePct - 10) * 1.5));
  } else if (changePct > 3) {
    // Rising 3-10%: 60-70
    score = Math.round(60 + ((changePct - 3) / 7) * 10);
  } else if (changePct >= -3) {
    // Flat ±3%: 50
    score = 50;
  } else if (changePct >= -10) {
    // Falling 3-10%: 30-40
    score = Math.round(30 + ((changePct + 10) / 7) * 10);
  } else {
    // Falling >10%: 10-20
    score = Math.round(Math.max(10, 20 + (changePct + 10) * 0.5));
  }

  return { score, changePct: Math.round(changePct * 100) / 100 };
}
