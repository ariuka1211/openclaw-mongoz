"""
BTC Smart Grid — AI Analyst

Fetches BTC OHLCV candles from OKX (free public API).
Calls LLM via OpenRouter to identify swing highs/lows as grid levels.
Returns structured JSON output for use by GridManager.

Run standalone: python3 analyst.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import yaml
from market_intel import gather_all_intel, format_market_intel
from indicators import gather_indicators

# Load .env from workspace root
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

from dotenv import load_dotenv as _load_dotenv

def _load_env():
    """Load .env using python-dotenv."""
    _load_dotenv(dotenv_path=str(_ENV_PATH), override=True)


_load_env()

OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def fetch_candles(timeframe: str, limit: int = 200) -> list[dict]:
    """Fetch BTC-USDT candles from OKX public API.

    Args:
        timeframe: "15m" or "30m" (maps to OKX bar parameter).
        limit: number of candles to fetch (max 300).

    Returns:
        List of dicts in chronological order (oldest first):
        [{"ts": int, "o": float, "h": float, "l": float, "c": float, "v": float}, ...]

    Raises:
        httpx.HTTPStatusError on HTTP errors.
        ValueError on bad API response.
    """
    params = {"instId": "BTC-USDT", "bar": timeframe, "limit": str(limit)}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(OKX_CANDLES_URL, params=params)
        resp.raise_for_status()

    body = resp.json()

    if body.get("code") != "0":
        raise ValueError(f"OKX API error: {body.get('msg', body)}")

    rows = body.get("data", [])
    if not rows:
        raise ValueError("OKX returned empty candle data")

    # Rows are newest-first; reverse for chronological order
    rows.reverse()

    candles = []
    for row in rows:
        candles.append(
            {
                "ts": int(row[0]),
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }
        )

    return candles


def find_swing_points(candles: list[dict], order: int = 3) -> dict:
    """Identify swing highs, swing lows, and OHLCV statistics from candles.

    Uses the 'order' parameter — a point is a local high/low if it is the
    highest/lowest among `order` candles on each side.

    Returns:
        {
            "swing_highs": [{"index": i, "price": float, "ts": int}, ...],
            "swing_lows": [{"index": i, "price": float, "ts": int}, ...],
            "stats": {"high": float, "low": float, "avg_vol": float, ...},
            "current_price": float,
        }
    """
    if len(candles) < 2 * order + 1:
        return {
            "swing_highs": [],
            "swing_lows": [],
            "stats": {},
            "current_price": candles[-1]["c"] if candles else 0,
        }

    swing_highs = []
    swing_lows = []

    for i in range(order, len(candles) - order):
        price = candles[i]["h"]  # use high for swing highs
        is_high = all(candles[i + j]["h"] <= price for j in range(-order, 0)) and \
                  all(candles[i + j]["h"] <= price for j in range(1, order + 1))
        if is_high:
            swing_highs.append({"index": i, "price": round(price, 2), "ts": candles[i]["ts"]})

        price = candles[i]["l"]  # use low for swing lows
        is_low = all(candles[i + j]["l"] >= price for j in range(-order, 0)) and \
                 all(candles[i + j]["l"] >= price for j in range(1, order + 1))
        if is_low:
            swing_lows.append({"index": i, "price": round(price, 2), "ts": candles[i]["ts"]})

    # Stats
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    volumes = [c["v"] for c in candles]

    stats = {
        "highest": round(max(highs), 2),
        "lowest": round(min(lows), 2),
        "current": round(closes[-1], 2),
        "avg_vol": round(sum(volumes) / len(volumes), 2),
        "candle_count": len(candles),
    }

    return {
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "stats": stats,
        "current_price": stats["current"],
    }


def build_prompt(swing_15m: dict, swing_30m: dict, market_intel: str = "", indicators: str = "") -> str:
    """Build the LLM prompt for swing-level analysis using pre-computed swing points.

    Replaces raw CSV candle data with condensed swing point summaries
    to reduce token usage by 70-80%.
    """
    # Format swing points compactly
    highs_15 = swing_15m.get("swing_highs", [])
    lows_15 = swing_15m.get("swing_lows", [])
    highs_30 = swing_30m.get("swing_highs", [])
    lows_30 = swing_30m.get("swing_lows", [])
    stats_15 = swing_15m.get("stats", {})
    stats_30 = swing_30m.get("stats", {})

    # Format: time, price pairs for swing points
    def format_swings(swings: list[dict]) -> str:
        if not swings:
            return "  (no clear swings detected)"
        lines = []
        for s in swings[-15:]:  # cap at 15 most recent
            from datetime import datetime, timezone
            t = datetime.fromtimestamp(s["ts"] / 1000, timezone.utc).strftime("%H:%M")
            lines.append(f"  {t}: ${s['price']:,.0f}")
        return "\n".join(lines[-8:])  # show last 8 max

    price = stats_15.get("current", 0)

    prompt = f"""You are a BTC market structure analyst for a grid trading bot.

Below are pre-computed swing points from BTC 15-minute and 30-minute candles.

{indicators}

{market_intel}

## Market Summary
- Current price: ${price:,.0f}
- 15m range: ${stats_15.get('lowest', 0):,.0f} – ${stats_15.get('highest', 0):,.0f} ({stats_15.get('candle_count', 0)} candles)
- 30m range: ${stats_30.get('lowest', 0):,.0f} – ${stats_30.get('highest', 0):,.0f} ({stats_30.get('candle_count', 0)} candles)

## Swing Highs (resistance candidates)
15m:
{format_swings(highs_15)}
30m:
{format_swings(highs_30)}

## Swing Lows (support candidates)
15m:
{format_swings(lows_15)}
30m:
{format_swings(lows_30)}

