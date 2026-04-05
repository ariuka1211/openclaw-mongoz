#!/usr/bin/env python3
"""
BTC Grid Bot Stress Test Framework

Runs synthetic price paths against a simulated grid bot engine.
No real API calls. Uses actual calculate_grid() from core.calculator.

Usage:
  cd projects/btc-grid-bot && python3 tools/stress_test.py
"""

import sys
import os
import math
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from core.calculator import calculate_grid

# ── Load config ─────────────────────────────────────────────────
CONFIG_PATH = PROJECT_ROOT / "config.yml"
with open(CONFIG_PATH) as f:
    CFG = yaml.safe_load(f)

EQUITY = CFG["capital"]["starting_equity"]
MAX_EXPOSURE = CFG["capital"]["max_exposure_multiplier"]
MARGIN_RESERVE = CFG["capital"]["margin_reserve_pct"]
DAILY_LOSS_PCT = CFG["risk"]["daily_loss_limit_pct"]
TRAILING_PCT = CFG["risk"].get("trailing_loss_pct", 0.04)
DEFAULT_BTC = 84000
POLL_SECONDS = 30

# ═══════════════════════════════════════════════════════════════
#  Simulated Exchange
# ═══════════════════════════════════════════════════════════════

class Exchange:
    """Tracks orders at prices, equity, and detects when price crosses levels."""

    def __init__(self, equity: float, price: float):
        self.starting_equity = equity
        self.usdc = equity          # liquid USDC (not counting positions)
        self.position_btc = 0.0     # BTC held from fills
        self.price = price
        self.orders: list[dict] = []   # {id, side, price, size}
        self.id_counter = 1
        self.fills_this_step: list[dict] = []

    @property
    def equity(self) -> float:
        """Total equity: USDC + BTC valued at current mark."""
        return self.usdc + self.position_btc * self.price

    @equity.setter
    def equity(self, _val):
        pass  # no-op, equity is computed

    def place(self, side: str, price: float, size: float) -> dict:
        o = {"id": self.id_counter, "side": side, "price": price, "size": size}
        self.orders.append(o)
        self.id_counter += 1
        return o

    def cancel(self, order_id: int):
        self.orders = [o for o in self.orders if o["id"] != order_id]

    def cancel_all(self) -> int:
        n = len(self.orders)
        self.orders.clear()
        return n

    def step(self, new_price: float):
        """Advance price, return list of fills from this step."""
        old_price = self.price
        self.price = new_price
        self.fills_this_step = []

        remaining = []
        for o in self.orders:
            filled = False
            if o["side"] == "buy" and new_price <= o["price"]:
                filled = True
            elif o["side"] == "sell" and new_price >= o["price"]:
                filled = True
            if filled:
                self.fills_this_step.append(o.copy())
            else:
                remaining.append(o)
        self.orders = remaining
        return self.fills_this_step


# ═══════════════════════════════════════════════════════════════
#  Grid Bot Simulator (mirrors GridManager logic)
# ═══════════════════════════════════════════════════════════════

