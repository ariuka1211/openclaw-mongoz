"""Funding rate adjustment calculation."""

from typing import Dict


def funding_rate_adjustment(funding_data: dict, price: float) -> dict:
    """Calculate grid size adjustment based on funding rate.

    Args:
        funding_data: dict with 'current' key containing funding info from market_intel.
        price: current BTC price (used for context in warning messages).

    Returns:
        {"adj_multiplier": float, "label": str, "warning": str | None}
    """
    current = funding_data.get("current", {})
    funding_rate = current.get("funding_rate", 0)

    if funding_rate < 0:
        # Negative funding: shorts paying longs — bullish squeeze potential
        if funding_rate < -0.0003:
            adj_multiplier = 1.15
            label = "Very negative funding — strong squeeze potential (1.15x)"
            warning = None
        elif funding_rate < -0.0001:
            adj_multiplier = 1.1
            label = "Negative funding — moderate squeeze potential (1.1x)"
            warning = None
        else:  # -0.0001 to 0
            adj_multiplier = 1.05
            label = "Slightly negative funding — mild squeeze potential (1.05x)"
            warning = None
    elif funding_rate > 0.001:
        # > 0.1% — extreme positive funding
        adj_multiplier = 0.4
        label = "Extreme positive funding — reduce 60%"
        warning = "⚠️ EXTREME FUNDING: Size reduced 60% to avoid liquidation squeeze"
    elif funding_rate > 0.0004:
        # 0.04% to 0.1%
        adj_multiplier = 0.6
        label = "High positive funding — reduce 40%"
        warning = None
    elif funding_rate > 0.0001:
        # 0.01% to 0.04%
        adj_multiplier = 0.8
        label = "Elevated positive funding — reduce 20%"
        warning = None
    else:
        # 0 to 0.01%
        adj_multiplier = 1.0
        label = "Normal funding — no adjustment"
        warning = None

    formatted = (
        f"Funding Rate: {funding_rate*100:.4f}% per 8h"
        f" | Adjustment: {adj_multiplier:.2f}x ({label})"
    )
    if warning:
        formatted += f" | {warning}"

    return {
        "adj_multiplier": adj_multiplier,
        "label": label,
        "warning": warning,
        "formatted": formatted,
    }
