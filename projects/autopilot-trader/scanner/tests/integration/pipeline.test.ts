import { describe, it, expect, mock, beforeEach, afterEach, beforeAll } from "bun:test";
import { mockOrderBookDetail } from "../fixtures/test-data";
import * as fs from "node:fs";

describe("Full scan pipeline", () => {
  // Ensure ipc directory exists
  const ipcDir = new URL("../../../ipc", import.meta.url).pathname;
  beforeAll(() => {
    if (!fs.existsSync(ipcDir)) {
      fs.mkdirSync(ipcDir, { recursive: true });
    }
  });

  it("runs main() and produces valid signals.json", async () => {
    // Save originals
    const originalFetch = globalThis.fetch;
    let capturedSignals: any = null;

    // --- Mock fetch ---
    const fetchMock = mock((url: string | URL | Request, _init?: RequestInit) => {
      const urlStr = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;

      if (urlStr.includes("/api/v1/account")) {
        return Promise.resolve(new Response(
          JSON.stringify({ accounts: [{ collateral: "10000" }] }),
          { status: 200 }
        ));
      }

      if (urlStr.includes("/api/v1/orderBookDetails")) {
        const markets = [
          mockOrderBookDetail({
            symbol: "BTC", market_id: 1,
            daily_quote_token_volume: 50000000,
            last_trade_price: 85000,
            daily_price_low: 83000,
            daily_price_high: 87000,
            daily_price_change: 2,
            open_interest: 500000,
          }),
          mockOrderBookDetail({
            symbol: "ETH", market_id: 2,
            daily_quote_token_volume: 30000000,
            last_trade_price: 2000,
            daily_price_low: 1900,
            daily_price_high: 2200,
            daily_price_change: 5,
            open_interest: 300000,
          }),
          mockOrderBookDetail({
            symbol: "SOL", market_id: 3,
            daily_quote_token_volume: 15000000,
            last_trade_price: 150,
            daily_price_low: 130,
            daily_price_high: 170,
            daily_price_change: 8,
            open_interest: 200000,
          }),
        ];
        return Promise.resolve(new Response(
          JSON.stringify({ order_book_details: markets }),
          { status: 200 }
        ));
      }

      if (urlStr.includes("/api/v1/funding-rates")) {
        const rates = [
          // Larger spread to get higher funding scores
          { market_id: 1, exchange: "lighter", symbol: "BTC", rate: 0.001 },    // high
          { market_id: 2, exchange: "lighter", symbol: "ETH", rate: 0.0008 },
          { market_id: 3, exchange: "lighter", symbol: "SOL", rate: 0.0009 },
          { market_id: 1, exchange: "binance", symbol: "BTC", rate: 0.00005 },  // low
          { market_id: 1, exchange: "bybit", symbol: "BTC", rate: 0.00006 },
          { market_id: 2, exchange: "binance", symbol: "ETH", rate: 0.00004 },
          { market_id: 3, exchange: "binance", symbol: "SOL", rate: 0.00008 },
        ];
        return Promise.resolve(new Response(
          JSON.stringify({ code: 200, funding_rates: rates }),
          { status: 200 }
        ));
      }

      if (urlStr.includes("okx.com/api/v5/market/candles")) {
        // Generate 210 candles with strong bull pattern
        const candles: string[][] = [];
        let price = 100;
        for (let i = 0; i < 210; i++) {
          const open = price;
          const close = price + 0.5; // strong up
          const high = close + 0.1;
          const low = open - 0.1;
          const ts = Date.now() - (210 - i) * 3600000;
          candles.push([ts.toString(), open.toFixed(2), high.toFixed(2), low.toFixed(2), close.toFixed(2), "1000", "50000", "5000000", "1"]);
          price = close;
        }
        return Promise.resolve(new Response(
          JSON.stringify({ code: "0", data: candles }),
          { status: 200 }
        ));
      }

      return Promise.resolve(new Response("Not Found", { status: 404 }));
    });

    globalThis.fetch = fetchMock as any;

    // --- Mock Bun.write to capture signals.json ---
    const origWrite = Bun.write;
    (Bun as any).write = mock(async (path: any, data: any) => {
      const pathStr = typeof path === "string" ? path : (path as any)?.path || "";
      if (pathStr.includes("signals.json")) {
        if (typeof data === "string") {
          capturedSignals = JSON.parse(data);
        }
      }
      // Call original for non-signals writes
      return origWrite(path, data);
    });

    try {
      // Dynamic import triggers main()
      const mod = await import("../../src/main");
      // Give a moment for async operations
      await new Promise(r => setTimeout(r, 1000));
    } catch (e: any) {
      // main() may throw due to rename mocking - that's expected
    }

    // Restore mocks
    globalThis.fetch = originalFetch;
    (Bun as any).write = origWrite;

    // Reset CONFIG.accountEquity (modified by main() when fetching balance)
    const { CONFIG } = await import("../../src/config");
    CONFIG.accountEquity = 60;

    // --- Verify signals.json was captured ---
    expect(capturedSignals).not.toBeNull();
    expect(capturedSignals).toBeTruthy();

    // Has timestamp as ISO string
    expect(typeof capturedSignals.timestamp).toBe("string");
    expect(capturedSignals.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    // Has config with accountEquity matching mock
    expect(typeof capturedSignals.config).toBe("object");
    expect(capturedSignals.config.accountEquity).toBe(10000);

    // Has opportunities array
    expect(Array.isArray(capturedSignals.opportunities)).toBe(true);
    expect(capturedSignals.opportunities.length).toBeGreaterThan(0);

    // Verify opportunity structure and value ranges
    for (const opp of capturedSignals.opportunities) {
      expect(typeof opp.symbol).toBe("string");
      expect(opp.symbol.length).toBeGreaterThan(0);
      expect(typeof opp.marketId).toBe("number");
      expect(opp.marketId).toBeGreaterThan(0);
      expect(typeof opp.compositeScore).toBe("number");
      expect(opp.compositeScore).toBeGreaterThanOrEqual(0);
      expect(opp.compositeScore).toBeLessThanOrEqual(100);
      expect(["long", "short"]).toContain(opp.direction);
      expect(typeof opp.fundingSpreadScore).toBe("number");
      expect(typeof opp.momentumScore).toBe("number");
      expect(typeof opp.maAlignmentScore).toBe("number");
      expect(typeof opp.orderBlockScore).toBe("number");
      expect(typeof opp.oiTrendScore).toBe("number");
      expect(typeof opp.positionSizeUsd).toBe("number");
      expect(typeof opp.actualLeverage).toBe("number");
      expect(typeof opp.riskAmountUsd).toBe("number");
      expect(typeof opp.safetyPass).toBe("boolean");
      expect(typeof opp.detectedAt).toBe("string");
      // Detected timestamp should be recent ISO
      expect(opp.detectedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }

    // Pipeline made expected API calls
    const callCount = (fetchMock as any).mock?.calls?.length ?? 0;
    expect(callCount).toBeGreaterThanOrEqual(4); // account + orderBookDetails + funding-rates + okx candles

    console.log(`  Pipeline: ${callCount} API calls, signals captured: ${capturedSignals ? "yes" : "no"}`);
  }, 15000);
});
