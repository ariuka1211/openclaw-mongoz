"""OI (Open Interest) divergence analysis."""

from typing import List, Dict


def oi_divergence(price_history: list, oi_history: list) -> dict:
    """Detect OI/Price divergence to identify forced liquidation regimes.

    Compares last 12 readings (3 hours) vs the 12 before that (3-6 hours ago).

    Args:
        price_history: last ~50 candles with keys {"ts": int, "c": float}
        oi_history: last ~50 OI readings with keys {"t": int, "oi": float}

    Returns:
        {
            "state": str,  # "long_squeeze" | "capitulation" | "new_shorts" | "new_longs" | "neutral"
            "price_direction": str,  # "up", "down", "flat"
            "oi_direction": str,     # "up", "down", "flat"
            "price_change_pct": float,
            "oi_change_pct": float,
            "label": str,
            "formatted": str,
            "grid_implication": str
        }
    """
    if not price_history or not oi_history or len(price_history) < 24 or len(oi_history) < 24:
        return {
            "state": "neutral",
            "price_direction": "flat",
            "oi_direction": "flat",
            "price_change_pct": 0.0,
            "oi_change_pct": 0.0,
            "label": "Insufficient data for OI divergence analysis",
            "formatted": "OI Divergence: neutral (insufficient data)",
            "grid_implication": "none"
        }

    # Take last 24 readings for both price and OI
    recent_prices = price_history[-12:]
    old_prices = price_history[-24:-12]
    recent_oi = oi_history[-12:]
    old_oi = oi_history[-24:-12]

    # Compute averages
    recent_price_avg = sum(c["c"] for c in recent_prices) / len(recent_prices)
    old_price_avg = sum(c["c"] for c in old_prices) / len(old_prices)
    recent_oi_avg = sum(o["oi"] for o in recent_oi) / len(recent_oi)
    old_oi_avg = sum(o["oi"] for o in old_oi) / len(old_oi)

    # Compute direction (% change)
    if old_price_avg > 0:
        price_change_pct = (recent_price_avg - old_price_avg) / old_price_avg
    else:
        price_change_pct = 0.0

    if old_oi_avg > 0:
        oi_change_pct = (recent_oi_avg - old_oi_avg) / old_oi_avg
    else:
        oi_change_pct = 0.0

    # Classify directions (threshold: 0.1% for flat detection)
    def direction(pct):
        if pct > 0.001:
            return "up"
        elif pct < -0.001:
            return "down"
        else:
            return "flat"

    price_dir = direction(price_change_pct)
    oi_dir = direction(oi_change_pct)

    # Classify the 4 divergence states
    if price_dir == "up" and oi_dir == "down":
        state = "long_squeeze"
        label = f"Price up {price_change_pct*100:.2f}% + OI down {abs(oi_change_pct)*100:.2f}% → Shorts getting squeezed (bullish continuation)"
        grid_implication = "widen sells"
    elif price_dir == "down" and oi_dir == "down":
        state = "capitulation"
        label = f"Price down {abs(price_change_pct)*100:.2f}% + OI down {abs(oi_change_pct)*100:.2f}% → Longs getting liquidated (capitulation)"
        grid_implication = "prepare buys"
    elif price_dir == "down" and oi_dir == "up":
        state = "new_shorts"
        label = f"Price down {abs(price_change_pct)*100:.2f}% + OI up {oi_change_pct*100:.2f}% → New shorts entering (bearish trend)"
        grid_implication = "reduce size"
    elif price_dir == "up" and oi_dir == "up":
        state = "new_longs"
        label = f"Price up {price_change_pct*100:.2f}% + OI up {oi_change_pct*100:.2f}% → New longs entering (bullish trend)"
        grid_implication = "normal grid"
    else:
        state = "neutral"
        label = "Price and OI both flat — no clear divergence signal"
        grid_implication = "none"

    formatted = (
        f"OI Divergence: {state} | Price: {price_dir} ({price_change_pct*100:+.2f}%) | "
        f"OI: {oi_dir} ({oi_change_pct*100:+.2f}%)\n"
        f"  {label}\n"
        f"  Grid hint: {grid_implication}"
    )

    return {
        "state": state,
        "price_direction": price_dir,
        "oi_direction": oi_dir,
        "price_change_pct": round(price_change_pct, 6),
        "oi_change_pct": round(oi_change_pct, 6),
        "label": label,
        "formatted": formatted,
        "grid_implication": grid_implication
    }
