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
from indicators import gather_indicators, _format_regime, funding_rate_adjustment, calc_atr

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
            # Strength: how many surrounding candles confirm the swing
            strength = 0
            for j in range(-order, 0):
                if candles[i + j]["h"] <= price * 0.999:
                    strength += 1
            for j in range(1, order + 1):
                if candles[i + j]["h"] <= price * 0.999:
                    strength += 1
            swing_highs.append({"index": i, "price": round(price, 2), "ts": candles[i]["ts"], "strength": strength})

        price = candles[i]["l"]  # use low for swing lows
        is_low = all(candles[i + j]["l"] >= price for j in range(-order, 0)) and \
                 all(candles[i + j]["l"] >= price for j in range(1, order + 1))
        if is_low:
            # Strength: how many surrounding candles confirm the swing
            strength = 0
            for j in range(-order, 0):
                if candles[i + j]["l"] >= price * 1.001:
                    strength += 1
            for j in range(1, order + 1):
                if candles[i + j]["l"] >= price * 1.001:
                    strength += 1
            swing_lows.append({"index": i, "price": round(price, 2), "ts": candles[i]["ts"], "strength": strength})

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


def build_prompt(
    swing_15m: dict,
    swing_30m: dict,
    market_intel: str = "",
    indicators: str = "",
    grid_history: str = "",
    account_context: str = "",
    spacing_guidance: str = "",
    regime_label: str = "",
    volume_profile: str = "",
    funding_context: str = "",
    oi_divergence: str = "",
    volume_spike: str = "",
) -> str:
    """Build an enhanced LLM prompt with full context."""
    highs_15 = swing_15m.get("swing_highs", [])
    lows_15 = swing_15m.get("swing_lows", [])
    highs_30 = swing_30m.get("swing_highs", [])
    lows_30 = swing_30m.get("swing_lows", [])
    stats_15 = swing_15m.get("stats", {})
    stats_30 = swing_30m.get("stats", {})

    price = stats_15.get("current", 0)

    # Format swing points with strength (confluence count)
    def format_swings(swings: list[dict], label: str) -> str:
        if not swings:
            return "  (no clear swings detected)"
        lines = []
        for s in swings:
            from datetime import datetime, timezone
            t = datetime.fromtimestamp(s["ts"] / 1000, timezone.utc).strftime("%H:%M")
            strength = "★" * min(s.get("strength", 1), 3)
            lines.append(f"  {t}: ${s['price']:,.0f} {strength}")
        # Show up to 15, most recent
        return "\n".join(lines[-15:])

    # Build the enhanced prompt
    prompt = f"""You are a BTC market structure analyst for a GRID TRADING bot.

IMPORTANT: This is a GRID bot, not a directional trader. It places RESTING limit orders 
above and below current price to profit from sideways volatility. The bot buys at support 
levels and sells at resistance levels, repeatedly.

Your job: identify the best swing highs (sell levels) and swing lows (buy levels) 
for placing limit orders. Think about where price is LIKELY TO REVERSE, not where it's going.

{account_context}

{indicators}

## Market Regime Classification
{regime_label}

{volume_profile}

{funding_context}

## Open Interest Divergence
{oi_divergence}

## Volume Spike Alert
{volume_spike}

{market_intel}

{spacing_guidance}

## Market Summary
- Current price: ${price:,.0f}
- 15m range: ${stats_15.get('lowest', 0):,.0f} – ${stats_15.get('highest', 0):,.0f} ({stats_15.get('candle_count', 0)} candles)
- 30m range: ${stats_30.get('lowest', 0):,.0f} – ${stats_30.get('highest', 0):,.0f} ({stats_30.get('candle_count', 0)} candles)

## Swing Highs (resistance candidates) — price reversed DOWN from these
15m:
{format_swings(highs_15, "15m")}
30m:
{format_swings(highs_30, "30m")}

## Swing Lows (support candidates) — price reversed UP from these
15m:
{format_swings(lows_15, "15m")}
30m:
{format_swings(lows_30, "30m")}

{grid_history}

## Grid Direction Selection

You MUST choose the GRID DIRECTION based on market conditions. This tells the bot
which side of price to place orders on. There are THREE options:

### "long" grid (default — buy low, sell high)
- Buy orders placed BELOW current price (long entries at support)
- Sell orders placed ABOVE current price (profit-taking above)
- Use when: BTC is ranging, consolidating, or mildly bullish
- Classic grid mode — profits from sideways volatility

### "short" grid (mirror of long — sell low, buy high)
- Sell orders placed BELOW current price (short entries on breakdowns)
- Buy orders placed ABOVE current price (covering shorts at profit)
- Use when: BTC is ranging but leaning bearish, or there's a confirmed downtrend
- Profits when price drops — buy covers are cheaper than the initial short

### "pause" (no grid at all)
- Use ONLY in extreme conditions: violent trending with no reversals,
  major news events, or when structure is completely unclear
- This is a last resort — prefer "short" grid over "pause" in a downtrend

## Open Interest Divergence — Critical for Direction Choice

OI divergence tells you WHO is trapped and whether a short grid makes sense:

- **capitulation** (price↓, OI↓): Longs getting liquidated already. RISKY to short here —
  the flush may be ending. Consider "long" grid for a bounce or "pause".
- **new_shorts** (price↓, OI↑): Fresh shorts piling in, trend confirmed. GOOD for "short" grid —
  downside likely continues. Place sell entries at breakdown levels.
- **long_squeeze** (price↑, OI↓): Shorts covering, price rising on liquidations.
  DON'T short — dangerous squeeze. Use "pause" or cautiously use "long" grid.
- **new_longs** (price↑, OI↑): Bullish conviction. Use "long" grid or "pause" if momentum is extreme.

For short grids specifically:
- Target breakdown levels where price failed to hold support
- Look at failed bounce levels as short entry points
- Buy levels (covers) should be at resistance zones where price is likely to stall
- Avoid short grids during capitulation (OI↓ on price drops) — the move may be exhausted

## Rules for level selection by direction:

### For "long" grid:
1. 4-8 buy levels BELOW current price (support zones)
2. 4-8 sell levels ABOVE current price (resistance zones)
3. Place levels at actual swing reversals — NOT evenly spaced

### For "short" grid:
1. 4-8 sell levels BELOW current price (short entries on breakdowns)
2. 4-8 buy levels ABOVE current price (buy covers at resistance/profit zones)
3. Mirror of long grid — think in reverse

### General rules (all directions):
4. Round to nearest $50
5. Min spacing between levels: see guidance above
6. Do NOT invent levels — only use what the data shows
7. High funding rates / crowded longs = risk of liquidation cascades (avoid placing sells too close)
8. Strongly trending with no reversals = "pause"
9. In a downtrend, prefer "short" grid over "pause" if swing structure exists

Return ONLY valid JSON, no markdown, no commentary:
{{
  "direction": "long",
  "buy_levels": [83500, 84100, 84800],
  "sell_levels": [82400, 81900, 81200],
  "range_low": 81200,
  "range_high": 84800,
  "confidence": "high",
  "note": "brief explanation of structure",
  "pause_reason": null
}}

For a short grid, note that sell_levels are BELOW current price and buy_levels are ABOVE.

If you cannot identify clear structure (strongly trending, no reversals):
set "direction": "pause" and explain why in "pause_reason".
Use "pause" sparingly — prefer "short" grid in bearish conditions.
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


async def run_analyst(cfg: dict, equity: float = 0, btc_price: float = 0, grid_manager=None) -> dict:
    """Main analyst pipeline: fetch candles → build prompt → call LLM → validate.

    Returns:
        Dict with buy_levels, sell_levels, range_low, range_high, confidence, note, pause.
    """
    try:
        # Fetch candles from OKX (no auth needed)
        candles_15m = await fetch_candles("15m", limit=200)
        candles_30m = await fetch_candles("30m", limit=200)
        candles_4h = await fetch_candles("4H", limit=48)
        try:
            candles_1d = await fetch_candles("1D", limit=90)
        except Exception:
            candles_1d = []
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
    indicators_data = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel_data, candles_1d)
    indicators_text = indicators_data.get("formatted", "")

    # Pre-compute swing points to replace raw CSV (saves 70-80% tokens)
    swing_15m = find_swing_points(candles_15m, order=3)
    swing_30m = find_swing_points(candles_30m, order=2)

    # Compute ATR for spacing guidance
    atr_value = 0
    atr_pct = 0
    try:
        atr_data = gather_indicators(candles_15m, candles_30m, candles_4h, market_intel_data)
        atr_value = atr_data.get("atr", {}).get("atr", 0)
        if btc_price > 0:
            atr_pct = atr_value / btc_price
    except Exception:
        pass

    min_spacing = max(atr_value * 1.0, btc_price * 0.003) if btc_price > 0 else 0
    min_grid_range = atr_value * 5.0  # Minimum grid span: 5 ATRs
    spacing_guidance = f"""## Spacing Guidance
