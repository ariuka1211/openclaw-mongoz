def calculate_grid(
    account_equity: float,
    btc_price: float,
    num_buy_levels: int,
    num_sell_levels: int,
    max_exposure_mult: float = 3.0,
    margin_reserve_pct: float = 0.20,
    atr_pct: float = None,     # NEW: ATR as % of price
    atr_spacing: float = None, # NEW: ATR-based spacing in USD
    vol_cfg: dict = None,      # NEW: volatility config overrides
    compounding_mult: float = 1.0,  # Auto-compounding multiplier
    time_adj: float = 1.0,          # Time-of-day adjustment multiplier
    funding_adj: float = 1.0,       # Funding rate adjustment multiplier
    direction: str = "long",
) -> dict:
    """
    Returns:
    {
        "safe": bool,
        "size_per_level": float,       # BTC per order
        "size_per_level_usd": float,   # USD value per order
        "max_notional": float,
        "available_notional": float,
        "worst_case_notional": float,
        "buffer": float,
        "reason": str,                 # if not safe, explains why
        "adjusted_num_buy_levels": int or None,
        "adjusted_num_sell_levels": int or None,
    }
    """
    # ── Volatility-adaptive sizing ─────────────────────────────
    vol_adj = 1.0  # default: no adjustment
    if atr_pct is not None and atr_pct > 0:
        # Read thresholds from config or use defaults
        if vol_cfg is None:
            vol_cfg = {}
        baseline = vol_cfg.get("baseline_pct", 0.005)      # 0.5% ATR = neutral
        high_threshold = vol_cfg.get("high_threshold_pct", 0.015)  # 1.5% ATR = start reducing
        max_increase = vol_cfg.get("max_increase", 0.20)   # max 20% size increase
        max_decrease = vol_cfg.get("max_decrease", 0.40)   # max 40% size decrease

        if atr_pct < baseline:
            vol_adj = 1.0 + (baseline - atr_pct) / baseline * max_increase
        elif atr_pct > high_threshold:
            vol_adj = 1.0 - (atr_pct - high_threshold) / high_threshold * max_decrease
        # between baseline and high_threshold: vol_adj stays at 1.0

    # Log the adjustment
    if vol_adj != 1.0 and atr_pct:
        print(f"Volatility adj: {vol_adj:.2f}x (ATR: {atr_pct:.2%})")

    if direction == "short":
        print("Direction: short — margin calculation same but monitoring for short squeeze risk")

    max_notional = account_equity * max_exposure_mult
    reserved = account_equity * margin_reserve_pct
    available = max_notional - reserved
    total_levels = num_buy_levels + num_sell_levels

    safety_factor = 0.90
    safe_available = available * safety_factor
    size_per_level = (safe_available / total_levels) / btc_price
    size_per_level *= vol_adj  # Apply vol adjustment to size
    size_per_level *= compounding_mult  # Apply auto-compounding
    size_per_level *= time_adj  # Apply time-of-day adjustment
    size_per_level *= funding_adj  # Apply funding rate adjustment
    adjusted_num_buy = None
    adjusted_num_sell = None
    
    min_size = 0.001
    if size_per_level < min_size:
        size_per_level = min_size
        total_levels = num_buy_levels + num_sell_levels
        max_notional_for_min = size_per_level * btc_price * total_levels
        if max_notional_for_min > available:
            max_levels = int(available / (size_per_level * btc_price))
            if max_levels < 2:
                return {
                    "safe": False,
                    "size_per_level": 0,
                    "size_per_level_usd": 0,
                    "max_notional": 0,
                    "available_notional": 0,
                    "worst_case_notional": 0,
                    "buffer": 0,
                    "reason": "Equity too low to place even minimum size orders",
                    "adjusted_num_buy_levels": None,
                    "adjusted_num_sell_levels": None,
                    "vol_adj": round(vol_adj, 2),
                    "atr_pct": round(atr_pct, 4) if atr_pct else None,
                    "time_adj": round(time_adj, 2),
                    "funding_adj": round(funding_adj, 2),
                }
            adjusted_num_buy = max(1, int(num_buy_levels * (max_levels / total_levels)))
            adjusted_num_sell = max(1, int(num_sell_levels * (max_levels / total_levels)))
            total_levels = adjusted_num_buy + adjusted_num_sell
            size_per_level = (available * safety_factor) / (total_levels * btc_price)
            size_per_level *= vol_adj  # Re-apply vol adjustment after min size fix
            size_per_level *= time_adj  # Re-apply time adj after min size fix
            size_per_level *= funding_adj  # Re-apply funding adj after min size fix
    
    worst_case_notional = total_levels * size_per_level * btc_price

    safe = worst_case_notional <= available
    return {
        "safe": safe,
        "size_per_level": round(size_per_level, 6),
        "size_per_level_usd": round(size_per_level * btc_price, 2),
        "max_notional": round(max_notional, 2),
        "available_notional": round(available, 2),
        "worst_case_notional": round(worst_case_notional, 2),
        "buffer": round(available - worst_case_notional, 2),
        "reason": "" if safe else f"Worst case ${worst_case_notional:.0f} exceeds available ${available:.0f}",
        "adjusted_num_buy_levels": adjusted_num_buy,
        "adjusted_num_sell_levels": adjusted_num_sell,
        "vol_adj": round(vol_adj, 2),
        "atr_pct": round(atr_pct, 4) if atr_pct else None,
        "compounding_mult": round(compounding_mult, 3),
        "time_adj": round(time_adj, 2),
        "funding_adj": round(funding_adj, 2),
        "direction": direction,
    }


