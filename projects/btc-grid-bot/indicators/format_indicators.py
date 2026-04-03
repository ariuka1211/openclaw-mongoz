"""Formatting utilities for indicators."""

from typing import Dict
from .regime import _format_regime  # noqa: F401  (re-export)


def format_indicators(bands: Dict, atr: Dict, skew: Dict) -> str:
    """Format all indicators into text block for LLM prompt."""
    position_pct = round(bands["position"] * 100)
    half = "upper" if position_pct > 50 else "lower"
    trend_word = "expanding" if bands["expanding"] else "contracting"

    return (
        f"=== BOLLINGER BANDS ===\n"
        f"Upper: ${bands['upper']:,.0f} | Middle: ${bands['middle']:,.0f} | Lower: ${bands['lower']:,.0f}\n"
        f"Band Width: ${bands['width']:,.0f} ({bands['width_pct']:.1f}%) — {trend_word}\n"
        f"Price Position: {position_pct}% through band ({half} half)\n"
        f"Suggested Level Spacing: ${atr['suggested_spacing']:,.0f} (0.5x ATR = ${atr['atr']:,.0f})\n"
        f"\n"
        f"{skew['formatted']}"
    )
