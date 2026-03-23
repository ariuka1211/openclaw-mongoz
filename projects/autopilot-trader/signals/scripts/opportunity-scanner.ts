/**
 * Lighter.xyz Opportunity Scanner
 *
 * Scans all Lighter perp markets for actionable trading opportunities
 * based on funding rate arbitrage, volume anomalies, and price momentum.
 *
 * Signal breakdown:
 *   A. Funding Rate Arbitrage — Lighter rate vs CEX average (PRIMARY signal)
 *   B. Volume Anomaly         — daily volume vs 30-day baseline estimate
 *   C. Price Momentum          — daily_price_change magnitude
 *
 * Position sizing uses risk-based formula:
 *   positionSizeUsd = (equity × riskPct) / stopLossDistance
 *   stopLossDistance = lastPrice × dailyVolatility × stopLossMultiple
 *
 * Safety rules: liq distance ≥ 2× SL, max leverage cap, NaN guards.
 *
 * Usage: bun run scripts/opportunity-scanner.ts [--equity 1000] [--min-score 60] [--max-positions 3]
 */
import { rename } from "node:fs/promises";

// --- Types ---

interface OrderBookDetail {
  symbol: string;
  market_id: number;
  size_decimals: number;
  price_decimals: number;
  quote_multiplier: number;
  default_initial_margin_fraction: number;  // 500 = 0.5% = 200x
  min_initial_margin_fraction: number;      // 200 = 0.2% = 500x max
  maintenance_margin_fraction: number;      // 120 = 0.12%
  closeout_margin_fraction: number;         // 80 = 0.08%
  last_trade_price: number;
  daily_trades_count: number;
  daily_base_token_volume: number;
  daily_quote_token_volume: number;         // USD volume
  daily_price_low: number;
  daily_price_high: number;
  daily_price_change: number;               // % change
  open_interest: number;
  strategy_index: number;
}

interface FundingRateRaw {
  market_id: number;
  exchange: string;
  symbol: string;
  rate: number;  // hourly rate as decimal
}

interface MarketOpportunity {
  symbol: string;
  marketId: number;
  // Signal scores (0-100)
  fundingSpreadScore: number;
  volumeAnomalyScore: number;
  momentumScore: number;
  maAlignmentScore: number;
  orderBlockScore: number;
  compositeScore: number;
  // Raw values
  lighterFundingRate8h: number;   // % per 8hr
  cexAvgFundingRate8h: number;    // % per 8hr
  fundingSpread8h: number;        // % per 8hr
  dailyVolumeUsd: number;
  dailyPriceChange: number;       // %
  lastPrice: number;
  // MA details
  maDirection: "↑" | "↓" | "↔";  // bullish / bearish / choppy
  ma50: number | null;
  ma99: number | null;
  ma200: number | null;
  // Order Block details
  obType: "support" | "resistance" | "none";
  obDistancePct: number | null;   // % distance to nearest OB
  obPrice: number | null;         // OB price level
  // Direction (long/short) from majority vote of MA + OB + funding spread
  direction: "long" | "short";
  // Risk-based position sizing
  maxLeverage: number;
  positionSizeUsd: number;
  actualLeverage: number;
  riskAmountUsd: number;          // $ risked on this trade
  stopLossDistanceAbs: number;    // absolute $ distance to SL
  stopLossDistancePct: number;    // % distance to SL
  liquidationDistancePct: number; // % from entry to liquidation
  safetyPass: boolean;
  safetyReason: string;
  detectedAt: string;             // ISO timestamp when opportunity was detected
}

// --- Config ---

const CONFIG = {
  accountEquity: 60,                // USD — updated to actual account balance
  riskPct: 0.05,                    // 5% of equity risked per trade
  stopLossMultiple: 1.0,            // SL = dailyVolatility × this multiple
  maxLeverageCap: 20,               // never exceed 20x
  maxConcurrentPositions: 3,        // max simultaneous positions (1-5)
  minDailyVolume: 100000,           // $100k minimum daily volume
  safetyMultiple: 2,               // liquidation must be 2× stop-loss away
  minConfidenceScore: 60,          // only show opportunities above this score
};

const BASE_URL = "https://mainnet.zklighter.elliot.ai";
const LIGHTER_ACCOUNT_INDEX = "719758";
const EIGHT_HR_MULTIPLIER = 8;

// --- OKX Klines (for MA + Order Block signals) ---

type KlineCandle = [string, string, string, string, string, string, ...string[]]; // [ts, o, h, l, c, vol, ...]

// Symbol mapping: Lighter symbol → OKX instId base
// Markets without OKX equivalents get neutral score (50).
const OKX_MARKETS = new Set([
  "MET","LTC","FIL","WIF","EIGEN","ROBO","PROVE","PENGU","CRO","TON",
  "ONDO","XRP","ZORA","LINEA","ZEC","JTO","LIT","HBAR","SUI","STRK",
  "IP","TRX","SOL","DYDX","POL","BNB","XPL","LINK","BONK","BERA",
  "AXS","TIA","JUP","ENA","PUMP","BCH","SKY","ADA","ETHFI","CC",
  "APT","DOGE","MORPHO","ZK","WLD","AVNT","OP","BTC","LDO","AVAX",
  "TRUMP","GMX","ASTER","RESOLV","DASH","DOT","ICP","NEAR","CRV","FLOKI",
  "2Z","SHIB","AAVE","SEI","PENDLE","PAXG","PYTH","NMR","WLFI","UNI",
  "ZRO","S","TOSHI","ETH","VIRTUAL","ARB","HYPE","KAITO",
]);