def print_safety_table(equity, btc_price, num_buy, num_sell, result, max_exposure_mult=3.0, margin_reserve_pct=0.20):
    """Print a formatted safety table to terminal."""
    status = "✅ SAFE TO DEPLOY" if result["safe"] else "❌ UNSAFE — DO NOT DEPLOY"
    print("═" * 45)
    print("  BTC Grid Bot — Capital Safety Check")
    print("═" * 45)
    print(f"  Account equity:      ${equity:,.0f} USDC")
    print(f"  BTC mark price:      ${btc_price:,.0f}")
    print()
    print(f"  Max safe notional:   ${result['max_notional']:,.0f}  ({max_exposure_mult:.1f}× equity)")
    print(f"  Margin reserved:     ${equity * margin_reserve_pct:,.0f}    ({margin_reserve_pct*100:.0f}%)")
    print(f"  Available notional:  ${result['available_notional']:,.0f}")
    print()
    print(f"  Grid: {num_buy} buy + {num_sell} sell levels")
    print(f"  Size per level:      {result['size_per_level']:.6f} BTC  (${result['size_per_level_usd']:,.0f}/level)")
    if result.get("vol_adj") is not None and result["vol_adj"] != 1.0:
        atr_str = f" (ATR: {result['atr_pct']:.2%})" if result.get("atr_pct") else ""
        print(f"  Volatility adj:    {result['vol_adj']:.2f}x{atr_str}")
    if result.get("time_adj") is not None and result["time_adj"] != 1.0:
        print(f"  Time adj:          {result['time_adj']:.2f}x")
    if result.get("compounding_mult") is not None and result["compounding_mult"] != 1.0:
        print(f"  Compounding adj:   {result['compounding_mult']:.3f}x")
    if result.get("funding_adj") is not None and result["funding_adj"] != 1.0:
        print(f"  Funding adj:       {result['funding_adj']:.2f}x")
    print(f"  Worst case (all levels fill): ${result['worst_case_notional']:,.0f}")
    print()
    if result["safe"]:
        print(f"  Buffer remaining:    ${result['buffer']:,.0f}")
    else:
        print(f"  Over by:             ${-result['buffer']:,.0f}")
        print(f"  → Reduce levels or increase equity")
    print()
    print(f"  {status}")
    print("═" * 45)


async def main():
    """Run standalone: python calculator.py"""
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)

    api = LighterAPI(cfg)
    equity = await api.get_equity()
    btc_price = await api.get_btc_price()

    num_buy = cfg["grid"]["max_levels"] // 2
    num_sell = cfg["grid"]["max_levels"] // 2

    # Load volatility config section if present
    vol_cfg = cfg.get("volatility", {})
    result = calculate_grid(
        equity, btc_price, num_buy, num_sell,
        cfg["capital"]["max_exposure_multiplier"],
        cfg["capital"]["margin_reserve_pct"],
        atr_pct=None,  # Standalone mode: no ATR passed
        vol_cfg=vol_cfg,
    )
    print_safety_table(equity, btc_price, num_buy, num_sell, result)


if __name__ == "__main__":
    asyncio.run(main())