- Current 15m ATR(14): ${atr_value:,.0f} ({atr_pct:.2%} of price)
- Minimum level spacing: ${min_spacing:,.0f} (1.0x ATR)
- Ideal buy/sell spacing: ${atr_value*1.5:,.0f}–${atr_value*3:,.0f} (1.5-3x ATR)
- Place levels at least ${min_spacing:,.0f} apart to avoid constant fills
- MINIMUM GRID RANGE: ${min_grid_range:,.0f} (5 ATRs) — your buy_levels and sell_levels must span at least ${min_grid_range:,.0f} total range from lowest buy to highest sell
- Wider grid = fewer fills but each fill is more profitable = better in trending markets
"""

    # Prepare account context
    if equity > 0 and btc_price > 0:
        account_context = f"""## Account Context
- Account equity: ${equity:,.2f} USDC
- Max exposure: {cfg['capital'].get('max_exposure_multiplier', 3.0)}x equity = ${equity * cfg['capital'].get('max_exposure_multiplier', 3.0):,.0f}
- Margin reserve: {cfg['capital'].get('margin_reserve_pct', 0.20):.0%} ({equity * cfg['capital'].get('margin_reserve_pct', 0.20):,.0f} USDC always reserved)
- Realized PnL so far: ${0:,.2f}
"""
    else:
        account_context = ""

    # Prepare grid history
    grid_history_text = ""
    if grid_manager:
        state = grid_manager.state
        if state.get("trades"):
            trades = state["trades"][-5:]
            pnl = state.get("realized_pnl", 0)
            fills = state.get("fill_count", 0)
            grid_history_text = f"""## Previous Grid Session Results