Your job: identify the key swing highs and swing lows that price has clearly reversed from. These will be used as grid order levels.

Rules:
- 4 to 8 buy levels (support / swing lows) — below current price
- 4 to 8 sell levels (resistance / swing highs) — above current price
- Only include levels where price visibly reversed or consolidated
- Round levels to nearest $50 for BTC
- Do not invent levels — only use what the data shows
- Consider market intel: high funding + crowded positions = potential liquidation cascades

Return ONLY valid JSON, no commentary:
{{
  "buy_levels": [82400, 81900, 81200],
  "sell_levels": [83500, 84100, 84800],
  "range_low": 82400,
  "range_high": 84800,
  "confidence": "high",
  "note": "brief explanation",
  "pause": false,
  "pause_reason": null
}}

If you cannot identify clear structure (e.g. strongly trending, not enough data):
set "pause": true and explain in "pause_reason".
"""
    return prompt


async def call_llm(prompt: str, cfg: dict) -> dict:
    """Call OpenRouter LLM and parse JSON response.

    Returns:
        Parsed dict from LLM, or a pause=True error dict on failure.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return {
            "pause": True,
            "pause_reason": "OPENROUTER_API_KEY not set in environment",
            "buy_levels": [],
            "sell_levels": [],
            "range_low": 0,
            "range_high": 0,
            "confidence": "none",
            "note": "",
        }

    llm_cfg = cfg.get("llm", {})
    model = llm_cfg.get("model", "anthropic/claude-3.5-sonnet")
    max_tokens = llm_cfg.get("max_tokens", 600)
    temperature = llm_cfg.get("temperature", 0.1)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://openclaw.ai",
        "X-Title": "OpenClaw",
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
    except Exception as e:
        return {
            "pause": True,
            "pause_reason": f"LLM HTTP error: {e}",
            "buy_levels": [],
            "sell_levels": [],
            "range_low": 0,
            "range_high": 0,
            "confidence": "none",
            "note": "",
        }

    try:
        # mimo-v2-pro streams SSE whitespace lines before JSON — strip and find the JSON object
        raw_text = resp.text
        json_start = raw_text.find('{')
        if json_start > 0:
            raw_text = raw_text[json_start:]
        body = json.loads(raw_text)
        
        # mimo-v2-pro uses reasoning field instead of content
        content = body["choices"][0]["message"]["content"]
        if content is None:
            content = body["choices"][0]["message"].get("reasoning", "")
        
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines)
        
        # Try to extract JSON from the reasoning text if it's not already JSON
        if not content.startswith('{'):
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                content = content[json_start:json_end]
        
        result = json.loads(content)
        return result
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raw = ""
        try:
            raw = resp.text[:500]
        except Exception:
            pass
        return {
            "pause": True,
            "pause_reason": f"LLM returned invalid JSON: {e} | raw: {raw}",
            "buy_levels": [],
            "sell_levels": [],
            "range_low": 0,
            "range_high": 0,
            "confidence": "none",
            "note": "",
        }


async def run_analyst(cfg: dict) -> dict:
    """Main analyst pipeline: fetch candles → build prompt → call LLM → validate.

    Returns:
        Dict with buy_levels, sell_levels, range_low, range_high, confidence, note, pause.
    """
    try:
        # Fetch candles from OKX (no auth needed)
        candles_15m = await fetch_candles("15m", limit=200)
        candles_30m = await fetch_candles("30m", limit=200)
        candles_4h = await fetch_candles("4H", limit=48)
    except Exception as e:
        return {
            "pause": True,
            "pause_reason": f"Failed to fetch candles: {e}",
            "buy_levels": [],
            "sell_levels": [],
            "range_low": 0,
            "range_high": 0,
            "confidence": "none",
            "note": "",
        }

    # Fetch market intelligence data
    market_intel_data = await gather_all_intel(cfg)
    market_intel_text = market_intel_data.get("formatted", "")

    # Calculate indicators
    indicators_data = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel_data)
    indicators_text = indicators_data.get("formatted", "")

    # Pre-compute swing points to replace raw CSV (saves 70-80% tokens)
    swing_15m = find_swing_points(candles_15m, order=3)
    swing_30m = find_swing_points(candles_30m, order=2)

    prompt = build_prompt(swing_15m, swing_30m, market_intel_text, indicators_text)
    result = await call_llm(prompt, cfg)

    # Validate output
    if not isinstance(result, dict):
        return {
            "pause": True,
            "pause_reason": "LLM did not return a dict",
            "buy_levels": [],
            "sell_levels": [],
            "range_low": 0,
            "range_high": 0,
            "confidence": "none",
            "note": "",
        }

    # Ensure required fields exist
    for key, default in [
        ("buy_levels", []),
        ("sell_levels", []),
        ("range_low", 0),
        ("range_high", 0),
        ("confidence", ""),
        ("note", ""),
        ("pause", False),
    ]:
        if key not in result:
            result[key] = default

    if "pause_reason" not in result:
        result["pause_reason"] = None

    # Validate levels are lists of numbers
    if not result.get("pause"):
        buy = result.get("buy_levels", [])
        sell = result.get("sell_levels", [])
        if not isinstance(buy, list) or not isinstance(sell, list):
            result["pause"] = True
            result["pause_reason"] = "buy_levels or sell_levels is not a list"
        elif len(buy) < 1 or len(sell) < 1:
            result["pause"] = True
            result["pause_reason"] = "buy_levels or sell_levels is empty"
        elif not all(isinstance(x, (int, float)) for x in buy + sell):
            result["pause"] = True
            result["pause_reason"] = "buy_levels or sell_levels contains non-numeric values"

    return result


async def main():
    config_path = Path(__file__).parent / "config.yml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    result = await run_analyst(cfg)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