class BotSim:
    """
    Simulates the grid: deploy → poll → fill → replace → roll → pause.
    Uses real calculate_grid() for capital checks.
    Tracks equity from realized trades.
    """

    def __init__(self, exch: Exchange, cfg: dict, start_price: float,
                 num_buy: int, num_sell: int, size: float,
                 buy_levels: list, sell_levels: list):
        self.exch = exch
        self.cfg = cfg
        self.start_price = start_price
        self.num_buy = num_buy
        self.num_sell = num_sell
        self.size = size
        self.buy_levels = list(buy_levels)
        self.sell_levels = list(sell_levels)
        self.range_low = min(buy_levels) if buy_levels else start_price - 2000
        self.range_high = max(sell_levels) if sell_levels else start_price + 2000

        # State
        self.active = True
        self.paused = False
        self.pending_buys: list[dict] = []  # {price, size, minute}
        self.realized_pnl = 0.0
        self.completed_trades: list[dict] = []
        self.roll_count = 0
        self.eff_stop = None  # effective stop price (trailing)
        self.peak_equity = exch.equity
        self.pause_minute = None
        self.stop_minute = None
        self.all_fills: list[dict] = []
        self.fill_log: list[dict] = []

    # ── Deploy ──────────────────────────────────────────────────

    def place_orders(self):
        """Place all buy and sell orders on the exchange."""
        for p in self.buy_levels:
            self.exch.place("buy", p, self.size)
        for p in self.sell_levels:
            self.exch.place("sell", p, self.size)

    # ── Poll cycle ──────────────────────────────────────────────

    def tick(self, price: float, minute: float):
        """One poll cycle: check fills, process, replace, safety."""
        if self.paused:
            return

        # Step exchange price → returns fills
        fills = self.exch.step(price)

        for f in fills:
            self._process_fill(f, minute)

        # Safety checks
        self._check_trailing_stop(minute)
        self._check_pause(minute)

    def _process_fill(self, fill: dict, minute: float):
        """Process a single fill per GridManager fill logic.
        
        Correct USDC accounting:
        - Buy fill: spend USDC, acquire BTC
        - Sell fill (matching buy): sell BTC, receive USDC, record realized PnL
        - Sell fill (no match): unexpected in long grid, skip PnL
        """
        side = fill["side"]
        price = fill["price"]
        size = fill["size"]
        cost = price * size  # dollar value of this fill

        self.all_fills.append({**fill, "minute": minute, "fill_at": self.exch.price})

        if side == "buy":
            # Buy filled: SPEND USDC, acquire BTC
            self.exch.usdc -= cost
            self.exch.position_btc += size

            self.pending_buys.append({"price": price, "size": size, "minute": minute})

            # Replace with a new buy one level lower
            new_price = price - self._level_spacing()
            if new_price > self.range_low * 0.50:  # reasonable floor
                self.exch.place("buy", round(new_price / 50) * 50, size)

        elif side == "sell":
            # Sell filled: only meaningful if we have BTC from a previous buy
            if self.pending_buys and self.exch.position_btc >= size:
                buy = self.pending_buys.pop(0)
                pnl = (price - buy["price"]) * buy["size"]
                self.realized_pnl += pnl
                self.completed_trades.append({
                    "buy_price": buy["price"],
                    "sell_price": price,
                    "size": buy["size"],
                    "pnl": round(pnl, 2),
                    "held_minutes": round(minute - buy["minute"], 1),
                })
                # Release the BTC and receive USDC
                self.exch.position_btc -= buy["size"]
                self.exch.usdc += cost
            elif self.exch.position_btc >= size:
                # Sell existing BTC (not from grid buy) - no PnL tracking
                self.exch.position_btc -= size
                self.exch.usdc += cost
            # else: sell order filled but no BTC to sell = invalid, ignore

            # Replace with a new sell one level higher
            new_price = price + self._level_spacing()
            if new_price < self.range_high * 1.50:  # reasonable ceiling
                self.exch.place("sell", round(new_price / 50) * 50, size)

        # Equity is automatically recomputed via property

    def _level_spacing(self) -> float:
        return (self.range_high - self.range_low) / (self.num_buy + self.num_sell)

    # ── Safety: trailing stop ───────────────────────────────────

    def _check_trailing_stop(self, minute: float):
        if self.stop_minute is not None:
            return
        starting = self.exch.starting_equity
        current_equity = self.exch.equity
        # Update peak tracking
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        
        # Only trigger if we're actually losing money (equity < starting)
        if current_equity >= starting:
            return  # no losses yet
            
        # trailing stop: max loss from peak
        drawdown_from_peak = (self.peak_equity - current_equity) / self.peak_equity
        daily_loss = (starting - current_equity) / starting
        
        if drawdown_from_peak >= TRAILING_PCT or daily_loss >= DAILY_LOSS_PCT:
            self.stop_minute = minute
            self.paused = True

    # ── Safety: pause (price out of range) ──────────────────────

    def _check_pause(self, minute: float):
        if self.pause_minute is not None:
            return
        rng = self.range_high - self.range_low
        # Generous buffer: price must be more than 2x the range outside before pausing
        # This prevents premature pauses on normal volatility
        if self.num_fill_rate() > 0.5:
            # If we're filling a lot, the grid is working — don't pause
            pass
        # Hard exit: price is more than 1 range width outside
        if self.exch.price < self.range_low - rng * 2:
            self.pause_minute = minute
            self.paused = True
        elif self.exch.price > self.range_high + rng * 2:
            self.pause_minute = minute
            self.paused = True

    def num_fill_rate(self) -> float:
        """What fraction of orders are still open? High = many still pending."""
        if not self.buy_levels and not self.sell_levels:
            return 0
        total_expected = len(self.buy_levels) + len(self.sell_levels)
        if total_expected == 0:
            return 0
        # Rough estimate: pending_buys + open sell orders / max expected
        current_open = len(self.exch.orders)
        return current_open / max(total_expected, 1)

    # ── Roll ────────────────────────────────────────────────────

    def try_roll(self, minute: float):
        """Roll grid when price is near the band edge."""
        if self.paused:
            return False

        rng = self.range_high - self.range_low
        buffer = rng * 0.05  # 5% of range

        shifted = False
        if self.exch.price >= self.range_high - buffer:
            # Shift up
            shift = rng * 0.3
            self.range_low += shift
            self.range_high += shift
            shifted = True
        elif self.exch.price <= self.range_low + buffer:
            # Shift down
            shift = rng * 0.3
            self.range_low -= shift
            self.range_high -= shift
            shifted = True

        if not shifted:
            return False

        self.roll_count += 1
        # Cancel all and redeploy at new levels
        self.exch.cancel_all()
        self.pending_buys.clear()

        spacing = rng / (self.num_buy + self.num_sell)
        self.buy_levels = [round(self.range_low + (i + 1) * spacing, 0)
                          for i in range(self.num_buy)]
        self.sell_levels = [round(self.range_high - (self.num_sell - i) * spacing, 0)
                           for i in range(self.num_sell)]
        # Filter: buys must be below price, sells above
        self.buy_levels = [p for p in self.buy_levels if p < self.exch.price]
        self.sell_levels = [p for p in self.sell_levels if p > self.exch.price]
        if not self.buy_levels or not self.sell_levels:
            # Can't deploy meaningful levels, don't roll
            return False
        self._compute_size()
        self.place_orders()
        return True

    def _compute_size(self):
        """Re-run capital check and update size if needed."""
        calc = calculate_grid(
            account_equity=self.exch.equity,
            btc_price=self.exch.price,
            num_buy_levels=self.num_buy,
            num_sell_levels=self.num_sell,
            max_exposure_mult=MAX_EXPOSURE,
            margin_reserve_pct=MARGIN_RESERVE,
        )
        if calc["safe"]:
            self.size = calc["size_per_level"]

    # ── Report ──────────────────────────────────────────────────

    def report(self, scenario: str, desc: str) -> dict:
        starting = self.exch.starting_equity
        final_equity = self.exch.equity
        pnl = final_equity - starting
        pnl_pct = (pnl / starting * 100) if starting else 0
        dd = max(0, starting - final_equity)
        dd_pct = (dd / starting * 100) if starting else 0

        trades = self.completed_trades
        wins = sum(1 for t in trades if t["pnl"] >= 0)
        losses = sum(1 for t in trades if t["pnl"] < 0)
        win_rate = (wins / len(trades) * 100) if trades else 0

        # Stranded buys (filled buys that never got to sell)
        stranded = len(self.pending_buys)

        return {
            "scenario": scenario,
            "desc": desc,
            "starting_equity": starting,
            "final_equity": self.exch.equity,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 1),
            "max_drawdown": round(dd, 2),
            "max_drawdown_pct": round(dd_pct, 1),
            "total_fills": len(self.all_fills),
            "completed_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "stranded_buys": stranded,
            "grid_rolls": self.roll_count,
            "paused": self.pause_minute is not None,
            "pause_minute": self.pause_minute,
            "trailing_stop_hit": self.stop_minute is not None,
            "stop_minute": self.stop_minute,
            "trades": trades,
        }