- Realized PnL: ${pnl:,.2f}
- Total fills: {fills}
- Completed trades: {len(state.get('trades', []))}
- Recent trades:
"""
            for t in trades:
                emoji = "✅" if t.get("pnl", 0) >= 0 else "❌"
                grid_history_text += f"  {emoji} Buy ${t['buy_price']:,.0f} → Sell ${t['sell_price']:,.0f} | PnL ${t['pnl']:.2f}\n"

    # Volume Profile text
    regime = indicators_data.get("regime", "unknown")
    regime_label = _format_regime(regime)
    volume_profile_text = indicators_data.get("volume_profile", {}).get("formatted", "")

    # OI Divergence text
    oi_div_data = indicators_data.get("oi_divergence", {})
    oi_div_text = oi_div_data.get("formatted", "OI Divergence: neutral (no data)")

    # Volume spike text
    volume_spike_data = indicators_data.get("volume_spike", {})
    volume_spike_text = volume_spike_data.get("formatted", "Volume Spike: no data")

    # Funding rate context
    funding_data = indicators_data.get("funding_adj", {})
    if funding_data:
        current = market_intel_data.get("current", {})
        funding_rate = current.get("funding_rate", 0)
        adj_mult = funding_data.get("adj_multiplier", 1.0)
        warning = funding_data.get("warning")
        
        funding_context = f"""## Funding Rate Context
