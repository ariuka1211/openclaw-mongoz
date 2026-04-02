def calculate_grid(
    account_equity: float,
    btc_price: float,
    num_buy_levels: int,
    num_sell_levels: int,
    max_exposure_mult: float = 3.0,
    margin_reserve_pct: float = 0.20,
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
        "reason": str                  # if not safe, explains why
    }
    """
    max_notional = account_equity * max_exposure_mult
    reserved = account_equity * margin_reserve_pct
    available = max_notional - reserved
    total_levels = num_buy_levels + num_sell_levels
    
    # Size per level with 10% safety margin (never use 100% of available)
    safety_factor = 0.90
    safe_available = available * safety_factor
    size_per_level = (safe_available / total_levels) / btc_price
    
    # Enforce minimum order size of 0.001 BTC (~$66 at current prices)
    min_size = 0.001
    if size_per_level < min_size:
        size_per_level = min_size
        # Reduce number of levels to fit within available equity
        total_levels = num_buy_levels + num_sell_levels
        # Recalculate safe levels based on min size
        max_notional_for_min = size_per_level * btc_price * total_levels
        if max_notional_for_min > available:
            # Still exceeds, need to reduce levels
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
                    "reason": "Equity too low to place even minimum size orders"
                }
            # Adjust levels proportionally
            num_buy_levels = max(1, int(num_buy_levels * (max_levels / total_levels)))
            num_sell_levels = max(1, int(num_sell_levels * (max_levels / total_levels)))
            total_levels = num_buy_levels + num_sell_levels
            # Recalculate size with adjusted levels
            size_per_level = (available * safety_factor) / (total_levels * btc_price)
    
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
        "reason": "" if safe else f"Worst case ${worst_case_notional:.0f} exceeds available ${available:.0f}"
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

    result = calculate_grid(
        equity, btc_price, num_buy, num_sell,
        cfg["capital"]["max_exposure_multiplier"],
        cfg["capital"]["margin_reserve_pct"],
    )
    print_safety_table(equity, btc_price, num_buy, num_sell, result)


if __name__ == "__main__":
    asyncio.run(main())