# ═══════════════════════════════════════════════════════════════
#  Reporting
# ═══════════════════════════════════════════════════════════════

def print_report(r: dict):
    sep = "═" * 50
    print()
    print(sep)
    print(f"  {r['desc']}")
    print(sep)
    print(f"  Starting Equity:    ${r['starting_equity']:,.2f}")
    print(f"  Final Equity:       ${r['final_equity']:,.2f}")
    print(f"  Realized PnL:       {r['pnl']:+,.2f} ({r['pnl_pct']:+.1f}%)")
    print(f"  Max Drawdown:       ${r['max_drawdown']:,.2f} ({r['max_drawdown_pct']:.1f}%)")
    print(f"  Total Fills:        {r['total_fills']}")
    print(f"  Completed Trades:   {r['completed_trades']} ({r['wins']}W / {r['losses']}L)")
    win_emoji = "🎯" if r['win_rate'] >= 50 else "—"
    print(f"  Win Rate:           {win_emoji} {r['win_rate']:.0f}%")
    print(f"  Stranded Buys:      {r['stranded_buys']}")
    print(f"  Grid Rolls:         {r['grid_rolls']}")
    if r["trailing_stop_hit"]:
        print(f"  Trailing Stop:      🔴 HIT at minute {r['stop_minute']:.0f}")
    else:
        print(f"  Trailing Stop:      ✅ Not triggered")
    if r["paused"]:
        print(f"  Paused:             ⏸ at minute {r['pause_minute']:.0f}")
    else:
        print(f"  Paused:             No")

    # Show trade detail if any
    if r["trades"]:
        print()
        print("  Trade Detail:")
        for i, t in enumerate(r["trades"][:10]):
            emoji = "✅" if t["pnl"] >= 0 else "❌"
            print(f"    {emoji} #{i+1}: Buy ${t['buy_price']:,.0f} → Sell ${t['sell_price']:,.0f} | {t['size']:.6f} BTC | PnL {t['pnl']:+.2f} | Held {t['held_minutes']:.0f}m")
        if len(r["trades"]) > 10:
            print(f"    ... and {len(r['trades']) - 10} more trades")
    print(sep)


