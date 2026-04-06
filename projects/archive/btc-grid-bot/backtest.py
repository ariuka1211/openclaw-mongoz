#!/usr/bin/env python3
"""
BTC Grid Bot — Backtester

Fetches historical OHLCV from OKX (public API, no auth needed),
simulates grid trading with ATR-based level generation, and
reports PnL, fill stats, and win rate.

⚡ Fees assumed: 0.02% maker rate (adjust FEE_PCT if different on Lighter)

Usage:
    python3 backtest.py                    # default: 30 days, 5 buy + 3 sell
    python3 backtest.py --days 90           # 90 days
    python3 backtest.py --buys 4 --sells 4  # custom grid shape
    python3 backtest.py --equity 500        # starting capital
    python3 backtest.py --interval 5m      # candle granularity
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────
FEE_PCT = 0.0002  # 0.02% maker fee (adjust for your exchange)
OKX_BASE = "https://www.okx.com"

# ── OKX Data ──────────────────────────────────────────────────────

def fetch_okx_candles(inst_id="BTC-USDT-SWAP", bar="1H", limit=100, after_ts=None):
    """
    Fetch OHLCV candles from OKX public API.
    Returns list of dicts: {t, o, h, l, c, vol, volCcy}
    """
    import requests
    
    params = {
        "instId": inst_id,
        "bar": bar,
        "limit": limit
    }
    if after_ts:
        params["after"] = after_ts
    
    url = f"{OKX_BASE}/api/v5/market/candles"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; btc-grid-backtest/1.0)",
        "Accept": "application/json"
    }
    
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "0":
        raise RuntimeError(f"OKX API error: {data}")

    candles = []
    # OKX returns: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    for row in data["data"]:
        candles.append({
            "t": int(row[0]),
            "o": float(row[1]),
            "h": float(row[2]),
            "l": float(row[3]),
            "c": float(row[4]),
            "v": float(row[5]),
        })
    return candles


def download_candles(inst_id="BTC-USDT-SWAP", bar="1H", days=30, save_path=None):
    """
    Download candles for the requested period.
    OKX returns max 100 per call, so we paginate backward.
    """
    now = int(time.time() * 1000)
    start = now - (days * 86_400_000)

    # Estimate candle count for pagination
    bar_minutes = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1H": 60, "2H": 120, "4H": 240, "1D": 1440,
    }
    minutes = bar_minutes.get(bar, 60)
    candles_per_day = 1440 / minutes

    all_candles = []
    cursor = None
    batch = 1
    max_batches = int((candles_per_day * days) / 100) + 5  # safety margin

    print(f"  Fetching {bar} candles for {days} days (~{int(candles_per_day * days)} candles)...")

    while batch <= max_batches:
        batch_candles = fetch_okx_candles(inst_id, bar, limit=100, after_ts=cursor)
        if not batch_candles:
            break

        all_candles.extend(batch_candles)

        # oldest candle in this batch → use as cursor for next call
        oldest = min(c["t"] for c in batch_candles)
        cursor = oldest - 1  # shift back by 1ms

        ts = datetime.fromtimestamp(oldest / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"    Batch {batch}: {len(batch_candles)} candles, oldest = {ts}")

        if oldest <= start:
            break

        batch += 1
        time.sleep(0.2)  # rate limit courtesy

    # Sort ascending (oldest first)
    all_candles.sort(key=lambda c: c["t"])

    # Trim to start boundary
    all_candles = [c for c in all_candles if c["t"] >= start]

    # Deduplicate (same timestamp)
    seen = set()
    unique = []
    for c in all_candles:
        if c["t"] not in seen:
            seen.add(c["t"])
            unique.append(c)
    all_candles = unique

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w") as f:
            json.dump(all_candles, f)
        print(f"  Saved {len(all_candles)} candles to {save_path}")

    return all_candles


# ── ATR Calculation ───────────────────────────────────────────────

def calc_atr(candles, period=14):
    """Simple ATR (Wilder's smoothing)."""
    if len(candles) < period + 1:
        return 0

    true_ranges = []
    for i in range(1, len(candles)):
        h = candles[i]["h"]
        l = candles[i]["l"]
        prev_c = candles[i - 1]["c"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    atr = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return atr


# ── Grid Level Generator ──────────────────────────────────────────

def generate_grid_levels(price, atr, num_buy, num_sell, equity,
                          max_exposure=3.0, margin_reserve=0.20,
                          spacing_atr=0.5):
    """
    Generate grid levels ATR-based from current price.
    Mirrors the bot's calculator.py logic (simplified).

    Returns: {buy_levels, sell_levels, size_per_level, range_low, range_high}
    """
    spacing = atr * spacing_atr  # distance between levels in $

    buy_levels = []
    for i in range(1, num_buy + 1):
        buy_levels.append(round(price - spacing * i, 1))

    sell_levels = []
    for i in range(1, num_sell + 1):
        sell_levels.append(round(price + spacing * i, 1))

    # Size per level (same as calculator.py, simplified)
    max_notional = equity * max_exposure
    reserved = equity * margin_reserve
    available = (max_notional - reserved) * 0.90  # safety factor
    total_levels = num_buy + num_sell
    size_per_level = available / total_levels / price
    size_per_level = max(size_per_level, 0.001)  # min size

    return {
        "buy_levels": buy_levels,
        "sell_levels": sell_levels,
        "size_per_level": round(size_per_level, 6),
        "range_low": buy_levels[0] if buy_levels else price,
        "range_high": sell_levels[-1] if sell_levels else price,
    }


# ── Simulation Engine ─────────────────────────────────────────────

class BacktestEngine:
    """Simulate grid trading on historical candles."""

    def __init__(self, equity, num_buy, num_sell, fee_pct=FEE_PCT,
                 max_exposure=3.0, margin_reserve=0.20,
                 spacing_atr=0.5, atr_period=14,
                 roll_threshold=0.8, max_rolls=2):
        self.initial_equity = equity
        self.equity = equity
        self.usdc_balance = equity
        self.btc_balance = 0.0
        self.fee_pct = fee_pct
        self.num_buy = num_buy
        self.num_sell = num_sell
        self.max_exposure = max_exposure
        self.margin_reserve = margin_reserve
        self.spacing_atr = spacing_atr
        self.atr_period = atr_period
        self.roll_threshold = roll_threshold
        self.max_rolls = max_rolls

        # Grid state
        self.buy_orders = []    # list of prices
        self.sell_orders = []   # list of prices
        self.size_per_level = 0
        self.grid_range_low = 0
        self.grid_range_high = 0

        # Stats
        self.trades = []
        self.rolls = 0
        self.fills = 0
        self.total_buy_volume = 0
        self.total_sell_volume = 0
        self.realized_pnl = 0
        self.realized_pnl_before_fees = 0
        self.total_fees = 0
        self.daily_pnls = {}  # date -> pnl

    def _deploy_grid(self, price, atr):
        """Place orders — resets any existing open orders."""
        grid = generate_grid_levels(
            price, atr, self.num_buy, self.num_sell,
            self.equity, self.max_exposure, self.margin_reserve,
            self.spacing_atr
        )
        self.buy_orders = grid["buy_levels"][:]
        self.sell_orders = grid["sell_levels"][:]
        self.size_per_level = grid["size_per_level"]
        self.grid_range_low = grid["range_low"]
        self.grid_range_high = grid["range_high"]

    def _should_roll(self, price, atr):
        """Check if price has moved near edge → new grid deployment."""
        if self.rolls >= self.max_rolls:
            return False
        threshold = atr * self.roll_threshold
        near_top = price >= self.grid_range_high - threshold
        near_bottom = price <= self.grid_range_low + threshold
        return near_top or near_bottom

    def _process_candle(self, candle, atr_val):
        """
        Process one candle through the grid.
        Returns number of fills processed.
        """
        fills = 0
        o, h, l, c = candle["o"], candle["h"], candle["l"], candle["c"]
        sz = self.size_per_level

        # Check if we should roll
        if self._should_roll(c, atr_val):
            self._deploy_grid(c, atr_val)
            self.rolls += 1
            return 0

        # For a candle [low, high], a buy order at buy_p fills if low <= buy_p
        # A sell order at sell_p fills if high >= sell_p
        # Within a single candle, process buys first (price drops), then sells (price rises)

        # Detect filled buy orders
        new_buy_orders = []
        for buy_p in sorted(self.buy_orders, reverse=True):  # highest buy first
            if l <= buy_p <= h or l <= buy_p:  # price touched this buy level
                # BUY filled — we bought sz BTC at buy_p
                cost = sz * buy_p
                fee = cost * self.fee_pct
                if self.usdc_balance >= cost + fee:
                    self.usdc_balance -= (cost + fee)
                    self.btc_balance += sz
                    self.total_fees += fee
                    self.total_buy_volume += sz
                    self.fills += 1
                    fills += 1

                    # Place a sell replacement order at the buy level + spacing
                    spacing = atr_val * self.spacing_atr
                    replacement_sell = round(buy_p + spacing, 1)
                    if replacement_sell not in self.sell_orders:
                        self.sell_orders.append(replacement_sell)
                        self.sell_orders.sort()

                    self.trades.append({
                        "side": "buy",
                        "price": buy_p,
                        "size": sz,
                        "fee": fee,
                        "ts": candle["t"],
                    })

                    # Track daily PnL
                    date = datetime.fromtimestamp(candle["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    self.daily_pnls.setdefault(date, 0)

        # Detect filled sell orders
        for sell_p in sorted(self.sell_orders):  # lowest sell first
            if h >= sell_p >= l or h >= sell_p:  # price touched this sell level
                # SELL filled — we sold sz BTC at sell_p
                # Only if we have BTC
                if self.btc_balance >= sz:
                    revenue = sz * sell_p
                    fee = revenue * self.fee_pct
                    self.btc_balance -= sz
                    self.usdc_balance += (revenue - fee)
                    self.total_fees += fee
                    self.total_sell_volume += sz
                    self.fills += 1
                    fills += 1

                    # Calculate realized PnL for this round-trip
                    # Find the most recent unfilled buy that this sell closes
                    # Simple: PnL = sell_revenue - buy_cost - fees
                    buy_cost = sz * (sell_p - atr_val * self.spacing_atr)  # rough match
                    pnl = revenue - buy_cost - 2 * fee  # buy fee + sell fee
                    self.realized_pnl += pnl
                    self.realized_pnl_before_fees += (revenue - buy_cost)

                    # Place a buy replacement order at the sell level - spacing
                    spacing = atr_val * self.spacing_atr
                    replacement_buy = round(sell_p - spacing, 1)
                    if replacement_buy not in self.buy_orders:
                        self.buy_orders.append(replacement_buy)
                        self.buy_orders.sort()

                    date = datetime.fromtimestamp(candle["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    self.daily_pnls.setdefault(date, 0)
                    self.daily_pnls[date] += pnl

                    self.trades.append({
                        "side": "sell",
                        "price": sell_p,
                        "size": sz,
                        "pnl": pnl,
                        "fee": fee,
                        "ts": candle["t"],
                    })

        return fills

    def run(self, candles):
        """
        Run backtest over full candle series.
        candles: list of {t, o, h, l, c, v}
        """
        print("\n" + "=" * 55)
        print("  BTC Grid Bot Backtester")
        print("=" * 55)
        print(f"  Period: {datetime.fromtimestamp(candles[0]['t']/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(candles[-1]['t']/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
        print(f"  Candles: {len(candles)}")
        print(f"  Starting equity: ${self.initial_equity:,.2f}")
        print(f"  Grid: {self.num_buy} buy + {self.num_sell} sell levels")
        print(f"  Fee: {self.fee_pct*100:.3f}% per fill")

        # Need enough candles for ATR warmup
        warmup = self.atr_period + 5
        start_idx = warmup

        price = candles[start_idx - 1]["c"]
        atr = calc_atr(candles[:start_idx], self.atr_period)

        print(f"  Start price: ${price:,.2f}")
        print(f"  Start ATR:   ${atr:.2f} ({atr/price*100:.2f}%)")
        print("=" * 55)

        # Initial grid deployment
        self._deploy_grid(price, atr)

        # Progress tracking
        print(f"  Starting simulation...")

        for i in range(start_idx, len(candles)):
            candle = candles[i]
            atr = calc_atr(candles[max(0, i - 100):i + 1], self.atr_period)
            self._process_candle(candle, atr)

            # Print progress every 1000 candles
            if (i - start_idx) % 1000 == 0:
                pct = (i - start_idx) / (len(candles) - start_idx) * 100
                print(f"    Progress: {pct:.0f}%")

        self._print_results(candles)

    def _print_results(self, candles):
        """Print backtest summary."""
        duration_days = (candles[-1]["t"] - candles[0]["t"]) / 86_400_000
        end_price = candles[-1]["c"]
        start_price = candles[0]["c"]

        # Final equity = USDC balance + BTC at market price
        final_btc_value = self.btc_balance * end_price
        final_equity = self.usdc_balance + final_btc_value

        total_return = final_equity - self.initial_equity
        return_pct = total_return / self.initial_equity * 100

        buy_only_return = (start_price - end_price) / start_price  # short bias
        hold_return = (end_price - start_price) / start_price * 100

        # Buy/sell counts
        buy_count = sum(1 for t in self.trades if t["side"] == "buy")
        sell_count = sum(1 for t in self.trades if t["side"] == "sell")
        total_round_trips = min(buy_count, sell_count)

        # Win rate (sell trades with positive PnL)
        sell_trades = [t for t in self.trades if t["side"] == "sell"]
        winning = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
        win_rate = winning / len(sell_trades) * 100 if sell_trades else 0

        print("\n" + "=" * 55)
        print("  📊 BACKTEST RESULTS")
        print("=" * 55)
        print(f"  Period: {duration_days:.1f} days ({datetime.fromtimestamp(candles[0]['t']/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(candles[-1]['t']/1000, tz=timezone.utc).strftime('%Y-%m-%d')})")
        print(f"  Candles processed: {len(candles) - self.atr_period - 5}")
        print(f"  Starting equity:   ${self.initial_equity:,.2f}")
        print(f"  Final equity:      ${final_equity:,.2f}")
        print(f"    USDC:            ${self.usdc_balance:,.2f}")
        print(f"    BTC:             {self.btc_balance:.6f} (${final_btc_value:,.2f})")
        print()
        print(f"  Realized PnL:      ${self.realized_pnl:,.2f}")
        print(f"  Realized (pre-fee):${self.realized_pnl_before_fees:,.2f}")
        print(f"  Total fees:        ${self.total_fees:,.2f}")
        print(f"  Grid return:       {return_pct:+.2f}%")
        print(f"  B&H return:        {hold_return:+.2f}%")
        print()
        print(f"  Total fills:       {self.fills}")
        print(f"    Buys filled:     {buy_count}")
        print(f"    Sells filled:    {sell_count}")
        print(f"  Round trips:       {total_round_trips}")
        print(f"  Win rate:          {win_rate:.1f}%")
        print(f"  Grid rolls:        {self.rolls}")
        print()

        # Daily PnL breakdown
        if self.daily_pnls:
            sorted_days = sorted(self.daily_pnls.keys())
            print("  ── Daily PnL (last 20 days) ──")
            for day in sorted_days[-20:]:
                pnl = self.daily_pnls[day]
                bar = "▓" * max(0, int(abs(pnl))) + "░" * max(0, int(20 - abs(pnl)))
                emoji = "🟢" if pnl >= 0 else "🔴"
                print(f"    {day}  {emoji} ${pnl:>+8.4f}  {bar}")

            profitable_days = sum(1 for v in self.daily_pnls.values() if v > 0)
            total_active_days = len([v for v in self.daily_pnls.values() if abs(v) > 0.001])
            print(f"\n  Profitable days: {profitable_days}/{total_active_days} ({profitable_days/total_active_days*100:.0f}%)" if total_active_days else "")

        # Key metrics
        print(f"\n  ── Key Metrics ──")
        if duration_days > 0:
            print(f"  Daily avg PnL:     ${total_return/duration_days:,.4f}")
            print(f"  Annualized return: {return_pct * 365/duration_days:+.1f}%")
            avg_fill_per_day = self.fills / duration_days
            print(f"  Fills/day:         {avg_fill_per_day:.1f}")

        # Verdict
        print(f"\n  {'✅ Grid captured profit' if total_return > 0 else '❌ Grid lost money'}")
        if total_return > 0:
            print(f"     Outperformed B&H? {'✅ YES' if return_pct > hold_return else '❌ NO'}")
        print("=" * 55)


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BTC Grid Bot Backtester")
    parser.add_argument("--days", type=int, default=30, help="Days of historical data (default: 30)")
    parser.add_argument("--buys", type=int, default=5, help="Number of buy levels (default: 5)")
    parser.add_argument("--sells", type=int, default=3, help="Number of sell levels (default: 3)")
    parser.add_argument("--equity", type=float, default=1000, help="Starting equity in USDC (default: 1000)")
    parser.add_argument("--interval", type=str, default="1H", help="Candle interval: 1m, 5m, 15m, 30m, 1H, 2H, 4H, 1D")
    parser.add_argument("--spacing", type=float, default=0.5, help="Grid spacing as fraction of ATR (default: 0.5)")
    parser.add_argument("--fee", type=float, default=0.02, help="Maker fee % (default: 0.02)")
    parser.add_argument("--save", type=str, default=None, help="Save candles to file path")
    parser.add_argument("--load", type=str, default=None, help="Load candles from file instead of fetching")
    args = parser.parse_args()

    if args.load:
        print(f"Loading candles from {args.load}...")
        with open(args.load) as f:
            candles = json.load(f)
        print(f"  Loaded {len(candles)} candles")
    else:
        candles = download_candles(
            inst_id="BTC-USDT-SWAP",
            bar=args.interval,
            days=args.days,
            save_path=args.save
        )

    if not candles:
        print("❌ No candles downloaded!")
        sys.exit(1)

    engine = BacktestEngine(
        equity=args.equity,
        num_buy=args.buys,
        num_sell=args.sells,
        fee_pct=args.fee / 100,
        spacing_atr=args.spacing,
    )

    engine.run(candles)


if __name__ == "__main__":
    main()
