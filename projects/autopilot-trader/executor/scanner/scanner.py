#!/usr/bin/env python3
"""
Opportunity Scanner — 4-stage funnel for crypto perp opportunities.
Zero LLM tokens. Score 0-400.

Stages:
  1. Volume Surge (0-100)    — unusual volume vs baseline
  2. OI Flow (0-100)         — open interest direction vs price direction + liq cascade
  3. Timeframe Confluence (0-100) — trend alignment across 1h + 4h + OI change
  4. Funding Extreme (0-100) — contrarian signal from funding rates
  [DISABLED] 5. Sentiment (0-100) — Fear & Greed + CoinGecko trending + momentum vs BTC

Data source: Coinalyze API (free tier: 40 req/min). Batches of 20 symbols.
"""

import requests
import time
import sys
import json
import yaml
from pathlib import Path
from dataclasses import dataclass, field

# ── Config ──────────────────────────────────────────────────────────────
COINALYZE_API_KEY = "0b84b892-eec9-4629-b55c-54adc3639ae9"
COINALYZE_BASE = "https://api.coinalyze.net/v1"
RATE_LIMIT_DELAY = 1.6  # seconds between API calls (40/min budget)
RATE_LIMIT_PER_MIN = 40

# Load Telegram config
_cfg_path = Path(__file__).parent.parent / "config.yml"
if _cfg_path.exists():
    with open(_cfg_path) as f:
        _cfg = yaml.safe_load(f) or {}
    TG_TOKEN = _cfg.get("telegram_token", "")
    TG_CHAT_ID = str(_cfg.get("telegram_chat_id", ""))
else:
    TG_TOKEN = ""
    TG_CHAT_ID = ""

# Top coins to scan (aggregated perp symbols on Coinalyze)
# Filtered to crypto perps listed on Lighter.xyz with good liquidity.
# Batches of 20 per API call. 50 coins ≈ 15 calls per data type.
COINS = [
    # ── Tier 1: High Volume ($1M+ daily on Lighter) ──
    "BTCUSDT_PERP.A",       # $980M daily
    "ETHUSDT_PERP.A",       # $994M daily
    "SOLUSDT_PERP.A",       # $109M daily
    "HYPEUSDT_PERP.A",      # $122M daily
    "XRPUSDT_PERP.A",       # $3.5M daily
    "BNBUSDT_PERP.A",       # $2.6M daily
    "AAVEUSDT_PERP.A",      # $2.1M daily
    "DOGEUSDT_PERP.A",      # $1.3M daily
    "LINKUSDT_PERP.A",      # $1.1M daily
    # ── Tier 2: Strong Liquidity ($500K–$1M) ──
    "1000PEPEUSDT_PERP.A",  # $1.8M daily
    "SUIUSDT_PERP.A",       # $1.4M daily
    "1000BONKUSDT_PERP.A",  # $1.1M daily
    "TRUMPUSDT_PERP.A",     # $892K daily
    "ENAUSDT_PERP.A",       # $843K daily
    "TAOUSDT_PERP.A",       # $706K daily
    "UNIUSDT_PERP.A",       # $611K daily
    "NEARUSDT_PERP.A",      # $559K daily
    # ── Tier 3: Decent Liquidity ($125K–$500K) ──
    "APTUSDT_PERP.A",       # $406K daily
    "AVAXUSDT_PERP.A",      # $380K daily
    "WLDUSDT_PERP.A",       # $335K daily
    "TRXUSDT_PERP.A",       # $281K daily
    "ARBUSDT_PERP.A",       # $240K daily
    "TONUSDT_PERP.A",       # $237K daily
    "WIFUSDT_PERP.A",       # $232K daily
    "BCHUSDT_PERP.A",       # $216K daily
    "PENGUUSDT_PERP.A",     # $596K daily
    "1000SHIBUSDT_PERP.A",  # $184K daily
    "MORPHOUSDT_PERP.A",    # $183K daily
    "ADAUSDT_PERP.A",       # $175K daily
    "PENDLEUSDT_PERP.A",    # $131K daily
    "OPUSDT_PERP.A",        # $125K daily
    # ── Tier 4: Moderate Liquidity ($50K–$125K) ──
    "FILUSDT_PERP.A",
    "SEIUSDT_PERP.A",
    "JUPUSDT_PERP.A",
    "MKRUSDT_PERP.A",
    "LTCUSDT_PERP.A",
    "DOTUSDT_PERP.A",
    "CRVUSDT_PERP.A",
    "1000FLOKIUSDT_PERP.A",
    "HBARUSDT_PERP.A",
    "ICPUSDT_PERP.A",
    "VETUSDT_PERP.A",
    "DYDXUSDT_PERP.A",
    "RENDERUSDT_PERP.A",
    "FETUSDT_PERP.A",
    "ONDOUSDT_PERP.A",
    "ZECUSDT_PERP.A",
    "LDOUSDT_PERP.A",
    "STXUSDT_PERP.A",
    "THETAUSDT_PERP.A",
    "GMXUSDT_PERP.A",
]

