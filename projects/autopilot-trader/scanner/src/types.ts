// --- Types ---

export interface OrderBookDetail {
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

export interface FundingRateRaw {
  market_id: number;
  exchange: string;
  symbol: string;
  rate: number;  // hourly rate as decimal
}

export interface MarketOpportunity {
  symbol: string;
  marketId: number;
  // Signal scores (0-100)
  fundingSpreadScore: number;
  oiTrendScore: number;
  oiChangePct: number;
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
  positionSizeUsd: number;
  riskAmountUsd: number;          // $ risked on this trade
  stopLossDistanceAbs: number;    // absolute $ distance to SL
  stopLossDistancePct: number;    // % distance to SL
  safetyPass: boolean;
  safetyReason: string;
  detectedAt: string;             // ISO timestamp when opportunity was detected
}

export interface OiSnapshot {
  [symbol: string]: {
    oi: number;
    timestamp: string;  // ISO
  };
}

export interface KlineData {
  opens: number[];
  highs: number[];
  lows: number[];
  closes: number[];
}

export interface MaResult {
  score: number;
  direction: "↑" | "↓" | "↔";
  ma50: number | null;
  ma99: number | null;
  ma200: number | null;
}

export interface ObLevel {
  type: "bullish" | "bearish";
  price: number; // low for bullish OB, high for bearish OB
}

export interface ObResult {
  score: number;
  obType: "support" | "resistance" | "none";
  distancePct: number | null;
  obPrice: number | null;
}