- Current funding rate: {funding_rate*100:.4f}% per 8h
- Grid size adjustment: {adj_mult:.2f}x
- Implication: {funding_data.get('label', 'Normal funding')}
"""
        if warning:
            funding_context += f"- ⚠️ **{warning}**\n"
        funding_context += "Extreme funding rates increase liquidation squeeze risk. The bot will auto-adjust position sizing.\n"
    else:
        funding_context = ""
    
    prompt = build_prompt(
        swing_15m, swing_30m, market_intel_text, indicators_text,
        grid_history=grid_history_text,
        account_context=account_context,
        spacing_guidance=spacing_guidance,
        regime_label=regime_label,
        volume_profile=volume_profile_text,
        funding_context=funding_context,
        oi_divergence=oi_div_text,
        volume_spike=volume_spike_text,
    )
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

    # Backward compatibility: if LLM returns "pause": true but no direction field,
    # map it to direction = "pause"
    direction = result.get("direction")
    if direction is None:
        if result.get("pause") is True:
            direction = "pause"
        else:
            direction = "long"  # default
        result["direction"] = direction

    # Validate direction is one of the allowed values
    valid_directions = ("long", "short", "pause")
    if direction not in valid_directions:
        result["pause"] = True
        result["pause_reason"] = f"Invalid direction: '{direction}'. Must be one of {valid_directions}"
        result["buy_levels"] = []
        result["sell_levels"] = []
        result["range_low"] = 0
        result["range_high"] = 0
        return result

    # Set pause true for backward compat if direction is pause
    if direction == "pause":
        result["pause"] = True

    # Ensure required fields exist
    for key, default in [
        ("buy_levels", []),
        ("sell_levels", []),
        ("range_low", 0),
        ("range_high", 0),
        ("confidence", ""),
        ("note", ""),
    ]:
        if key not in result:
            result[key] = default

    if "pause_reason" not in result:
        result["pause_reason"] = None

    # If direction is pause, clear levels
    if direction == "pause":
        result["buy_levels"] = []
        result["sell_levels"] = []
        result["range_low"] = 0
        result["range_high"] = 0
        return result

    # Validate levels are lists of numbers
    buy = result.get("buy_levels", [])
    sell = result.get("sell_levels", [])
    if not isinstance(buy, list) or not isinstance(sell, list):
        result["pause"] = True
        result["direction"] = "pause"
        result["pause_reason"] = "buy_levels or sell_levels is not a list"
        result["buy_levels"] = []
        result["sell_levels"] = []
        result["range_low"] = 0
        result["range_high"] = 0
        return result
    if len(buy) < 1 or len(sell) < 1:
        result["pause"] = True
        result["direction"] = "pause"
        result["pause_reason"] = "buy_levels or sell_levels is empty"
        result["buy_levels"] = []
        result["sell_levels"] = []
        result["range_low"] = 0
        result["range_high"] = 0
        return result
    if not all(isinstance(x, (int, float)) for x in buy + sell):
        result["pause"] = True
        result["direction"] = "pause"
        result["pause_reason"] = "buy_levels or sell_levels contains non-numeric values"
        result["buy_levels"] = []
        result["sell_levels"] = []
        result["range_low"] = 0
        result["range_high"] = 0
        return result

    # Direction-specific validation: check price levels are on correct side of current price
    current_price = btc_price if btc_price > 0 else (swing_15m.get("current_price", 0) if swing_15m else 0)
    if current_price > 0:
        if direction == "long":
            # Long grid: buys below price, sells above price
            invalid_buys = [x for x in buy if x >= current_price]
            invalid_sells = [x for x in sell if x <= current_price]
            if invalid_buys:
                result["pause"] = True
                result["direction"] = "pause"
                result["pause_reason"] = f"Long grid: buy levels must be below current price {current_price}, got {invalid_buys}"
                result["buy_levels"] = []
                result["sell_levels"] = []
                result["range_low"] = 0
                result["range_high"] = 0
                return result
            if invalid_sells:
                result["pause"] = True
                result["direction"] = "pause"
                result["pause_reason"] = f"Long grid: sell levels must be above current price {current_price}, got {invalid_sells}"
                result["buy_levels"] = []
                result["sell_levels"] = []
                result["range_low"] = 0
                result["range_high"] = 0
                return result
        elif direction == "short":
            # Short grid: sells below price (open shorts), buys above price (close shorts)
            invalid_sells = [x for x in sell if x >= current_price]
            invalid_buys = [x for x in buy if x <= current_price]
            if invalid_sells:
                result["pause"] = True
                result["direction"] = "pause"
                result["pause_reason"] = f"Short grid: sell levels must be below current price {current_price}, got {invalid_sells}"
                result["buy_levels"] = []
                result["sell_levels"] = []
                result["range_low"] = 0
                result["range_high"] = 0
                return result
            if invalid_buys:
                result["pause"] = True
                result["direction"] = "pause"
                result["pause_reason"] = f"Short grid: buy levels must be above current price {current_price}, got {invalid_buys}"
                result["buy_levels"] = []
                result["sell_levels"] = []
                result["range_low"] = 0
                result["range_high"] = 0
                return result

    # Validate minimum grid range (must span at least 5 ATRs)
    if not result.get("pause") and current_price > 0:
        buy = result.get("buy_levels", [])
        sell = result.get("sell_levels", [])
        if buy and sell:
            grid_range = max(sell) - min(buy) if result["direction"] == "long" else max(buy) - min(sell)
            atr_value = 0
            try:
                atr_data = calc_atr(candles_15m, period=14)
                atr_value = atr_data.get("atr", 0)
            except Exception:
                pass
            min_grid_range = atr_value * 5.0 if atr_value > 0 else 0

            if min_grid_range > 0 and grid_range < min_grid_range:
                result["pause"] = True
                result["direction"] = "pause"
                result["pause_reason"] = (
                    f"Grid range too narrow: ${grid_range:,.0f} < ${min_grid_range:,.0f} "
                    f"(minimum 5 ATRs). Grid must be wider to avoid runs."
                )
                result["buy_levels"] = []
                result["sell_levels"] = []
                result["range_low"] = 0
                result["range_high"] = 0
                return result

    return result


async def main():
    config_path = Path(__file__).parent / "config.yml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    result = await run_analyst(cfg)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