def print_summary(reports: list):
    print()
    print("═" * 100)
    print("  STRESS TEST SUMMARY")
    print("═" * 100)
    hdr = f"  {'Scenario':<42} {'PnL':>8} {'PnL%':>7} {'DD%':>6} {'Fills':>6} {'Trades':>7} {'Win%':>6} {'Rolls':>5} {'Status':>14}"
    print(hdr)
    print("─" * 100)
    for r in reports:
        pnl_str = f"${r['pnl']:+,.0f}"
        pnl_pct_str = f"{r['pnl_pct']:+.1f}%"
        dd_str = f"{r['max_drawdown_pct']:.1f}%"
        rolls = r["grid_rolls"]
        if r["trailing_stop_hit"]:
            status = "🔴 STOP HIT"
        elif r["paused"]:
            status = f"⏸ {r['pause_minute']:.0f}m"
        else:
            status = "✅ Clean"
        print(f"  {r['desc']:<42} {pnl_str:>8} {pnl_pct_str:>7} {dd_str:>6} "
              f"{r['total_fills']:>6} {r['completed_trades']:>7} {r['win_rate']:>5.0f}% "
              f"{rolls:>5} {status:>14}")
    print("═" * 100)


# ═══════════════════════════════════════════════════════════════
#  Scenario definitions
# ═══════════════════════════════════════════════════════════════

SCENARIOS = [
    ("normal_chop", "📊 Normal Chop ±2.5% · 12h"),
    ("crash",       "📉 Crash -15% in 2h → chop"),
    ("pump",        "📈 Pump +10% in 3h → chop"),
    ("sawtooth",    "⚡ Sawtooth ±3% every 30m · 8h"),
    ("slow_bleed",  "🩸 Slow Bleed -0.7%/hr · 14h"),
    ("flash",       "💥 Flash Crash -12% in 15m → recover"),
]


def gen_normal_chop(start=DEFAULT_BTC):
    """±2.5% chop for 12h, mean-reverting."""
    path = []
    price = start
    for i in range(int(12 * 60 / 0.5)):
        t = i * 0.5
        mean_rev = (start - price) / start * 0.02
        noise = random.gauss(0, start * 0.003)
        price += noise + mean_rev * start
        price = max(start * 0.975, min(start * 1.025, price))
        path.append((round(price, 2), t))
    return path


def gen_crash(start=DEFAULT_BTC):
    """-15% in 2h → 6h chop at bottom."""
    path = []
    price = start
    # Crash phase
    for i in range(int(120 / 0.5)):
        t = i * 0.5
        target = start * 0.85
        price += (target - price) * 0.05 + random.gauss(0, start * 0.002)
        path.append((round(price, 2), t))
    # Chop at bottom
    bottom = price
    for i in range(int(360 / 0.5)):
        t = 120 + i * 0.5
        price = bottom + random.gauss(0, bottom * 0.005)
        price = max(bottom * 0.99, min(bottom * 1.01, price))
        path.append((round(price, 2), t))
    return path


def gen_pump(start=DEFAULT_BTC):
    """+10% in 3h → 7h chop at top."""
    path = []
    price = start
    for i in range(int(180 / 0.5)):
        t = i * 0.5
        target = start * 1.10
        price += (target - price) * 0.03 + random.gauss(0, start * 0.003)
        path.append((round(price, 2), t))
    peak = price
    for i in range(int(420 / 0.5)):
        t = 180 + i * 0.5
        price = peak + random.gauss(0, peak * 0.007)
        price = max(peak * 0.985, min(peak * 1.015, price))
        path.append((round(price, 2), t))
    return path


