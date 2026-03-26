import type { OrderBookDetail, FundingRateRaw } from "../types";
import { BASE_URL, LIGHTER_ACCOUNT_INDEX } from "../config";

export async function fetchBalance(): Promise<number> {
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

export async function fetchOrderBookDetails(): Promise<OrderBookDetail[]> {
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

export async function fetchFundingRates(): Promise<FundingRateRaw[]> {
  const res = await fetch(`${BASE_URL}/api/v1/funding-rates`, {
    headers: { accept: "application/json" },
  });
  if (!res.ok) throw new Error(`funding-rates: ${res.status} ${res.statusText}`);
  const data: { code: number; funding_rates: FundingRateRaw[] } = await res.json();
  if (data.code !== 200) throw new Error(`funding-rates API code ${data.code}`);
  return data.funding_rates;
}