# Note: Some Lighter coins (FARTCOIN, VVV, USELESS, LAUNCHCOIN, AI16Z,
# PIPPIN, GRASS, ASTER, RIVER, ZRO) may not have Coinalyze coverage.
# Scanner skips coins that return no data.

# Scanner thresholds
MIN_SCORE = 150       # minimum composite score to report (0-400 scale)
TOP_N = 10            # max opportunities to show


@dataclass
class ScanResult:
    symbol: str
    price: float
    price_change_pct: float
    volume_score: int
    oi_score: int
    tf_score: int
    funding_score: int
    sentiment_score: int
    composite: int
    label: str = ""
    details: dict = field(default_factory=dict)


class CoinalyzeClient:
    """Thin wrapper around Coinalyze API with rate limiting."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base = COINALYZE_BASE
        self.calls = 0
        self._call_log = []  # timestamps for sliding window rate limiting

    def _throttle(self, cost: int):
        """Proactively throttle to stay under 40 calls/min."""
        now = time.time()
        # Remove calls older than 60s
        self._call_log = [t for t in self._call_log if now - t["time"] < 60]
        
        current_minute_calls = sum(c["cost"] for c in self._call_log)
        
        if current_minute_calls + cost > RATE_LIMIT_PER_MIN:
            # We need to wait until enough older calls drop off
            # Find how long we need to wait
            needed_drops = (current_minute_calls + cost) - RATE_LIMIT_PER_MIN
            dropped = 0
            wait = 0
            for call in self._call_log:
                dropped += call["cost"]
                if dropped >= needed_drops:
                    wait = max(0, 61 - (now - call["time"]))
                    break
                    
            if wait > 0:
                print(f"    ⏳ Throttling {wait:.0f}s for {cost} calls (approaching 40/min limit)...")
                time.sleep(wait)
                now = time.time()
                self._call_log = [t for t in self._call_log if now - t["time"] < 60]
                
        self._call_log.append({"time": now, "cost": cost})

    def _get(self, endpoint: str, params: dict = None) -> list:
        if params is None:
            params = {}
        
        # Calculate cost based on number of symbols
        cost = 1
        if "symbols" in params and params["symbols"]:
            cost = len(params["symbols"].split(","))
            
        params["api_key"] = self.api_key
        self._throttle(cost)
        time.sleep(0.5)  # Small delay between actual HTTP requests
        self.calls += cost
        
        resp = requests.get(f"{self.base}/{endpoint}", params=params, timeout=15)
        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After", 30))
            print(f"  ⏳ Rate limited, waiting {retry:.0f}s...")
            time.sleep(retry + 1)
            # Reset call log since we got rate limited
            self._call_log = []
            return self._get(endpoint, params)
        resp.raise_for_status()
        return resp.json()

    def fetch_ohlcv(self, symbols: list, interval: str, from_ts: int, to_ts: int) -> list:
        """Fetch OHLCV history. Each symbol = 1 API call."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("ohlcv-history", {
                "symbols": ",".join(batch),
                "interval": interval,
                "from": from_ts,
                "to": to_ts
            })
            results.extend(data)
        return results

    def fetch_oi(self, symbols: list) -> list:
        """Fetch current open interest."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("open-interest", {"symbols": ",".join(batch)})
            results.extend(data)
        return results

    def fetch_oi_history(self, symbols: list, interval: str, from_ts: int, to_ts: int) -> list:
        """Fetch OI history (OHLCV format)."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("open-interest-history", {
                "symbols": ",".join(batch),
                "interval": interval,
                "from": from_ts,
                "to": to_ts
            })
            results.extend(data)
        return results

    def fetch_funding(self, symbols: list) -> list:
        """Fetch current funding rates."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("funding-rate", {"symbols": ",".join(batch)})
            results.extend(data)
        return results

    def fetch_predicted_funding(self, symbols: list) -> list:
        """Fetch predicted funding rates."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("predicted-funding-rate", {"symbols": ",".join(batch)})
            results.extend(data)
        return results

    def fetch_liquidations(self, symbols: list, interval: str, from_ts: int, to_ts: int) -> list:
        """Fetch liquidation history."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("liquidation-history", {
                "symbols": ",".join(batch),
                "interval": interval,
                "from": from_ts,
                "to": to_ts
            })
            results.extend(data)
        return results

    def fetch_ls_ratio(self, symbols: list, interval: str, from_ts: int, to_ts: int) -> list:
        """Fetch long/short ratio history."""
        results = []
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            data = self._get("long-short-ratio-history", {
                "symbols": ",".join(batch),
                "interval": interval,
                "from": from_ts,
                "to": to_ts
            })
            results.extend(data)
        return results


class OpportunityScanner:
    """
    4-stage funnel scanner. [Sentiment stage disabled]

    Each stage scores 0-100. Composite = sum (0-400).
    """

    def __init__(self, api_key: str):
        self.client = CoinalyzeClient(api_key)
        self.now = int(time.time())

    # ── Stage 1: Volume Surge ───────────────────────────────────────
    def score_volume(self, ohlcv_1h: list) -> tuple:
        """
        Score 0-100 based on volume surge.

        Compares recent volume (last 3 candles) to baseline (prior 21 candles).
        Also detects volume consistency (sustained surge vs one-off spike).
        """
        if len(ohlcv_1h) < 10:
            return 0, {"reason": "insufficient data"}

        volumes = [c["v"] for c in ohlcv_1h]
        recent = volumes[-3:]        # last 3 hours
        baseline = volumes[-24:-3] if len(volumes) >= 24 else volumes[:-3]

        if not baseline or not recent:
            return 0, {"reason": "no baseline"}

        avg_recent = sum(recent) / len(recent)
        avg_base = sum(baseline) / len(baseline)

        if avg_base == 0:
            return 0, {"reason": "zero baseline volume"}

        ratio = avg_recent / avg_base

        # Score: ratio 1.0x = 0pts, 1.5x = 25, 2.0x = 50, 3.0x = 75, 4.0x+ = 100
        score = min(100, int((ratio - 1.0) * 33))
        score = max(0, score)

        # Bonus: sustained surge (all recent candles above baseline)
        if all(v > avg_base for v in recent):
            score = min(100, score + 15)

        details = {
            "recent_avg_vol": round(avg_recent, 1),
            "baseline_avg_vol": round(avg_base, 1),
            "surge_ratio": round(ratio, 2),
        }
        return score, details

    # ── Stage 2: Smart Money Flow (OI + Liquidations) ──────────────
    def score_oi_flow(self, ohlcv_1h: list, oi_history: list,
                      liq_history: list, ls_ratio_history: list) -> tuple:
        """
        Score 0-100 based on open interest flow + liquidation patterns.

        Rising OI + rising price = bullish conviction (+)
        Rising OI + falling price = bearish conviction (+)
        Falling OI = position unwinding (low conviction)
        Liquidation clustering = cascade setup (+)
        Extreme LS ratio = crowding (+)
        """
        score = 0
        details = {}

        # ── OI direction vs price direction ──
        if oi_history and oi_history[0].get("history") and len(oi_history[0]["history"]) >= 4:
            oi_closes = [c["c"] for c in oi_history[0]["history"][-6:]]
            oi_change = (oi_closes[-1] - oi_closes[0]) / oi_closes[0] if oi_closes[0] > 0 else 0
        elif oi_history and oi_history[0].get("value"):
            oi_change = 0.001  # slight positive signal from having current OI
        else:
            oi_change = 0

        if len(ohlcv_1h) >= 6:
            price_start = ohlcv_1h[-6]["o"]
            price_end = ohlcv_1h[-1]["c"]
            price_change = (price_end - price_start) / price_start if price_start > 0 else 0
        else:
            price_change = 0

        # OI growing = new money entering (conviction signal)
        oi_growing = oi_change > 0.005  # >0.5% OI growth
        oi_strong = oi_change > 0.02    # >2% OI growth

        if oi_growing:
            score += 30
            if oi_strong:
                score += 20

        # OI-price alignment (both moving same direction = conviction)
        if oi_growing and price_change != 0:
            if (oi_change > 0 and price_change > 0) or (oi_change > 0 and price_change < 0):
                score += 15  # money entering with directional price = strong signal

        details["oi_change_pct"] = round(oi_change * 100, 2)
        details["price_change_pct"] = round(price_change * 100, 2)

        # ── Liquidation cascade detection ──
        if liq_history and liq_history[0].get("history"):
            liqs = liq_history[0]["history"][-6:]  # last 6 hours
            total_longs = sum(c["l"] for c in liqs)
            total_shorts = sum(c["s"] for c in liqs)
            total_liq = total_longs + total_shorts

            # High liquidation volume = cascade potential
            if total_liq > 100:  # >$100M liquidated
                score += 20
            elif total_liq > 50:
                score += 10

            # One-sided liquidations = exhaustion near
            if total_longs > 0 and total_shorts > 0:
                liq_ratio = total_longs / total_shorts
                if liq_ratio > 5:   # 5x more long liqs = short squeeze setup
                    score += 5
                elif liq_ratio < 0.2:  # 5x more short liqs = long squeeze setup
                    score += 5

            details["total_liq_longs"] = round(total_longs, 1)
            details["total_liq_shorts"] = round(total_shorts, 1)

        # ── LS ratio crowding ──
        if ls_ratio_history and ls_ratio_history[0].get("history"):
            latest_ls = ls_ratio_history[0]["history"][-1]
            ls_ratio = latest_ls["r"]
            long_pct = latest_ls["l"]

            # Extreme crowding = contrarian opportunity
            if long_pct > 70 or long_pct < 30:
                score += 10
            if long_pct > 80 or long_pct < 20:
                score += 10

            details["ls_ratio"] = round(ls_ratio, 2)
            details["long_pct"] = round(long_pct, 1)

        score = min(100, score)
        return score, details

    # ── Stage 3: Multi-Timeframe Confluence ────────────────────────
    def score_timeframe_confluence(self, ohlcv_1h: list, ohlcv_4h: list,
                                    oi_history: list) -> tuple:
        """
        Score 0-100 based on trend alignment across timeframes.

        Checks: 1h trend, 4h trend, OI direction.
        All aligned = high score. Mixed = low score.
        """
        signals = []

        # 1h trend (last 6 candles)
        if len(ohlcv_1h) >= 6:
            h1_open = ohlcv_1h[-6]["o"]
            h1_close = ohlcv_1h[-1]["c"]
            h1_trend = "up" if h1_close > h1_open else "down"
            h1_strength = abs(h1_close - h1_open) / h1_open
            signals.append(("1h", h1_trend, h1_strength))

        # 4h trend (last 3 candles)
        if len(ohlcv_4h) >= 3:
            h4_open = ohlcv_4h[-3]["o"]
            h4_close = ohlcv_4h[-1]["c"]
            h4_trend = "up" if h4_close > h4_open else "down"
            h4_strength = abs(h4_close - h4_open) / h4_open
            signals.append(("4h", h4_trend, h4_strength))

        # OI direction (from history)
        if oi_history and oi_history[0].get("history") and len(oi_history[0]["history"]) >= 4:
            oi_closes = [c["c"] for c in oi_history[0]["history"][-6:]]
            oi_trend = "up" if oi_closes[-1] > oi_closes[0] else "down"
            oi_strength = abs(oi_closes[-1] - oi_closes[0]) / oi_closes[0] if oi_closes[0] > 0 else 0
            signals.append(("OI", oi_trend, oi_strength))

        if not signals:
            return 0, {"reason": "no data"}

        # Count aligned signals
        up_count = sum(1 for _, trend, _ in signals if trend == "up")
        down_count = sum(1 for _, trend, _ in signals if trend == "down")
        total = len(signals)

        details = {
            "signals": {name: trend for name, trend, _ in signals},
            "alignment": f"{up_count}up/{down_count}down/{total}total"
        }

        # All aligned = strong signal
        if up_count == total or down_count == total:
            # Score based on strength of the weakest signal
            min_strength = min(strength for _, _, strength in signals)
            score = 70 + min(30, int(min_strength * 1000))  # 70-100
        elif up_count >= 2 or down_count >= 2:
            # Majority aligned
            score = 40
        else:
            # Conflicting signals
            score = 10

        return score, details

    # ── Stage 4: Funding Rate Extreme ──────────────────────────────
    def score_funding(self, funding: float, predicted: float = None) -> tuple:
        """
        Score 0-100 based on funding rate extremity.

        Extreme positive (>0.05%): overcrowded longs → short setup
        Extreme negative (<-0.05%): overcrowded shorts → long setup
        Near zero: no signal
        """
        # Normalize: 0.01% = 1 basis point (typical)
        # 0.03% = somewhat elevated, 0.05% = high, 0.1%+ = extreme
        abs_rate = abs(funding)

        if abs_rate >= 0.001:    # 0.1% per 8h = extreme
            score = 100
        elif abs_rate >= 0.0005: # 0.05% = very high
            score = 80
        elif abs_rate >= 0.0003: # 0.03% = elevated
            score = 60
        elif abs_rate >= 0.0001: # 0.01% = slightly elevated
            score = 30
        else:
            score = 10

        # Direction: negative funding = overcrowded shorts = long setup
        direction = "short_setup" if funding > 0 else "long_setup"

        # Predicted rate amplifies signal
        pred_strong = False
        if predicted is not None and abs(predicted) > abs_rate:
            score = min(100, score + 10)
            pred_strong = True

        details = {
            "funding": f"{funding*100:.4f}%",
            "direction": direction,
            "predicted": f"{predicted*100:.4f}%" if predicted is not None else "n/a",
            "pred_stronger": pred_strong,
        }
        return score, details

    # ── Stage 5: Sentiment (free, no API keys) ────────────────────
    def fetch_fear_greed(self) -> int:
        """Fetch Fear & Greed index (0-100). Free, no key."""
        try:
            resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return int(data["data"][0]["value"])
        except Exception:
            return 50  # neutral fallback

    def fetch_trending(self) -> set:
        """Fetch CoinGecko trending coins (top 15). Free, no key."""
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/search/trending",
                timeout=10)
            resp.raise_for_status()
            data = resp.json()
            symbols = set()
            for item in data.get("coins", []):
                s = item.get("item", {}).get("symbol", "").upper()
                if s:
                    symbols.add(s)
            return symbols
        except Exception:
            return set()

    @staticmethod
    def _symbol_to_coingecko(sym: str) -> str:
        """Convert scanner symbol (e.g. 'BTCUSDT') to CoinGecko-style symbol."""
        s = sym.replace("1000", "").replace("USDT", "").upper()
        # Common mappings
        return {"PEPE": "PEPE", "BONK": "BONK", "SHIB": "SHIB",
                "FLOKI": "FLOKI", "DOGE": "DOGE", "BTC": "BTC"}.get(s, s)

    def score_sentiment(self, symbol: str, price_chg_1h: float,
                        btc_chg_1h: float, fng: int, trending: set) -> tuple:
        """
        Score 0-100 based on sentiment signals.

        Components:
          - Fear & Greed bias (0-40): Extreme Fear → contrarian long +40,
            Extreme Greed → contrarian short +40
          - Trending bonus (0-30): Coin in CoinGecko's top 15 trending? +30
          - Momentum vs BTC (0-30): Coin outperforming BTC on 1h?
            Socially-driven moves tend to lead. Uses data we already have.
        """
        score = 0
        details = {}

        # ── Fear & Greed bias (contrarian) ──
        # Extreme Fear (<25) = oversold, long setup. Extreme Greed (>75) = overheated, short setup.
        fng_score = 0
        if fng <= 20:
            fng_score = 40  # extreme fear → strong contrarian long
        elif fng <= 35:
            fng_score = 25  # fear → moderate contrarian long
        elif fng >= 80:
            fng_score = 35  # extreme greed → contrarian short
        elif fng >= 65:
            fng_score = 20  # greed → moderate contrarian short
        else:
            fng_score = 10  # neutral

        score += fng_score
        details["fear_greed"] = fng
        details["fng_score"] = fng_score

        # ── Trending bonus ──
        cg_sym = self._symbol_to_coingecko(symbol)
        is_trending = cg_sym in trending
        trending_score = 30 if is_trending else 0
        score += trending_score
        details["trending"] = is_trending

        # ── Momentum vs BTC (socially-driven moves tend to outperform BTC) ──
        if btc_chg_1h != 0:
            outperform = price_chg_1h - btc_chg_1h
            if outperform > 3:       # 3%+ above BTC = strong social momentum
                mom_score = 30
            elif outperform > 1.5:
                mom_score = 20
            elif outperform > 0:
                mom_score = 10
            elif outperform > -1:
                mom_score = 5
            else:
                mom_score = 0
        else:
            mom_score = 15 if price_chg_1h > 0 else 0  # fallback

        score += mom_score
        details["btc_outperform_pct"] = round(price_chg_1h - btc_chg_1h, 2)
        details["mom_score"] = mom_score

        score = min(100, score)
        return score, details

    # ── Helpers ─────────────────────────────────────────────────────
    @staticmethod
    def label(score: int) -> str:
        if score >= 350:
            return "🔥 EXTREME"
        elif score >= 280:
            return "🔴 HIGH"
        elif score >= 200:
            return "🟡 MODERATE"
        elif score >= 150:
            return "🟢 LOW"
        else:
            return "⚪ WEAK"

    def _index_by_symbol(self, data: list) -> dict:
        """Index API response by symbol for fast lookup."""
        return {d["symbol"]: d for d in data}

    # ── Main Scan ──────────────────────────────────────────────────
    def scan(self, coins: list = None, min_score: int = MIN_SCORE,
             interval_1h: str = "1hour", interval_4h: str = "4hour") -> list:
        """
        Two-tier scan:
          Tier 1: OHLCV + OI + Funding + Sentiment (all coins) → fast filter
          Tier 2: Liquidations + LS ratio (top candidates only) → refine OI score

        Returns list of ScanResult sorted by composite score (desc).
        """
        if coins is None:
            coins = COINS

        self.now = int(time.time())
        from_6h = self.now - 6 * 3600
        from_24h = self.now - 24 * 3600
        from_12h = self.now - 12 * 3600

        n_coins = len(coins)
        print(f"🔍 Scanning {n_coins} coins (two-tier, 4 stages, sentiment disabled)...")
        print(f"   Tier 1: OHLCV + OI + Funding (~{n_coins*3+0} calls)")
        print(f"   Tier 2: Liquidations + LS (top candidates only)")
        print()

        # ── Tier 1: Core data for ALL coins ──
        # Sentiment disabled — skip Fear & Greed + CoinGecko trending
        print("  📊 Fetching OHLCV (1h)...")
        ohlcv_1h = self._index_by_symbol(
            self.client.fetch_ohlcv(coins, interval_1h, from_24h, self.now))

        print("  📊 Fetching OHLCV (4h)...")
        ohlcv_4h = self._index_by_symbol(
            self.client.fetch_ohlcv(coins, interval_4h, from_24h, self.now))

        print("  💰 Fetching funding rates...")
        funding = self._index_by_symbol(self.client.fetch_funding(coins))

        # Skip predicted funding in quick scan (saves ~n_coins calls)
        predicted = {}

        print("  📈 Fetching OI history...")
        oi_hist = self._index_by_symbol(
            self.client.fetch_oi_history(coins, interval_1h, from_12h, self.now))

        print(f"\n  ✅ {self.client.calls} API calls (Tier 1)")

        # ── Initial scoring without liquidations/LS ──
        prelim = []
        for sym in coins:
            h1 = ohlcv_1h.get(sym, {}).get("history", [])
            h4 = ohlcv_4h.get(sym, {}).get("history", [])
            if len(h1) < 2:
                continue

            price = h1[-1]["c"]
            price_chg = ((h1[-1]["c"] - h1[-2]["c"]) / h1[-2]["c"] * 100) if h1[-2]["c"] > 0 else 0

            vol_score, vol_det = self.score_volume(h1)
            # OI score without liq/LS for now (just OI direction)
            oi_score, oi_det = self.score_oi_flow(
                h1, oi_hist.get(sym, {}).get("history", []), [], [])
            tf_score, tf_det = self.score_timeframe_confluence(
                h1, h4, oi_hist.get(sym, {}).get("history", []))
            fr = funding.get(sym, {}).get("value", 0)
            pr = predicted.get(sym, {}).get("value")
            fund_score, fund_det = self.score_funding(fr, pr)

            # Sentiment disabled
            sent_score = 0
            sent_det = {"status": "disabled"}

            prelim.append({
                "sym": sym, "price": price, "price_chg": price_chg,
                "vol_score": vol_score, "vol_det": vol_det,
                "oi_score": oi_score, "oi_det": oi_det,
                "tf_score": tf_score, "tf_det": tf_det,
                "fund_score": fund_score, "fund_det": fund_det,
                "sent_score": sent_score, "sent_det": sent_det,
                "prelim": vol_score + oi_score + tf_score + fund_score + sent_score,
            })

        # Sort and pick top candidates for Tier 2
        prelim.sort(key=lambda x: x["prelim"], reverse=True)
        tier2_coins = [p["sym"] for p in prelim[:8]]  # top 8 get Tier 2

        # ── Tier 2: Liquidations + LS for top candidates ──
        liq = {}
        ls = {}
        if tier2_coins:
            print(f"\n  💥 Fetching liquidations (top {len(tier2_coins)})...")
            liq = self._index_by_symbol(
                self.client.fetch_liquidations(tier2_coins, "1hour", from_6h, self.now))

            print(f"  ⚖️  Fetching L/S ratio (top {len(tier2_coins)})...")
            ls = self._index_by_symbol(
                self.client.fetch_ls_ratio(tier2_coins, "1hour", from_6h, self.now))

        print(f"\n  ✅ {self.client.calls} API calls total\n")

        # ── Final scoring ──
        results = []
        for p in prelim:
            sym = p["sym"]
            # Re-score OI with liquidation/LS data if available
            h1 = ohlcv_1h.get(sym, {}).get("history", [])
            oi_data = oi_hist.get(sym, {}).get("history", [])
            liq_data = liq.get(sym, {}).get("history", []) if sym in liq else []
            ls_data = ls.get(sym, {}).get("history", []) if sym in ls else []

            if liq_data or ls_data:
                oi_score, oi_det = self.score_oi_flow(h1, oi_data, liq_data, ls_data)
            else:
                oi_score, oi_det = p["oi_score"], p["oi_det"]

            composite = (p["vol_score"] + oi_score + p["tf_score"] +
                        p["fund_score"] + p["sent_score"])

            results.append(ScanResult(
                symbol=sym.replace("_PERP.A", ""),
                price=p["price"],
                price_change_pct=round(p["price_chg"], 2),
                volume_score=p["vol_score"],
                oi_score=oi_score,
                tf_score=p["tf_score"],
                funding_score=p["fund_score"],
                sentiment_score=p["sent_score"],
                composite=composite,
                label=self.label(composite),
                details={"volume": p["vol_det"], "oi": oi_det,
                         "tf": p["tf_det"], "funding": p["fund_det"],
                         "sentiment": p["sent_det"]}
            ))

        results.sort(key=lambda r: r.composite, reverse=True)
        return results

    def quick_scan(self, coins: list = None):
        """Run scan and print formatted results."""
        print("=" * 72)
        print("  🎯 OPPORTUNITY SCANNER — 4-Stage Funnel (Coinalyze)")
        print("     [Sentiment stage disabled]")
        print("=" * 72)
        print()

        results = self.scan(coins)

        # ── Opportunities (above threshold) ──
        opps = [r for r in results if r.composite >= MIN_SCORE][:TOP_N]

        if opps:
            print(f"  🚀 TOP OPPORTUNITIES (score ≥ {MIN_SCORE})")
            print("  " + "─" * 78)
            print(f"  {'COIN':<10} {'PRICE':>12} {'1H%':>7} {'SCORE':>6} {'VOL':>4} {'OI':>4} {'TF':>4} {'FR':>4}  LABEL")
            print("  " + "─" * 78)
            for r in opps:
                print(f"  {r.symbol:<10} {r.price:>12,.2f} {r.price_change_pct:>+6.2f}% "
                      f"{r.composite:>5} {r.volume_score:>4} {r.oi_score:>4} "
                      f"{r.tf_score:>4} {r.funding_score:>4}  {r.label}")
            print("  " + "─" * 78)
        else:
            print(f"  ℹ️  No opportunities above {MIN_SCORE} threshold")
            print()

        # ── Volume leaders ──
        by_vol = sorted(results, key=lambda r: r.volume_score, reverse=True)[:5]
        if by_vol and by_vol[0].volume_score > 20:
            print(f"\n  📊 VOLUME LEADERS")
            print("  " + "─" * 40)
            for r in by_vol:
                if r.volume_score > 10:
                    surge = r.details.get("volume", {}).get("surge_ratio", "?")
                    print(f"  {r.symbol:<10} vol_score={r.volume_score:>3}  "
                          f"surge={surge}x  1h={r.price_change_pct:>+.2f}%")

        # ── Funding extremes ──
        by_fund = sorted(results, key=lambda r: r.funding_score, reverse=True)[:5]
        if by_fund and by_fund[0].funding_score > 30:
            print(f"\n  💰 FUNDING EXTREMES")
            print("  " + "─" * 40)
            for r in by_fund:
                if r.funding_score > 20:
                    det = r.details.get("funding", {})
                    print(f"  {r.symbol:<10} fund_score={r.funding_score:>3}  "
                          f"rate={det.get('funding', '?')}  {det.get('direction', '?')}")

        # ── Sentiment highlights ──
        by_sent = sorted(results, key=lambda r: r.sentiment_score, reverse=True)[:5]
        if by_sent and by_sent[0].sentiment_score > 40:
            print(f"\n  🧠 SENTIMENT SIGNALS")
            print("  " + "─" * 40)
            for r in by_sent:
                if r.sentiment_score > 30:
                    det = r.details.get("sentiment", {})
                    fng = det.get("fear_greed", "?")
                    trending = "🔥 trending" if det.get("trending") else ""
                    outperf = det.get("btc_outperform_pct", 0)
                    print(f"  {r.symbol:<10} sent={r.sentiment_score:>3}  "
                          f"F&G={fng}  vsBTC={outperf:+.1f}%  {trending}")

        # ── Summary ──
        print(f"\n  📋 SUMMARY")
        print(f"  Scanned: {len(results)} coins | "
              f"API calls: {self.client.calls} | "
              f"Opportunities: {len(opps)}")
        print()

        return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OSA — Opportunity Scanner")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--alert", action="store_true", help="Send results via Telegram")
    parser.add_argument("--min-score", type=int, default=MIN_SCORE, help="Minimum score to report/alert")
    parser.add_argument("--coins", type=int, help="Number of top coins to scan")
    args = parser.parse_args()

    if args.coins:
        COINS_SCAN = COINS[:args.coins]
    else:
        COINS_SCAN = COINS

    scanner = OpportunityScanner(COINALYZE_API_KEY)
    results = scanner.quick_scan(COINS_SCAN)

    if "--json" in sys.argv:
        out = [{
            "symbol": r.symbol,
            "price": r.price,
            "price_change_pct": r.price_change_pct,
            "composite": r.composite,
            "label": r.label,
            "volume_score": r.volume_score,
            "oi_score": r.oi_score,
            "tf_score": r.tf_score,
            "funding_score": r.funding_score,
            "sentiment_score": r.sentiment_score,
            "details": r.details
        } for r in results]
        print(json.dumps(out, indent=2))

    # Telegram alert
    if args.alert and TG_TOKEN and TG_CHAT_ID:
        opps = [r for r in results if r.composite >= args.min_score]
        lines = [f"🎯 OSA Scan — {len(results)} coins, {scanner.client.calls} calls\n"]

        if opps:
            for r in opps:
                det = r.details.get("funding", {})
                rate = det.get("funding", "?")
                direction = det.get("direction", "")
                snt = r.details.get("sentiment", {})
                fng = snt.get("fear_greed", "?")
                trending = "🔥" if snt.get("trending") else ""
                icon = "🟢" if r.composite < 200 else "🟡" if r.composite < 280 else "🔴"
                lines.append(
                    f"{icon} {r.symbol} — {r.composite} pts — ${r.price:,.2f} ({r.price_change_pct:+.2f}%)\n"
                    f"   FR: {rate} {direction} | TF: {r.tf_score} | SNT: {r.sentiment_score} (F&G={fng}) {trending}"
                )
        else:
            lines.append(f"📊 No opportunities above {args.min_score}")

        # Funding extremes
        by_fund = sorted(results, key=lambda r: r.funding_score, reverse=True)
        extremes = [r for r in by_fund if r.funding_score >= 80]
        if extremes:
            lines.append("")
            lines.append("💰 Funding extremes:")
            for r in extremes[:5]:
                det = r.details.get("funding", {})
                lines.append(f"   {r.symbol}: {det.get('funding', '?')}")

        text = "\n".join(lines)

        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            resp = requests.post(url, json={
                "chat_id": TG_CHAT_ID,
                "text": text,
            }, timeout=10)
            if resp.ok:
                print("  📱 Telegram alert sent")
            else:
                print(f"  ⚠️  Telegram failed: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"  ⚠️  Telegram error: {e}")