def gen_sawtooth(start=DEFAULT_BTC):
    """±3% every 30min for 8h."""
    path = []
    cycle = 30
    for i in range(int(8 * 60 / 0.5)):
        t = i * 0.5
        pos = (t % cycle) / cycle
        if pos < 0.25:
            swing = start * 0.03 * (pos * 4)
        elif pos < 0.50:
            swing = start * 0.03 * (2 - pos * 4)
        elif pos < 0.75:
            swing = start * 0.03 * ((pos - 0.5) * 4 - 1)
        else:
            swing = start * 0.03 * (1 - (pos - 0.75) * 4)
        price = start + swing + random.gauss(0, start * 0.001)
        path.append((round(price, 2), t))
    return path


def gen_slow_bleed(start=DEFAULT_BTC):
    """-0.7%/hr for 14h."""
    path = []
    price = start
    for i in range(int(14 * 60 / 0.5)):
        t = i * 0.5
        price *= (1 - 0.007 / 120)
        price += random.gauss(0, price * 0.001)
        path.append((round(price, 2), t))
    return path


def gen_flash(start=DEFAULT_BTC):
    """-12% in 15m → recover to start in 2h → chop."""
    path = []
    price = start
    # Flash crash
    for i in range(int(15 / 0.5)):
        t = i * 0.5
        target = start * 0.88
        price += (target - price) * 0.15 + random.gauss(0, start * 0.005)
        path.append((round(price, 2), t))
    # Recovery
    bottom = price
    for i in range(int(105 / 0.5)):
        t = 15 + i * 0.5
        target = start
        price += (target - price) * 0.03 + random.gauss(0, start * 0.004)
        path.append((round(price, 2), t))
    # Chop
    for i in range(int(180 / 0.5)):
        t = 120 + i * 0.5
        price = start + random.gauss(0, start * 0.004)
        price = max(start * 0.98, min(start * 1.02, price))
        path.append((round(price, 2), t))
    return path


SCENARIO_GENERATORS = {
    "normal_chop": gen_normal_chop,
    "crash": gen_crash,
    "pump": gen_pump,
    "sawtooth": gen_sawtooth,
    "slow_bleed": gen_slow_bleed,
    "flash": gen_flash,
}


# ═══════════════════════════════════════════════════════════════
#  Run a scenario
# ═══════════════════════════════════════════════════════════════

def run_scenario(name: str, desc: str) -> dict:
    random.seed(42 + hash(name) % 1000)

    path = SCENARIO_GENERATORS[name]()
    start_price = path[0][0]

    # Compute ATR-ish from the path
    diffs = [abs(path[i][0] - path[i-1][0]) for i in range(1, min(len(path), 100))]
    atr = sum(diffs) / len(diffs) if diffs else start_price * 0.005
    atr_pct = atr / start_price

    # Use real calculator for sizing
    calc = calculate_grid(
        account_equity=EQUITY,
        btc_price=start_price,
        num_buy_levels=3,
        num_sell_levels=3,
        max_exposure_mult=MAX_EXPOSURE,
        margin_reserve_pct=MARGIN_RESERVE,
        atr_pct=atr_pct,
        vol_cfg=CFG.get("volatility", {}),
    )

    if not calc["safe"]:
        print(f"  ⚠️  Safety check warning: {calc['reason']}")
        # Use adjusted levels if available
        n_buy = calc.get("adjusted_num_buy_levels") or 3
        n_sell = calc.get("adjusted_num_sell_levels") or 3
    else:
        n_buy = 3
        n_sell = 3

    size = calc["size_per_level"]

    # Generate levels around start price - realistic grid spacing
    base_spacing = max(atr * 5, start_price * 0.025)  # minimum 2.5% spacing for BTC
    buy_levels = sorted([round(start_price - (i + 1) * base_spacing, -1) for i in range(n_buy)])
    sell_levels = sorted([round(start_price + (i + 1) * base_spacing, -1) for i in range(n_sell)])

    # Set up exchange and bot
    exch = Exchange(EQUITY, start_price)
    bot = BotSim(exch, CFG, start_price, n_buy, n_sell, size, buy_levels, sell_levels)
    bot.place_orders()

    # Run poll cycles
    next_roll_minute = 60  # first roll check at 60 minutes
    for price, minute in path:
        if bot.paused:
            break

        bot.tick(price, minute)

        # Roll check every 60 minutes
        if minute >= next_roll_minute and not bot.paused:
            bot.try_roll(minute)
            next_roll_minute += 60

    return bot.report(name, desc)


