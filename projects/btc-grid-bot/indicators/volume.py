"""Volume profile and volume spike detection."""

from typing import Dict, List


def calc_volume_profile(all_candles: List[Dict], current_price: float, bin_size: float = 50.0) -> Dict:
    """Build a Volume-at-Price histogram from combined candle data.

    Buckets prices into $bin_size bins and sums volume per bin.

    Returns:
        {
            "hist": dict[int, float]   # rounded price -> total volume
            "nodes": list[dict]        # top volume nodes, sorted by volume desc
            "poc": float               # Point of Control (highest volume price)
            "hvn": list[float]         # High Volume Nodes (top 3 by volume)
            "lvn": list[float]         # Low Volume Nodes (gaps between HVNs, max 3)
            "formatted": str           # text representation for LLM prompt
        }
    """
    if not all_candles:
        return {
            "hist": {},
            "nodes": [],
            "poc": 0.0,
            "hvn": [],
            "lvn": [],
            "formatted": "No volume profile data available.",
        }

    # Build histogram: bucket each candle by rounded high+low midpoint volume
    hist: Dict[int, float] = {}
    for c in all_candles:
        # Use average of high and low as the "representative price" for this candle
        mid = (c["h"] + c["l"]) / 2.0
        bucket = int(mid // bin_size) * int(bin_size)
        vol = c.get("v", 0)
        hist[bucket] = hist.get(bucket, 0.0) + vol

    if not hist:
        return {
            "hist": {},
            "nodes": [],
            "poc": 0.0,
            "hvn": [],
            "lvn": [],
            "formatted": "No volume profile data available.",
        }

    # Find Point of Control (highest volume bucket)
    poc_price = max(hist, key=hist.get)
    poc_vol = hist[poc_price]

    # Build sorted nodes list
    nodes = sorted(
        [{"price": float(price), "volume": float(vol)} for price, vol in hist.items()],
        key=lambda x: x["volume"],
        reverse=True,
    )

    # HVN: top 3 volume nodes
    hvn = [n["price"] for n in nodes[:3]]

    # LVN: low-volume gaps between HVNs (up to 3)
    if len(hvn) >= 2:
        hvn_sorted = sorted(hvn)
        lvn = []
        for i in range(len(hvn_sorted) - 1):
            gap_mid = (hvn_sorted[i] + hvn_sorted[i + 1]) / 2.0
            gap_bucket = int(gap_mid // bin_size) * int(bin_size)
            # Only include if volume in this gap is significantly lower than neighbors
            gap_vol = hist.get(gap_bucket, 0)
            neighbor_vols = [hist.get(b, 0) for b in hist if abs(b - gap_bucket) < 3 * bin_size and b != gap_bucket]
            if neighbor_vols and gap_vol < sum(neighbor_vols) / len(neighbor_vols) * 0.3:
                lvn.append(float(gap_bucket))
        lvn = lvn[:3]
    else:
        lvn = []

    # Format for LLM prompt
    lines = []
    lines.append("=== VOLUME PROFILE ===")
    lines.append(f"Point of Control: ${poc_price:,.0f} (vol: {poc_vol:.2f})")
    lines.append(f"High Volume Nodes: {', '.join(f'${p:,.0f}' for p in hvn)}")
    if lvn:
        lines.append(f"Low Volume Nodes (gaps): {', '.join(f'${p:,.0f}' for p in lvn)}")
    lines.append("Top 10 volume bins:")
    for n in nodes[:10]:
        marker = " <-- POC" if n["price"] == poc_price else ""
        marker += " <-- HVN" if n["price"] in hvn else ""
        lines.append(f"  ${int(n['price']):,}: {n['volume']:.2f}{marker}")
    lines.append("=== END VOLUME PROFILE ===")
    formatted = "\n".join(lines)

    return {
        "hist": hist,
        "nodes": nodes,
        "poc": float(poc_price),
        "hvn": hvn,
        "lvn": lvn,
        "formatted": formatted,
    }


def detect_volume_spike(candles: List[Dict], period: int = 20, threshold_mult: float = 2.5) -> dict:
    """Detect volume spike in the latest candle.

    A volume spike is when the latest candle's volume exceeds
    threshold_mult * rolling average volume over the previous `period` candles.

    Args:
        candles: list of OHLCV dicts with keys "ts", "o", "h", "l", "c", "v" (last ~100 candles)
        period: number of candles to use for the rolling average (default: 20)
        threshold_mult: multiplier for spike detection (default: 2.5)

    Returns:
        {
            "is_spike": bool,
            "volume_ratio": float,  # current_vol / avg_vol
            "avg_volume": float,
            "current_volume": float,
            "direction": str,       # "bullish" | "bearish" | "neutral"
            "candle_body_pct": float,  # (close - open) / open * 100
            "label": str,           # human-readable description
            "formatted": str,       # text for LLM prompt
            "mean_reversion_likely": bool  # True if spike and volume_ratio > threshold
        }
    """
    if len(candles) < period + 1:
        return {
            "is_spike": False,
            "volume_ratio": 0.0,
            "avg_volume": 0.0,
            "current_volume": 0.0,
            "direction": "neutral",
            "candle_body_pct": 0.0,
            "label": "Insufficient candle data for volume spike detection",
            "formatted": "Volume Spike: insufficient data",
            "mean_reversion_likely": False,
        }

    # Latest candle
    latest = candles[-1]
    current_vol = latest.get("v", 0)
    current_open = latest.get("o", 0)
    current_close = latest.get("c", 0)

    # Rolling average volume from the previous `period` candles (exclude latest)
    prev_candles = candles[-(period + 1):-1]  # candles before the latest, up to `period` back
    avg_volume = sum(c.get("v", 0) for c in prev_candles) / len(prev_candles) if prev_candles else 0

    if avg_volume == 0:
        return {
            "is_spike": False,
            "volume_ratio": 0.0,
            "avg_volume": 0.0,
            "current_volume": current_vol,
            "direction": "neutral",
            "candle_body_pct": 0.0,
            "label": "Average volume is zero — cannot detect spike",
            "formatted": "Volume Spike: avg volume zero",
            "mean_reversion_likely": False,
        }

    volume_ratio = current_vol / avg_volume
    is_spike = volume_ratio > threshold_mult

    # Determine direction
    if current_close > current_open:
        direction = "bullish"
    elif current_close < current_open:
        direction = "bearish"
    else:
        direction = "neutral"

    # Candle body percentage
    candle_body_pct = ((current_close - current_open) / current_open * 100) if current_open != 0 else 0.0

    # Human-readable label
    if is_spike:
        label = f"Volume spike detected ({volume_ratio:.2f}x average) — {direction}"
    else:
        label = f"Normal volume ({volume_ratio:.2f}x average)"

    # Formatted text for LLM prompt
    if is_spike:
        mean_rev_text = "MEAN REVERSION LIKELY — consider counter-spike grid placement" if volume_ratio > threshold_mult else ""
        formatted = (
            f"⚡ VOLUME SPIKE: {current_vol:.2f} ({volume_ratio:.2f}x avg {avg_volume:.2f})"
            f" | Direction: {direction} (body {candle_body_pct:.2f}%)"
            f" | Mean reversion likely: {is_spike and volume_ratio > threshold_mult}"
        )
        if mean_rev_text:
            formatted += f" | {mean_rev_text}"
    else:
        formatted = f"Volume: normal ({current_vol:.2f}, {volume_ratio:.2f}x avg {avg_volume:.2f})"

    return {
        "is_spike": is_spike,
        "volume_ratio": round(volume_ratio, 3),
        "avg_volume": round(avg_volume, 2),
        "current_volume": round(current_vol, 2),
        "direction": direction,
        "candle_body_pct": round(candle_body_pct, 3),
        "label": label,
        "formatted": formatted,
        "mean_reversion_likely": is_spike and volume_ratio > threshold_mult,
    }