function getOkxInstId(symbol: string): string | null {
  // Strip 1000-prefix
  let base = symbol;
  if (symbol.startsWith("1000")) base = symbol.slice(4);
  // Manual overrides
  if (base === "XBT") base = "BTC";
  if (OKX_MARKETS.has(base)) return `${base}-USDT`;
  return null; // no OKX equivalent
}

// Cache: instId → parsed OHLC data
interface KlineData {
  opens: number[];
  highs: number[];
  lows: number[];
  closes: number[];
}
const klinesCache = new Map<string, KlineData>();

async function fetchOkxKlines(instId: string): Promise<KlineData | null> {
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

// --- MA Scoring ---

interface MaResult {
  score: number;
  direction: "↑" | "↓" | "↔";
  ma50: number | null;
  ma99: number | null;
  ma200: number | null;
}

function computeMA(closes: number[], period: number): number | null {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  const sum = slice.reduce((s, v) => s + v, 0);
  const avg = sum / period;
  return Number.isFinite(avg) ? avg : null;
}

function scoreMA(closes: number[], currentPrice: number): MaResult {
  if (currentPrice <= 0 || !Number.isFinite(currentPrice)) {
    return { score: 50, direction: "↔", ma50: null, ma99: null, ma200: null };
  }
  if (closes.length < 200) return { score: 50, direction: "↔", ma50: null, ma99: null, ma200: null };

  const ma50 = computeMA(closes, 50);
  const ma99 = computeMA(closes, 99);
  const ma200 = computeMA(closes, 200);

  if (ma50 === null || ma99 === null || ma200 === null) {
    return { score: 50, direction: "↔", ma50, ma99, ma200 };
  }

  const bullAlignment = currentPrice > ma50 && ma50 > ma99 && ma99 > ma200;
  const bearAlignment = currentPrice < ma50 && ma50 < ma99 && ma99 < ma200;

  if (bullAlignment) {
    // Score based on how clean: measure spread as % of price
    const spread = (currentPrice - ma200) / currentPrice;
    const score = Math.min(100, Math.round(80 + spread * 200));
    return { score, direction: "↑", ma50, ma99, ma200 };
  }

  if (bearAlignment) {
    const spread = (ma200 - currentPrice) / currentPrice;
    const score = Math.min(100, Math.round(80 + spread * 200));
    return { score, direction: "↓", ma50, ma99, ma200 };
  }

  // Choppy: price between MAs
  return { score: 30, direction: "↔", ma50, ma99, ma200 };
}

// --- Order Block Scoring ---

interface ObResult {
  score: number;
  obType: "support" | "resistance" | "none";
  distancePct: number | null;
  obPrice: number | null;
}

interface ObLevel {
  type: "bullish" | "bearish";
  price: number; // low for bullish OB, high for bearish OB
}

function detectOrderBlocks(closes: number[], highs: number[], lows: number[], opens: number[]): { bullish: ObLevel | null; bearish: ObLevel | null } {
  const lookback = Math.min(100, closes.length);
  if (lookback < 10) return { bullish: null, bearish: null };

  let bullishOb: ObLevel | null = null;
  let bearishOb: ObLevel | null = null;

  // Scan from oldest to newest within lookback window
  for (let i = closes.length - lookback; i < closes.length - 4; i++) {
    // Bullish OB: last down candle before impulse up
    if (closes[i] < opens[i]) { // down candle
      // Check for impulse up after
      let bullishCount = 0;
      let movePct = 0;
      for (let j = i + 1; j < Math.min(i + 6, closes.length); j++) {
        if (closes[j] > opens[j]) bullishCount++;
        movePct = (closes[j] - closes[i]) / closes[i];
        if (bullishCount >= 3 || movePct > 0.02) {
          bullishOb = { type: "bullish", price: lows[i] };
          break;
        }
      }
    }

    // Bearish OB: last up candle before impulse down
    if (closes[i] > opens[i]) { // up candle
      let bearishCount = 0;
      let movePct = 0;
      for (let j = i + 1; j < Math.min(i + 6, closes.length); j++) {
        if (closes[j] < opens[j]) bearishCount++;
        movePct = (closes[i] - closes[j]) / closes[i];
        if (bearishCount >= 3 || movePct > 0.02) {
          bearishOb = { type: "bearish", price: highs[i] };
          break;
        }
      }
    }
  }

  return { bullish: bullishOb, bearish: bearishOb };
}

function scoreOrderBlock(closes: number[], highs: number[], lows: number[], opens: number[], currentPrice: number): ObResult {
  const { bullish, bearish } = detectOrderBlocks(closes, highs, lows, opens);

  if (!bullish && !bearish) return { score: 50, obType: "none", distancePct: null, obPrice: null };

  // Find nearest OB and compute score
  let bestOb: ObLevel | null = null;
  let bestDistPct = Infinity;

  if (bullish) {
    const dist = Math.abs(currentPrice - bullish.price) / currentPrice;
    if (dist < bestDistPct) { bestDistPct = dist; bestOb = bullish; }
  }
  if (bearish) {
    const dist = Math.abs(currentPrice - bearish.price) / currentPrice;
    if (dist < bestDistPct) { bestDistPct = dist; bestOb = bearish; }
  }

  if (!bestOb) return { score: 50, obType: "none", distancePct: null, obPrice: null };

  const distPct = (bestDistPct * 100);

  if (bestOb.type === "bullish") {
    // Price near bullish OB (support) → high score (70-100)
    if (distPct <= 1) {
      const score = Math.round(100 - (distPct / 1) * 30); // closer = higher
      return { score, obType: "support", distancePct: distPct, obPrice: bestOb.price };
    }
    return { score: 50, obType: "support", distancePct: distPct, obPrice: bestOb.price };
  } else {
    // Price near bearish OB (resistance) → low score (20-30)
    if (distPct <= 1) {
      const score = Math.round(20 + (distPct / 1) * 10);
      return { score, obType: "resistance", distancePct: distPct, obPrice: bestOb.price };
    }
    return { score: 50, obType: "resistance", distancePct: distPct, obPrice: bestOb.price };
  }
}

// --- Helpers ---

function fmtPct(pct: number, decimals = 3): string {
  const s = pct.toFixed(decimals);
  return pct >= 0 ? `+${s}%` : `${s}%`;
}

function fmtUsd(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function pad(s: string, len: number): string {
  return s.length >= len ? s : s + " ".repeat(len - s.length);
}

function padL(s: string, len: number): string {
  return s.length >= len ? s : " ".repeat(len - s.length) + s;
}

// --- API Fetchers ---

async function fetchBalance(): Promise<number> {
  try {
    const res = await fetch(`${BASE_URL}/api/v1/account?by=index&value=${LIGHTER_ACCOUNT_INDEX}`, {
      headers: { accept: "application/json" },
    });
    if (!res.ok) throw new Error(`account: ${res.status}`);
    const data = await res.json();
    if (data.accounts?.[0]?.collateral) {
      return parseFloat(data.accounts[0].collateral);
    }
  } catch (e) {
    console.error("⚠️ Failed to fetch balance, using fallback:", e);
  }
  return 0;
}

async function fetchOrderBookDetails(): Promise<OrderBookDetail[]> {
  const res = await fetch(`${BASE_URL}/api/v1/orderBookDetails`, {
    headers: { accept: "application/json" },
  });
  if (!res.ok) throw new Error(`orderBookDetails: ${res.status} ${res.statusText}`);
  const data = await res.json();
  // API returns { code: 200, order_book_details: [...] }
  if (data.order_book_details) return data.order_book_details;
  if (Array.isArray(data)) return data;
  throw new Error("Unexpected orderBookDetails response shape");
}

async function fetchFundingRates(): Promise<FundingRateRaw[]> {
  const res = await fetch(`${BASE_URL}/api/v1/funding-rates`, {
    headers: { accept: "application/json" },
  });
  if (!res.ok) throw new Error(`funding-rates: ${res.status} ${res.statusText}`);
  const data: { code: number; funding_rates: FundingRateRaw[] } = await res.json();
  if (data.code !== 200) throw new Error(`funding-rates API code ${data.code}`);
  return data.funding_rates;
}

// --- Scoring ---

/**
 * A. Funding Rate Arbitrage Score (0-100)
 *
 * Compares Lighter's hourly funding rate against the average of CEX rates
 * (Binance, Bybit, Hyperliquid) for the same market.
 *
 * The funding SPREAD is what you arbitrage:
 *   - If Lighter longs pay 0.05%/8h and Binance charges 0.01%/8h, spread = 0.04%
 *   - Positive spread = Lighter longs overpay → short on Lighter, long on CEX
 *   - Negative spread = Lighter longs underpay → long on Lighter, short on CEX
 *
 * Bigger absolute spread = higher score.
 * Score range: 0 = no spread, 100 = extreme spread (≥0.15%/8hr).
 */
function scoreFunding(
  lighterRate: number,    // hourly decimal
  cexRates: number[]      // hourly decimals from other exchanges
): { score: number; lighter8h: number; cexAvg8h: number; spread8h: number } {
  const lighter8h = lighterRate * 100 * EIGHT_HR_MULTIPLIER;  // % per 8hr
  const validCexRates = cexRates.filter(r => Number.isFinite(r));
  let cexAvg8h = 0;

  if (validCexRates.length > 0) {
    const avgHourly = validCexRates.reduce((s, r) => s + r, 0) / validCexRates.length;
    cexAvg8h = avgHourly * 100 * EIGHT_HR_MULTIPLIER;
  }

  const spread8h = lighter8h - cexAvg8h;
  const absSpread = Math.abs(spread8h);

  // Score: 0.15%/8hr spread = 100 points, linear below that
  const score = Math.min(100, (absSpread / 0.15) * 100);

  return { score: Math.round(score), lighter8h, cexAvg8h, spread8h };
}

/**
 * B. Volume Anomaly Score (0-100)
 *
 * Compares today's daily_quote_token_volume against a 30-day baseline estimate.
 * Without historical data we use heuristics:
 *   - Markets with >$10M daily volume get a base score (they're active)
 *   - Markets with unusual volume relative to typical market caps score higher
 *
 * We estimate a "normal" volume for a market from its open_interest × price,
 * then compare actual volume to this proxy. If actual >> estimated, it's anomalous.
 *
 * Score: 0 = normal volume, 100 = volume 10× or more above baseline.
 */
function scoreVolumeAnomaly(market: OrderBookDetail): number {
  const vol = market.daily_quote_token_volume;
  if (vol < 100_000) return 0;

  // Proxy baseline: open_interest_value × average daily turnover multiplier
  // Typical perp markets have 5-20× daily volume vs open interest value
  const oiValue = market.open_interest * market.last_trade_price;
  const baselineVol = oiValue * 10;  // assume 10× turnover is "normal"

  if (baselineVol <= 0) {
    // No OI baseline — just score by absolute volume tiers
    if (vol > 100_000_000) return 80;  // >$100M = very active
    if (vol > 10_000_000) return 60;   // >$10M = active
    if (vol > 1_000_000) return 40;    // >$1M = moderate
    return 20;
  }

  const ratio = vol / baselineVol;
  // ratio > 1 = higher than expected volume = anomaly
  const score = Math.min(100, Math.max(0, ratio * 30));
  return Math.round(score);
}

/**
 * C. Price Momentum Score (0-100)
 *
 * Uses daily_price_change from orderBookDetails.
 *
 * Score by absolute magnitude:
 *   |change| ≥ 15% → 100 (extreme momentum)
 *   |change| ≥ 5%  → 60  (strong momentum)
 *   |change| ≥ 2%  → 30  (moderate)
 *   |change| < 2%  → 10  (quiet)
 *
 * Direction doesn't matter for the score — we're measuring opportunity
 * from volatility, not predicting direction.
 */
function scoreMomentum(dailyPriceChange: number): number {
  const absChange = Math.abs(dailyPriceChange);
  if (absChange >= 15) return 100;
  if (absChange >= 10) return 80;
  if (absChange >= 5) return 60;
  if (absChange >= 3) return 40;
  if (absChange >= 1) return 20;
  return 10;
}

// --- Position Sizing ---

/**
 * Calculate position size using risk-based formula and run safety checks.
 *
 * RISK-BASED SIZING:
 *   riskAmountUsd = accountEquity × riskPct
 *   dailyVolatility = (dailyHigh - dailyLow) / lastPrice
 *   stopLossDistance = dailyVolatility × stopLossMultiple
 *   positionSizeUsd = riskAmountUsd / stopLossDistance
 *
 * Example: $1000 equity, 5% risk ($50), 2% SL distance → position = $50/0.02 = $2500
 *
 * SAFETY RULES:
 *   - Max leverage capped at 20×
 *   - Liquidation distance must be ≥ 2× stop-loss distance
 *   - NaN guards on all inputs
 */
function calculatePosition(market: OrderBookDetail, compositeScore: number): {
  maxLeverage: number;
  positionSizeUsd: number;
  actualLeverage: number;
  riskAmountUsd: number;
  stopLossDistanceAbs: number;
  stopLossDistancePct: number;
  liqDistPct: number;
  pass: boolean;
  reason: string;
} {
  const { accountEquity, riskPct, stopLossMultiple, maxLeverageCap, safetyMultiple } = CONFIG;

  // Validate all required numeric fields are finite and positive
  const requiredFields = [
    market.last_trade_price,
    market.daily_price_high,
    market.daily_price_low,
    market.maintenance_margin_fraction,
    market.default_initial_margin_fraction,
    market.daily_quote_token_volume,
  ];
  if (requiredFields.some(f => !Number.isFinite(f) || f < 0)) {
    return {
      maxLeverage: 0,
      positionSizeUsd: 0,
      actualLeverage: 0,
      riskAmountUsd: 0,
      stopLossDistanceAbs: 0,
      stopLossDistancePct: 0,
      liqDistPct: 0,
      pass: false,
      reason: "Invalid numeric data from API",
    };
  }

  // Max leverage: exchange-allowed capped by our hard limit
  const exchangeMaxLeverage = 10000 / market.default_initial_margin_fraction;
  const maxLeverage = Math.min(maxLeverageCap, exchangeMaxLeverage);

  // --- Risk-based position sizing ---
  const riskAmountUsd = accountEquity * riskPct;  // $ risked per trade
  const dailyVolatility = (market.daily_price_high - market.daily_price_low) / market.last_trade_price;
  const stopLossDistancePct = dailyVolatility * stopLossMultiple * 100;  // as %
  const stopLossDistanceAbs = dailyVolatility * stopLossMultiple * market.last_trade_price;  // in $

  if (stopLossDistancePct <= 0) {
    return {
      maxLeverage, positionSizeUsd: 0, actualLeverage: 0,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct: 0, liqDistPct: 0,
      pass: false, reason: "No stop-loss range data (zero volatility)",
    };
  }

  // Core formula: position = riskAmount / SL_distance
  let positionSizeUsd = riskAmountUsd / (stopLossDistancePct / 100);

  // Hard cap: never exceed maxLeverage × equity
  const maxAllowedPosition = accountEquity * maxLeverage;
  positionSizeUsd = Math.min(positionSizeUsd, maxAllowedPosition);

  // Actual leverage = position / equity
  const actualLeverage = positionSizeUsd / accountEquity;

  if (actualLeverage <= 0) {
    return {
      maxLeverage, positionSizeUsd: 0, actualLeverage: 0,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct: 0,
      pass: false, reason: "Zero position",
    };
  }

  // Liquidation distance (how far price must move to liquidate, as % of entry)
  const maintMarginRate = market.maintenance_margin_fraction / 10000;
  const liqDistPct = (1 / actualLeverage - maintMarginRate) * 100;

  // SAFETY CHECK: liquidation must be ≥ 2× stop-loss distance
  if (liqDistPct < stopLossDistancePct * safetyMultiple) {
    return {
      maxLeverage, positionSizeUsd, actualLeverage,
      riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct,
      pass: false,
      reason: `Liq dist (${liqDistPct.toFixed(2)}%) < ${safetyMultiple}× SL dist (${(stopLossDistancePct * safetyMultiple).toFixed(2)}%)`,
    };
  }

  return {
    maxLeverage, positionSizeUsd, actualLeverage,
    riskAmountUsd, stopLossDistanceAbs, stopLossDistancePct, liqDistPct,
    pass: true, reason: "PASS",
  };
}

// --- Direction Logic (majority vote of 3 signals) ---

/**
 * Determine trade direction from majority vote of:
 *   1. MA direction:  ↑ → long, ↓ → short, ↔ → no vote
 *   2. OB type:       support → long, resistance → short, none → no vote
 *   3. Funding spread: negative → long (longs receive), positive → short (shorts receive)
 *
 * Rules:
 *   - 2+ signals agree → that direction
 *   - 0-1 votes or all 3 disagree → use MA as tiebreaker
 *   - MA also neutral → default "long" (conservative)
 */
function computeDirection(
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

  // Conservative default
  return "long";
}

// --- Main Scanner ---

async function main(): Promise<void> {
  // Parse CLI args for overrides
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--equity" && args[i + 1]) CONFIG.accountEquity = parseFloat(args[++i]);
    if (args[i] === "--min-score" && args[i + 1]) CONFIG.minConfidenceScore = parseFloat(args[++i]);
    if (args[i] === "--max-positions" && args[i + 1]) {
      const val = parseInt(args[++i], 10);
      if (Number.isFinite(val) && val >= 1 && val <= 5) {
        CONFIG.maxConcurrentPositions = val;
      } else {
        console.error("❌ --max-positions must be 1-5");
        process.exit(1);
      }
    }
  }

  if (!Number.isFinite(CONFIG.accountEquity) || CONFIG.accountEquity <= 0) {
    console.error("❌ Invalid account equity:", CONFIG.accountEquity);
    process.exit(1);
  }

  // Fetch actual balance from Lighter API
  const liveBalance = await fetchBalance();
  if (liveBalance > 0) {
    CONFIG.accountEquity = liveBalance;
    console.log(`  Live balance: $${liveBalance.toFixed(2)} (fetched from Lighter API)`);
  } else {
    console.log(`  Using fallback equity: $${CONFIG.accountEquity}`);
  }

  console.log("Fetching Lighter market data...");
  const [markets, fundingRates] = await Promise.all([
    fetchOrderBookDetails(),
    fetchFundingRates(),
  ]);

  console.log(`  Markets: ${markets.length} | Funding rates: ${fundingRates.length}`);

  // Filter liquid markets
  const liquidMarkets = markets.filter(
    (m) => m.daily_quote_token_volume >= CONFIG.minDailyVolume && m.last_trade_price > 0
  );
  console.log(`  Liquid markets (≥$${(CONFIG.minDailyVolume / 1000).toFixed(0)}K/day): ${liquidMarkets.length}`);

  // Index funding rates by market_id
  const lighterRates = new Map<number, FundingRateRaw>();
  const cexRatesByMarket = new Map<number, number[]>();

  for (const fr of fundingRates) {
    if (!Number.isFinite(fr.rate)) continue;  // skip NaN rates
    if (fr.exchange === "lighter") {
      lighterRates.set(fr.market_id, fr);
    } else {
      const arr = cexRatesByMarket.get(fr.market_id) ?? [];
      arr.push(fr.rate);
      cexRatesByMarket.set(fr.market_id, arr);
    }
  }

  // Score and size each market
  // First pass: identify which markets need OKX klines
  const marketsNeedingKlines: { instId: string; symbol: string; index: number }[] = [];
  const scoredMarkets: { market: OrderBookDetail; ltRate: FundingRateRaw; cexRates: number[] }[] = [];

  for (const m of liquidMarkets) {
    const ltRate = lighterRates.get(m.market_id);
    if (!ltRate || !Number.isFinite(ltRate.rate)) continue;
    const cexRates = cexRatesByMarket.get(m.market_id) ?? [];
    scoredMarkets.push({ market: m, ltRate, cexRates });
  }

  // Collect OKX klines with rate limiting (100ms between requests)
  for (const sm of scoredMarkets) {
    const instId = getOkxInstId(sm.market.symbol);
    if (instId && !klinesCache.has(instId)) {
      marketsNeedingKlines.push({ instId, symbol: sm.market.symbol, index: 0 });
    }
  }

  console.log(`  OKX klines to fetch: ${marketsNeedingKlines.length} markets`);
  for (let i = 0; i < marketsNeedingKlines.length; i++) {
    await fetchOkxKlines(marketsNeedingKlines[i].instId);
    if (i < marketsNeedingKlines.length - 1) {
      await Bun.sleep(100); // rate limit: 100ms between requests
    }
  }

  const opportunities: MarketOpportunity[] = [];

  for (const { market: m, ltRate, cexRates } of scoredMarkets) {
    // A. Funding arbitrage
    const funding = scoreFunding(ltRate.rate, cexRates);

    // B. Volume anomaly
    const volScore = scoreVolumeAnomaly(m);

    // C. Momentum
    const momScore = scoreMomentum(m.daily_price_change);

    // D. MA alignment + E. Order Block (from OKX klines)
    let maScore = 50;
    let maDir: "↑" | "↓" | "↔" = "↔";
    let ma50: number | null = null, ma99: number | null = null, ma200: number | null = null;
    let obScore = 50;
    let obType: "support" | "resistance" | "none" = "none";
    let obDistPct: number | null = null;
    let obPrice: number | null = null;

    const instId = getOkxInstId(m.symbol);
    if (instId) {
      const klines = klinesCache.get(instId);
      if (klines && klines.closes.length >= 200) {
        // MA scoring
        const maResult = scoreMA(klines.closes, m.last_trade_price);
        maScore = maResult.score;
        maDir = maResult.direction;
        ma50 = maResult.ma50;
        ma99 = maResult.ma99;
        ma200 = maResult.ma200;

        // OB scoring
        const obResult = scoreOrderBlock(klines.closes, klines.highs, klines.lows, klines.opens, m.last_trade_price);
        obScore = obResult.score;
        obType = obResult.obType;
        obDistPct = obResult.distancePct;
        obPrice = obResult.obPrice;
      }
    }

    // Composite: Funding 35% | Volume 15% | Momentum 15% | MA 20% | OB 15%
    const composite = Math.round(
      funding.score * 0.35 + volScore * 0.15 + momScore * 0.15 + maScore * 0.20 + obScore * 0.15
    );

    // Position sizing + safety check
    const pos = calculatePosition(m, composite);

    // Direction: majority vote of MA + OB + funding spread
    const direction = computeDirection(maDir, obType, funding.spread8h);

    opportunities.push({
      symbol: m.symbol,
      marketId: m.market_id,
      fundingSpreadScore: funding.score,
      volumeAnomalyScore: volScore,
      momentumScore: momScore,
      maAlignmentScore: maScore,
      orderBlockScore: obScore,
      compositeScore: composite,
      lighterFundingRate8h: funding.lighter8h,
      cexAvgFundingRate8h: funding.cexAvg8h,
      fundingSpread8h: funding.spread8h,
      dailyVolumeUsd: m.daily_quote_token_volume,
      dailyPriceChange: m.daily_price_change,
      lastPrice: m.last_trade_price,
      maDirection: maDir,
      ma50, ma99, ma200,
      obType,
      obDistancePct: obDistPct,
      obPrice,
      direction,
      maxLeverage: pos.maxLeverage,
      positionSizeUsd: pos.positionSizeUsd,
      actualLeverage: pos.actualLeverage,
      riskAmountUsd: pos.riskAmountUsd,
      stopLossDistanceAbs: pos.stopLossDistanceAbs,
      stopLossDistancePct: pos.stopLossDistancePct,
      liquidationDistancePct: pos.liqDistPct,
      safetyPass: pos.pass,
      safetyReason: pos.reason,
      detectedAt: new Date().toISOString(),
    });
  }

  // Sort by composite score descending
  opportunities.sort((a, b) => b.compositeScore - a.compositeScore);

  // Filter to minimum score
  const qualified = opportunities.filter((o) => o.compositeScore >= CONFIG.minConfidenceScore);
  const allPassedSafety = qualified.filter((o) => o.safetyPass);
  const failedSafety = qualified.filter((o) => !o.safetyPass);

  // Apply max concurrent positions cap — take only top N
  const passedSafety = allPassedSafety.slice(0, CONFIG.maxConcurrentPositions);

  // --- Output ---

  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
  console.log("  LIGHTER.OPPORTUNITY SCANNER");
  console.log(`  ${new Date().toISOString()}`);
  console.log(`  Equity: $${CONFIG.accountEquity} | Risk/trade: ${(CONFIG.riskPct * 100).toFixed(0)}% ($${(CONFIG.accountEquity * CONFIG.riskPct).toFixed(0)}) | SL multiple: ${CONFIG.stopLossMultiple}× daily vol`);
  console.log(`  Max positions: ${CONFIG.maxConcurrentPositions} | Max leverage: ${CONFIG.maxLeverageCap}× | Min score: ${CONFIG.minConfidenceScore}`);
  console.log(`  Scanned: ${liquidMarkets.length} liquid markets | Qualified: ${qualified.length} | Safety passed: ${allPassedSafety.length} | Selected: ${passedSafety.length}`);
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");

  // --- Opportunities that passed safety (capped by max positions) ---
  if (passedSafety.length > 0) {
    console.log("");
    console.log("  ✅ SELECTED POSITIONS (risk-based sizing)");
    console.log("");

    const COL = {
      sym: 10, dir: 5, score: 6, fund: 10, spread: 10, vol: 8, mom: 6,
      ma: 10, ob: 14,
      risk: 8, slPct: 8, slAbs: 10, posSize: 10, lev: 6, liqD: 8,
    };

    const header =
      pad("SYMBOL", COL.sym) +
      padL("DIR", COL.dir) +
      padL("SCORE", COL.score) +
      padL("FUND8H", COL.fund) +
      padL("SPREAD8H", COL.spread) +
      padL("VOLUME", COL.vol) +
      padL("CHG%", COL.mom) +
      padL("MA", COL.ma) +
      padL("OB", COL.ob) +
      padL("RISK $", COL.risk) +
      padL("SL %", COL.slPct) +
      padL("SL $", COL.slAbs) +
      padL("POS USD", COL.posSize) +
      padL("LEV×", COL.lev) +
      padL("LIQ D", COL.liqD);

    console.log(`  ${header}`);
    console.log(`  ${"─".repeat(header.length)}`);

    for (const o of passedSafety) {
      const maStr = `${o.maDirection}${o.maAlignmentScore}`;
      const obDistStr = o.obDistancePct !== null ? `${o.obDistancePct.toFixed(1)}%` : "—";
      const obStr = o.obType !== "none" ? `${o.obType === "support" ? "S" : "R"} ${obDistStr}` : "none";

      console.log(
        `  ${pad(o.symbol, COL.sym)}` +
        padL(o.direction === "long" ? "L" : "S", COL.dir) +
        padL(String(o.compositeScore), COL.score) +
        padL(fmtPct(o.lighterFundingRate8h, 3), COL.fund) +
        padL(fmtPct(o.fundingSpread8h, 3), COL.spread) +
        padL(fmtUsd(o.dailyVolumeUsd), COL.vol) +
        padL(fmtPct(o.dailyPriceChange, 1), COL.mom) +
        padL(maStr, COL.ma) +
        padL(obStr, COL.ob) +
        padL(fmtUsd(o.riskAmountUsd), COL.risk) +
        padL(`${o.stopLossDistancePct.toFixed(2)}%`, COL.slPct) +
        padL(fmtUsd(o.stopLossDistanceAbs), COL.slAbs) +
        padL(fmtUsd(o.positionSizeUsd), COL.posSize) +
        padL(`${o.actualLeverage.toFixed(1)}×`, COL.lev) +
        padL(`${o.liquidationDistancePct.toFixed(1)}%`, COL.liqD)
      );
    }

    // Total risk exposure
    const totalRiskUsd = passedSafety.reduce((s, o) => s + o.riskAmountUsd, 0);
    const totalRiskPct = (totalRiskUsd / CONFIG.accountEquity) * 100;
    const totalExposureUsd = passedSafety.reduce((s, o) => s + o.positionSizeUsd, 0);
    console.log("");
    console.log(`  📊 Total risk exposure: ${fmtUsd(totalRiskUsd)} (${totalRiskPct.toFixed(1)}% of equity across ${passedSafety.length} positions)`);
    console.log(`  📊 Total position exposure: ${fmtUsd(totalExposureUsd)} (${(totalExposureUsd / CONFIG.accountEquity * 100).toFixed(1)}% of equity)`);
  } else {
    console.log("");
    console.log("  No opportunities passed safety checks.");
  }

  // Show truncated positions if any were cut by max-positions cap
  const truncatedByCap = allPassedSafety.length - passedSafety.length;
  if (truncatedByCap > 0) {
    console.log("");
    console.log(`  ⚠️  ${truncatedByCap} more positions available but capped at ${CONFIG.maxConcurrentPositions} (--max-positions)`);
    for (const o of allPassedSafety.slice(CONFIG.maxConcurrentPositions)) {
      console.log(`     ${o.direction === "long" ? "L" : "S"} ${o.symbol} score=${o.compositeScore} pos=${fmtUsd(o.positionSizeUsd)} lev=${o.actualLeverage.toFixed(1)}×`);
    }
  }

  // --- Opportunities that failed safety ---
  if (failedSafety.length > 0) {
    console.log("");
    console.log("  ❌ QUALIFIED BUT FAILED SAFETY CHECK");
    console.log("");

    for (const o of failedSafety.slice(0, 10)) {
      console.log(
        `  ${o.direction === "long" ? "L" : "S"} ${pad(o.symbol, 10)} score=${o.compositeScore}  ` +
        `liq=${o.liquidationDistancePct.toFixed(1)}%  ` +
        `sl=${o.stopLossDistancePct.toFixed(1)}%  ` +
        `→ ${o.safetyReason}`
      );
    }
    if (failedSafety.length > 10) {
      console.log(`  ... and ${failedSafety.length - 10} more`);
    }
  }

  // --- Summary ---
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");
  console.log("  SUMMARY");
  console.log(`  Total markets scanned:       ${liquidMarkets.length}`);
  console.log(`  Score ≥ ${CONFIG.minConfidenceScore} (qualified):    ${qualified.length}`);
  console.log(`  Passed safety:               ${allPassedSafety.length}`);
  console.log(`  Selected (max ${CONFIG.maxConcurrentPositions}):              ${passedSafety.length}`);
  console.log(`  Failed safety:               ${failedSafety.length}`);
  console.log(`  Below score threshold:       ${opportunities.length - qualified.length}`);

  if (passedSafety.length > 0) {
    const totalRiskUsd = passedSafety.reduce((s, o) => s + o.riskAmountUsd, 0);
    const totalExposure = passedSafety.reduce((s, o) => s + o.positionSizeUsd, 0);
    console.log(`  Risk per trade:              ${fmtUsd(CONFIG.accountEquity * CONFIG.riskPct)} (${(CONFIG.riskPct * 100).toFixed(0)}% of equity)`);
    console.log(`  Total risk (all positions):  ${fmtUsd(totalRiskUsd)} (${(totalRiskUsd / CONFIG.accountEquity * 100).toFixed(1)}% of equity)`);
    console.log(`  Total exposure:              ${fmtUsd(totalExposure)} (${(totalExposure / CONFIG.accountEquity * 100).toFixed(1)}% of equity)`);
  }

  console.log("");
  console.log("  Signal weights: Funding 35% | Volume 15% | Momentum 15% | MA Alignment 20% | Order Block 15%");
  console.log("  Sizing: risk-based (equity × riskPct / SL distance) | Max 20× leverage | Liq ≥ 2× SL");

  // Check for suggested weights from signal analyzer
  try {
    const suggestedPath = "../ai-trader/state/signal_weights_suggested.json";
    const suggestedFile = Bun.file(suggestedPath);
    if (await suggestedFile.exists()) {
      const suggested = await suggestedFile.json();
      if (suggested.suggested_weights_blended && suggested.trades_analyzed >= 5) {
        console.log("");
        console.log(`  💡 SUGGESTED WEIGHTS AVAILABLE (${suggested.trades_analyzed} trades analyzed)`);
        console.log(`     FundingSpread: ${suggested.suggested_weights_blended.fundingSpreadScore}`);
        console.log(`     VolumeAnomaly: ${suggested.suggested_weights_blended.volumeAnomalyScore}`);
        console.log(`     Momentum:      ${suggested.suggested_weights_blended.momentumScore}`);
        console.log(`     MA Alignment:  ${suggested.suggested_weights_blended.maAlignmentScore}`);
        console.log(`     Order Block:   ${suggested.suggested_weights_blended.orderBlockScore}`);
        console.log(`     Review at: ai-trader/state/signal_weights_suggested.json`);
      }
    }
  } catch {
    // File doesn't exist or can't be parsed — that's fine
  }
  console.log("═══════════════════════════════════════════════════════════════════════════════════════════════════════════════");

  // === Signal Cleanup: Remove opportunities older than 20 minutes ===
  const MAX_SIGNAL_AGE_MS = 20 * 60 * 1000; // 20 minutes
  const cleanupNow = new Date();
  const freshOpportunities = opportunities.filter(o => {
    if (!o.detectedAt) return true; // Keep if no timestamp (legacy)
    const age = cleanupNow.getTime() - new Date(o.detectedAt).getTime();
    return age <= MAX_SIGNAL_AGE_MS;
  });
  const removedCount = opportunities.length - freshOpportunities.length;
  if (removedCount > 0) {
    console.log(`  🗑️ Cleanup: Removed ${removedCount} stale opportunities (>20min old)`);
  }

  // Write signals.json with fresh opportunities only
  const signalsOutput = {
    timestamp: new Date().toISOString(),
    config: { ...CONFIG },
    opportunities: freshOpportunities.map(o => ({
      symbol: o.symbol,
      marketId: o.marketId,
      compositeScore: o.compositeScore,
      fundingSpreadScore: o.fundingSpreadScore,
      volumeAnomalyScore: o.volumeAnomalyScore,
      momentumScore: o.momentumScore,
      maAlignmentScore: o.maAlignmentScore,
      orderBlockScore: o.orderBlockScore,
      lighterFundingRate8h: o.lighterFundingRate8h,
      cexAvgFundingRate8h: o.cexAvgFundingRate8h,
      fundingSpread8h: o.fundingSpread8h,
      dailyVolumeUsd: o.dailyVolumeUsd,
      dailyPriceChange: o.dailyPriceChange,
      lastPrice: o.lastPrice,
      maDirection: o.maDirection,
      ma50: o.ma50,
      ma99: o.ma99,
      ma200: o.ma200,
      obType: o.obType,
      obDistancePct: o.obDistancePct,
      obPrice: o.obPrice,
      direction: o.direction,
      positionSizeUsd: o.positionSizeUsd,
      actualLeverage: o.actualLeverage,
      riskAmountUsd: o.riskAmountUsd,
      stopLossDistancePct: o.stopLossDistancePct,
      liquidationDistancePct: o.liquidationDistancePct,
      safetyPass: o.safetyPass,
      safetyReason: o.safetyReason,
      detectedAt: o.detectedAt,
    })),
  };
  await Bun.write("signals.json.tmp", JSON.stringify(signalsOutput, null, 2));
  await rename("signals.json.tmp", "signals.json");
  console.log("\n  💾 Written: signals.json (atomic write)");
}

main().catch((err) => {
  console.error("❌ Scanner failed:", err.message);
  process.exit(1);
});
