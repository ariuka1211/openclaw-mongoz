import type { OrderBookDetail, FundingRateRaw, KlineData } from "../../src/types";

export function mockOrderBookDetail(overrides: Partial<OrderBookDetail> = {}): OrderBookDetail {
  return {
    symbol: "TEST",
    market_id: 1,
    size_decimals: 4,
    price_decimals: 2,
    quote_multiplier: 1,
    default_initial_margin_fraction: 500,
    min_initial_margin_fraction: 200,
    maintenance_margin_fraction: 120,
    closeout_margin_fraction: 80,
    last_trade_price: 100,
    daily_trades_count: 1000,
    daily_base_token_volume: 50000,
    daily_quote_token_volume: 5000000,
    daily_price_low: 95,
    daily_price_high: 105,
    daily_price_change: 5,
    open_interest: 100000,
    strategy_index: 0,
    ...overrides,
  };
}

export function mockFundingRates(marketId: number): FundingRateRaw[] {
  return [
    { market_id: marketId, exchange: "lighter", symbol: "TEST", rate: 0.0001 },
    { market_id: marketId, exchange: "binance", symbol: "TEST", rate: 0.00005 },
    { market_id: marketId, exchange: "bybit", symbol: "TEST", rate: 0.00006 },
    { market_id: marketId, exchange: "hyperliquid", symbol: "TEST", rate: 0.00004 },
  ];
}

export function mockKlineData(count: number = 210, pattern: "bull" | "bear" | "flat" = "flat"): KlineData {
  const opens: number[] = [];
  const highs: number[] = [];
  const lows: number[] = [];
  const closes: number[] = [];

  let price = 100;
  for (let i = 0; i < count; i++) {
    let change: number;
    if (pattern === "bull") {
      change = 0.2 + Math.random() * 0.3; // slight upward bias
    } else if (pattern === "bear") {
      change = -(0.2 + Math.random() * 0.3); // slight downward bias
    } else {
      change = (Math.random() - 0.5) * 0.5; // flat with noise
    }
    const open = price;
    const close = price + change;
    const high = Math.max(open, close) + Math.random() * 0.2;
    const low = Math.min(open, close) - Math.random() * 0.2;
    opens.push(open);
    highs.push(high);
    lows.push(low);
    closes.push(close);
    price = close;
  }
  return { opens, highs, lows, closes };
}

export const mockMarketList: OrderBookDetail[] = [
  mockOrderBookDetail({ symbol: "BTC", market_id: 1, daily_quote_token_volume: 50000000, last_trade_price: 85000, daily_price_low: 83000, daily_price_high: 87000, daily_price_change: 2, open_interest: 500000 }),
  mockOrderBookDetail({ symbol: "ETH", market_id: 2, daily_quote_token_volume: 30000000, last_trade_price: 2000, daily_price_low: 1950, daily_price_high: 2100, daily_price_change: 3, open_interest: 300000 }),
  mockOrderBookDetail({ symbol: "SOL", market_id: 3, daily_quote_token_volume: 15000000, last_trade_price: 150, daily_price_low: 140, daily_price_high: 160, daily_price_change: 4, open_interest: 200000 }),
];