# ═══════════════════════════════════════════════════════════════
#  Calculator edge cases
# ═══════════════════════════════════════════════════════════════

def test_calculator():
    print()
    print("═" * 55)
    print("  CALCULATOR EDGE CASE TESTS")
    print("═" * 55)

    cases = [
        ("Dust equity ($100)", 100, 84000, 3, 3),
        ("Large equity ($10K)", 10000, 84000, 3, 3),
        ("Low BTC ($30K)", 1000, 30000, 3, 3),
        ("High BTC ($150K)", 1000, 150000, 3, 3),
        ("Low ATR (0.5%)", 1000, 84000, 3, 3, 0.005),
        ("Extreme ATR (5%)", 1000, 84000, 3, 3, 0.05),
        ("Too many levels (16)", 1000, 84000, 8, 8),
        ("Tiny equity ($50)", 50, 84000, 3, 3),
    ]

    results = []
    for c in cases:
        label, eq, px, buy, sell = c[0], c[1], c[2], c[3], c[4]
        atr = c[5] if len(c) > 5 else None
        calc = calculate_grid(
            account_equity=eq, btc_price=px,
            num_buy_levels=buy, num_sell_levels=sell,
            max_exposure_mult=MAX_EXPOSURE,
            margin_reserve_pct=MARGIN_RESERVE,
            atr_pct=atr,
            vol_cfg=CFG.get("volatility", {}),
        )
        status = "✅ SAFE" if calc["safe"] else "❌ UNSAFE"
        results.append((label, status, calc["size_per_level"],
                       calc["size_per_level_usd"], calc["worst_case_notional"],
                       calc["available_notional"]))

    for label, status, size, usd, worst, avail in results:
        ok = "✅" if worst <= avail else "⚠️"
        print(f"\n  {label}: {status}")
        print(f"    {ok} Size/level: {size:.6f} BTC (${usd:,.0f}) | "
              f"Worst: ${worst:,.0f} / Available: ${avail:,.0f}")

    return results


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def main():
    print()
    print("╔" + "═" * 53 + "╗")
    print("║  BTC Grid Bot — Stress Test Suite" + " " * 13 + "║")
    print("║  No real API calls. Pure simulation." + " " * 11 + "║")
    print("╚" + "═" * 53 + "╝")
    print(f"\n  Config: equity=${EQUITY}, max {MAX_EXPOSURE}x exposure, "
          f"{TRAILING_PCT:.0%} trailing stop")

    print(f"\n  ⚙️  Each scenario deploys a grid, simulates {POLL_SECONDS}s polls,")
    print(f"  tracks fills → replacements → realized PnL → equity changes.\n")

    # Edge cases
    test_calculator()

    # Scenarios
    reports = []
    for name, desc in SCENARIOS:
        print(f"\nRunning: {desc} ... ", end="", flush=True)
        r = run_scenario(name, desc)
        print("done.")
        print_report(r)
        reports.append(r)

    # Summary
    print_summary(reports)

    # Key findings
    print()
    print("═" * 75)
    print("  KEY ANSWERS")
    print("═" * 75)

    for r in reports:
        lines = []
        if r["trailing_stop_hit"]:
            lines.append(f"🔴 Trailing stop HIT at minute {r['stop_minute']:.0f} — bot saved from worse")
        else:
            lines.append("✅ Trailing stop did NOT trigger")
        if r["paused"]:
            lines.append(f"⏸ Pause at minute {r['pause_minute']:.0f} — grid exited range")
        else:
            lines.append("✅ Grid never paused (handled the move)")
        total_pnl = r["pnl"]
        if total_pnl < 0:
            lines.append(f"📉 Net loss: ${total_pnl:,.2f} ({r['pnl_pct']:.1f}%)")
        elif total_pnl > 0:
            lines.append(f"📈 Net profit: ${total_pnl:,.2f} ({r['pnl_pct']:.1f}%)")
        else:
            lines.append("➡ Flat — no meaningful fills")
        if r["stranded_buys"] > 0:
            lines.append(f"⚠️ {r['stranded_buys']} stranded buy(s) — bought but never sold")

        print(f"\n  {r['desc']}:")
        for l in lines:
            print(f"    • {l}")

    print()
    print("=" * 75)
    print("  Done.")
    print("=" * 75)


if __name__ == "__main__":
    main()
