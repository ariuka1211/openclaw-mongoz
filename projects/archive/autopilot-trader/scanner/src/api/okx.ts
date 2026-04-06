import type { KlineData } from "../types";

// Cache: instId → parsed OHLC data
export const klinesCache = new Map<string, KlineData>();

export async function fetchOkxKlines(instId: string): Promise<KlineData | null> {
  if (klinesCache.has(instId)) return klinesCache.get(instId)!;

  const MAX_RETRIES = 3;
  const BASE_DELAY_MS = 500;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const url = `https://www.okx.com/api/v5/market/candles?instId=${instId}&bar=1H&limit=210`;
      const res = await fetch(url, { headers: { accept: "application/json" } });

      if (res.status === 429) {
        const delay = BASE_DELAY_MS * Math.pow(2, attempt + 1); // longer wait on 429
        console.error(`[okx] ${instId}: 429 rate limited, retry in ${delay}ms (attempt ${attempt + 1}/${MAX_RETRIES})`);
        await Bun.sleep(delay);
        continue;
      }

      if (!res.ok) {
        if (attempt < MAX_RETRIES - 1) {
          const delay = BASE_DELAY_MS * Math.pow(2, attempt);
          console.error(`[okx] ${instId}: ${res.status}, retry in ${delay}ms (attempt ${attempt + 1}/${MAX_RETRIES})`);
          await Bun.sleep(delay);
          continue;
        }
        return null;
      }

      const data: { code: string; data: string[][] } = await res.json();
      if (data.code !== "0" || !data.data?.length) return null;

      const reversed = [...data.data].reverse();
      const result: KlineData = { opens: [], highs: [], lows: [], closes: [] };
      for (const c of reversed) {
        if (c.length < 6) continue; // skip malformed rows
        const o = parseFloat(c[1]), h = parseFloat(c[2]), l = parseFloat(c[3]), cl = parseFloat(c[4]);
        if (Number.isFinite(o) && Number.isFinite(h) && Number.isFinite(l) && Number.isFinite(cl) && o > 0 && h > 0 && l > 0 && cl > 0) {
          result.opens.push(o); result.highs.push(h); result.lows.push(l); result.closes.push(cl);
        }
      }
      klinesCache.set(instId, result);
      return result;
    } catch (err) {
      if (attempt < MAX_RETRIES - 1) {
        const delay = BASE_DELAY_MS * Math.pow(2, attempt);
        console.error(`[okx] ${instId}: ${err instanceof Error ? err.message : String(err)}, retry in ${delay}ms (attempt ${attempt + 1}/${MAX_RETRIES})`);
        await Bun.sleep(delay);
        continue;
      }
      return null;
    }
  }
  return null;
}
