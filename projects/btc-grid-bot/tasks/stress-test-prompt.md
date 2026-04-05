# TASK: Build BTC Grid Bot Stress Test Framework

## Goal
Create a standalone Python script at `projects/btc-grid-bot/tools/stress_test.py` that simulates the grid bot against synthetic BTC price paths WITHOUT touching the real exchange.

## CONTEXT (CRITICAL — read carefully)

The grid bot is at `/root/.openclaw/workspace/projects/btc-grid-bot/`. Key files:
- `core/calculator.py` — `calculate_grid()` function determines sizing + safety. Pure function, no network.
- `core/grid_manager.py` — `GridManager` class. Handles deploy, check_fills, replace orders, rolls, pauses. Depends on `LighterAPI` for all exchange calls.
- `api/lighter.py` — `LighterAPI` class. Real API to Lighter DEX. Complex proxy setup.

For stress tests, you must **NOT** import or use the real `LighterAPI`. You build a `MockAPI` instead.

## DESIGN

### 1. MockLighterAPI
Simulates the exchange interface needed by GridManager:
- `get_equity()` — returns current simulated equity
- `get_btc_price()` — returns the current synthetic price
- `get_open_orders()` — returns simulated open orders
- `place_limit_order(side, price, size)` — adds order to simulated order book
- `cancel_order(order_id)` — removes order
- `cancel_all_orders()` — clears all
- `close()` — no-op
- `get_btc_balance()` — returns 0.0

Fill detection: An order "fills" when the synthetic price crosses it.
- Buy order fills when price goes <= order price
- Sell order fills when price goes >= order price

Track filled orders' PnL.

### 2. Synthetic Price Paths
Generate these as lists of (price, timestamp_offset_minutes):

**a) Normal Chop** — BTC at $84,000, oscillates ±2.5% over 12 hours, mean-reverting. ~4-6% daily range.

**b) Crash** — Start at $84,000, -15% in 2 hours (sharp drop), then chop ±1% at bottom for 6 hours. Tests: does the pause logic kick in? Does the trailing stop trigger?

**c) Pump** — Start at $84,000, +10% in 3 hours, then chop ±1.5% for 7 hours. Tests: grid gets left below price, rolls upward.

**d) Sawtooth/Whipsaw** — Start at $84,000, rapid 3% swings every 30 minutes for 8 hours. Tests: maximum fill count, PnL generation.

**e) Slow Bleed** — Start at $84,000, -0.7% per hour for 14 hours. Tests: slow trend death — few fills, equity drains from filled buys with no sells.

**f) Flash Crash + Recovery** — Start at $84,000, -12% in 15 minutes, then rapid recovery to $84,000 over 2 hours, then chop. Tests: worst case.

### 3. Simulation Engine
The engine simulates the bot's deploy → poll → check_fills loop:
1. Run the full `startup()` logic (but using MockAPI and a fixed analyst output instead of LLM)
2. Deploy the grid at the starting price
3. Step through synthetic prices (one price = one poll cycle, every 30 seconds)
4. At each step: call `check_fills(price)` on the GridManager with the MockAPI
5. Track: equity changes, fills, PnL, drawsdown, rolls, pauses

For the AI analyst part: **don't call the LLM**. Use the `GridManager.generate_levels_from_volume_profile()` method (which is already deterministic, using Bollinger Bands fallback) OR provide pre-computed levels based on the starting price:
- For a grid at ~$84,000: buy_levels = [82000, 82800, 83500], sell_levels = [84500, 85200, 86000]
- Or compute from: price ± ATR * range.

### 4. Report Output
After each scenario, print a clean report:

```
═══════════════════════════════════════
  STRESS TEST: 📉 Crash (-15%)
═══════════════════════════════════════
  Starting Equity:     $1,000.00
  Final Equity:        $942.30
  PnL:                 -$57.70 (-5.8%)
  Max Drawdown:        -$89.10 (-8.9%)
  Total Fills:         8
  Completed Trades:    3
  Win Rate:            66.7%
  Grid Rolls:          1
  Paused:              YES (at minute 124)
  Trailing Stop:       HIT (at minute 118)
═══════════════════════════════════════
```

Final summary table across all scenarios.

### 5. Test the Calculator Too
Test `calculate_grid()` with edge cases:
- Equity = $100 (dust — should fail safety)
- Equity = $10,000 (size should scale)
- BTC at $30,000 vs $150,000
- ATR = 0.5% (low vol) vs 5% (extreme vol)
- Num levels = 16 (too many — should fail)

## CONSTRAINTS

- **Pure Python, no external libs beyond what the project already uses** (yaml, dotenv, pathlib, json, asyncio)
- **Must be runnable standalone**: `cd projects/btc-grid-bot && python tools/stress_test.py`
- **Must NOT connect to any API** — fully offline
- **Must use the ACTUAL `calculate_grid()` from `core/calculator.py`** — don't reimplement it
- **Mock the GridManager's API layer** but use the REAL `GridManager` class for fill detection and order logic where possible, OR build a minimal simulator that follows the same logic
- The GridManager depends heavily on the real API and async calls. The safest approach may be to:
  a) Build a `SimulatedGridManager` that inherits from or copies the core loop logic
  b) Use the MockAPI to satisfy the interface
  c) For `check_fills()` to work: the MockAPI needs to track filled orders and remove them when price crosses

## RECOMMENDED APPROACH

Given the complexity, here's the cleanest path:

1. Create `tools/stress_test.py` as a single file
2. Define `SimulatedExchange` that tracks: orders (open/filled), equity, current price
3. The simulation loop iterates through price points
4. At each point, check which orders fill (price crosses), compute PnL, replace orders per grid rules
5. Apply the grid manager's logic for: pause (price exits range), roll (rolling grid), trailing stop
6. Read `config.yml` for the same parameters the live bot uses

The simulation doesn't need to be byte-for-byte identical to `GridManager` — it needs to test the SAME LOGIC: grid deployment, fill handling, replacement, pause, roll, trailing stop.

## SPECIFIC QUESTIONS THE STRESS TEST SHOULD ANSWER

For each scenario, the report should make these easy to see:
1. Did the trailing stop trigger BEFORE liquidation?
2. Did the pause logic actually save us from chasing?
3. How many round-trip trades completed vs stranded buys?
4. What was the worst single-day PnL impact?
5. Is the capital calculator correctly preventing overexposure?
6. Does the rolling grid work or create chaos?

## FILE TO WRITE
`/root/.openclaw/workspace/projects/btc-grid-bot/tools/stress_test.py`

## DO NOT
- Modify any existing source files
- Import the real LighterAPI
- Use any API calls
- Add new dependencies to requirements
- Touch config.yml
