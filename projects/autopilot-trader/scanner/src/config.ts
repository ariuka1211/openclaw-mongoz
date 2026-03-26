import type { OiSnapshot } from "./types";

// --- Config ---

export const CONFIG = {
  accountEquity: 60,                // USD — updated to actual account balance
  riskPct: 0.05,                    // 5% of equity risked per trade
  stopLossMultiple: 1.0,            // SL = dailyVolatility × this multiple
  maxPositionUsd: 15,               // Fixed USD cap per position
  maxConcurrentPositions: 3,        // max simultaneous positions (1-5)
  minDailyVolume: 100000,           // $100k minimum daily volume
  minConfidenceScore: 60,          // only show opportunities above this score
};

export const BASE_URL = "https://mainnet.zklighter.elliot.ai";
export const LIGHTER_ACCOUNT_INDEX = "719758";
export const EIGHT_HR_MULTIPLIER = 8;

export const OI_SNAPSHOT_PATH = "../signals/oi-snapshot.json";
export const OI_MAX_AGE_MS = 24 * 60 * 60 * 1000; // ~24 hours

// Markets without OKX equivalents get neutral score (50).
export const OKX_MARKETS = new Set([
  "MET","LTC","FIL","WIF","EIGEN","ROBO","PROVE","PENGU","CRO","TON",
  "ONDO","XRP","ZORA","LINEA","ZEC","JTO","LIT","HBAR","SUI","STRK",
  "IP","TRX","SOL","DYDX","POL","BNB","XPL","LINK","BONK","BERA",
  "AXS","TIA","JUP","ENA","PUMP","BCH","SKY","ADA","ETHFI","CC",
  "APT","DOGE","MORPHO","ZK","WLD","AVNT","OP","BTC","LDO","AVAX",
  "TRUMP","GMX","ASTER","RESOLV","DASH","DOT","ICP","NEAR","CRV","FLOKI",
  "2Z","SHIB","AAVE","SEI","PENDLE","PAXG","PYTH","NMR","WLFI","UNI",
  "ZRO","S","TOSHI","ETH","VIRTUAL","ARB","HYPE","KAITO",
]);

export function getOkxInstId(symbol: string): string | null {
  // Strip 1000-prefix
  let base = symbol;
  if (symbol.startsWith("1000")) base = symbol.slice(4);
  // Manual overrides
  if (base === "XBT") base = "BTC";
  if (OKX_MARKETS.has(base)) return `${base}-USDT`;
  return null; // no OKX equivalent
}
