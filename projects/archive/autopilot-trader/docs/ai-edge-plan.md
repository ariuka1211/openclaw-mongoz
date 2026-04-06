# AI Autopilot Edge-Finding Plan

*2026-03-29 — diagnostic session*

---

## Current State (the problem)

The AI layer is a pure function: `top_signal → trade → DSL exits`. After 186 trades:
- **48.4% win rate** (coin flip)
- **All profit from 1 outlier day** (Mar 26: +19.57, everything else flat/negative)
- **1 pattern learned** (below threshold, never used)
- **Scanner scores don't correlate with outcomes** — score 82 wins as often as score 65
- The prompt literally says "trust the scores" — so the AI never doubts bad signals

**Why the self-improvement doesn't work:**
The pattern engine needs 60%+ win rate to reinforce anything. At 48%, everything decays to zero. It's a cold-start trap: no patterns → no edge → no wins → no patterns.

---

## The Five Things That Would Create Edge

### 1. Historical Signal Backtest ("Did This Ever Work?")

**What:** Before trading any signal, look up past outcomes for similar signals.

**How it works:**
- Store every scanner signal (not just traded ones) with: score, direction, funding rate, OI trend, momentum, volatility, timestamp
- When a position closes, record: entry signal features, exit result, hold time, max adverse excursion
- Build a lookup: "When the scanner scored ETH long above 75 with negative funding and low vol, how often did it win in the last 30 days?"
- If historical win rate for this signal type is <50%, skip it regardless of current score

**Data needed:**
- Signals database (already exists but only stores traded signals — need ALL signals)
- Price snapshots at signal time + 15/30/60/120 min after
- Already have outcomes table — just need to link signals → outcomes by features

**Effort:** ~1 day. Add signal logging, add price follow-up tracking, add lookup in decision prompt.

**Why this alone could fix everything:** If the scanner generates 200 signals/day and 40% are winners, but the AI currently picks randomly among them, a simple filter ("only trade signal types with >55% historical win rate") would shift the whole curve.

---

### 2. Market Regime Detection

**What:** Classify current market state before trading.

**Regimes to detect:**
- **Trending up** — higher highs, price above key MAs, positive momentum
- **Trending down** — lower lows, price below key MAs, negative momentum
- **Ranging** — price bouncing between levels, low ATR expansion
- **High volatility** — ATR spiking, wide ranges, news events
- **Low volatility** — tight ranges, low volume, coiling

**How to use it:**
- In trending regimes: trade WITH the trend only (skip counter-trend signals)
- In ranging regimes: trade reversals at range boundaries, skip breakout signals
- In high vol: widen stops, reduce size
- In low vol: skip entirely (nothing moves, you just pay funding)

**Implementation:**
- Calculate 1h/4h ATR, compare to 20-period average
- Check price vs EMA(20) vs EMA(50) alignment
- Check if last N candles are making higher/lower highs
- Feed regime label into the AI prompt

**Effort:** ~4-6 hours. The scanner already has moving average alignment and momentum — just needs a regime classifier on top.

---

### 3. Entry Timing Filter ("Am I Late?")

**What:** Detect if you're chasing vs entering early.

**The problem:** Scanner fires a "short" signal on the 5th consecutive red candle. Price already dumped 4%. You enter, it bounces, you lose. This is the most common loss pattern in momentum-based systems.

**How to detect:**
- Count consecutive candles in signal direction (last 3-5 candles)
- Check RSI — if already oversold (<30) on a short signal, you're late
- Check if price just hit a key level (support for shorts, resistance for longs)
- Check distance from recent swing high/low

**How to use:**
- If "late" by any metric: skip the signal, or reduce size by 50%
- If "early" (first candle of a move, RSI neutral): full size

**Effort:** ~3-4 hours.

---

### 4. Position Correlation & Net Exposure

**What:** The AI should understand that 5 shorts = one big directional bet.

**Current problem:** Bot enters 5 shorts, market rips, all 5 lose at once. The AI treats each position independently.

**How to fix:**
- Calculate net exposure: (long notional - short notional) / total equity
- If net short > 60%: block new shorts, only allow longs
- If net long > 60%: block new longs, only allow shorts
- Show the AI its net bias in the prompt: "You are currently 70% net short — market can squeeze you"

**Effort:** ~2 hours. Mostly a safety layer addition.

---

### 5. Post-Entry Monitoring & Active Management

**What:** The AI should re-evaluate positions after entry, not just wait for DSL.

**Current flow:**
1. AI opens position
2. DSL manages exit
3. AI never thinks about it again

**Better flow:**
1. AI opens position with a thesis: "ETH long, score 79, expecting +1% in 2h based on funding + momentum"
2. 30 min later: "ETH moved +0.2%, on track. Hold."
3. 60 min later: "ETH flat, momentum fading. Thesis weakening. Tighten stop or close?"
4. 120 min later: "ETH hasn't moved. Thesis failed. Close at small loss."

**Implementation:**
- Store the entry thesis with each position
- Every cycle, the AI sees: "Your ETH long thesis was +1% in 2h. It's been 90 min, price moved -0.1%. Do you still believe?"
- Add a "thesis_score" that decays over time — if price doesn't confirm, cut it

**Effort:** ~1 day. Needs position-level thesis tracking and re-evaluation logic.

---

## The Minimum Viable Edge Test

Don't build all 5 at once. Start here:

### Phase 1: Does Any Edge Exist? (2-3 days)

1. **Log ALL signals** (not just traded ones) with scores + market state
2. **Track price at +15/+30/+60/+120 min** after every signal
3. **Compute win rates by signal type**: which score+direction+funding combos actually work?
4. **Answer the question**: "If we only traded the top 10% of signal types, what's the win rate?"

If the answer is <55%, the scanner itself is broken and needs a rebuild.
If the answer is >60%, we have something to work with.

### Phase 2: Build the Filter (1-2 days)

5. Add regime detection
6. Add entry timing filter
7. Add "historical win rate" lookup to the prompt
8. Replace "trust the scores" with "here's whether this type of signal has worked before"

### Phase 3: Active Management (2-3 days)

9. Add thesis tracking per position
10. Add post-entry re-evaluation
11. Add correlation-aware position limits

---

## Scanner Signal Breakdown (what goes into the score)

The scanner computes these sub-scores and blends them:

| Signal | What It Measures | Current Weight |
|--------|-----------------|----------------|
| funding-spread | Directional funding pressure | High |
| oi-trend | Open interest momentum | Medium |
| price-momentum | Short-term price direction | Medium |
| moving-average-alignment | Multi-TF trend alignment | Medium |
| order-block | Institutional S/R zones | Medium |

**The problem:** All of these are lagging or coincident indicators. None are *predictive*. They describe "what's happening now" not "what will happen next." Funding can be extreme and stay extreme for days. Momentum can continue or reverse. The score tells you the current state, not the future.

**What would make scores predictive:**
- Volume profile anomalies (unusual volume at specific price levels)
- Liquidation cascade detection (clustered liquidations = reversal incoming)
- Cross-asset divergence (BTC up, alts down = fragility)
- Funding rate *change* (not level — rate of change matters more)
- Order book imbalance (bid/ask skew)

---

## Cold Start Problem — How to Bootstrap

The pattern engine can't learn because win rate is too low. To break the cycle:

1. **Seed with manual rules first.** Don't wait for the AI to discover patterns. Write them by hand based on the backtest:
   - "Skip signals during Asia session if ATR is below X"
   - "Skip shorts when funding is >0.5% (too crowded)"
   - "Only trade signals with score >80 AND momentum aligned with trend"

2. **Let the AI *modify* these rules** rather than discover from scratch. Start with known-good heuristics, let the AI adjust thresholds.

3. **Use a tighter feedback loop.** Right now patterns are evaluated on 186 trades over 6 days. Evaluate every 10 trades — are we improving? If not, revert.

4. **Separate exploration from exploitation.** Dedicate 20% of trades to testing new signal types ("explore") and 80% to known-good types ("explore"). Currently it's 100% random.

---

## Things That Won't Help (common misconceptions)

❌ **More indicators** — Adding VWAP, RSI, MACD to the scanner won't help if the core scoring doesn't predict. More noise ≠ more signal.

❌ **Better LLM** — GPT-4o would do the same thing. The AI can only analyze what it's given. Garbage data → garbage analysis regardless of model.

❌ **Faster cycling** — Running every 60s instead of 120s just generates more churn. Speed doesn't create edge.

❌ **More patterns** — The pattern engine has 1 pattern because win rate is low. More features to track = more things that can't find a signal.

❌ **Tighter stops** — Already tried. Tighter stops = more frequent small losses = same net result.

---

## Summary

| Component | Status | Impact If Fixed |
|-----------|--------|----------------|
| Scanner scores → predictive | Broken | 🔴 Critical — nothing else matters if scores don't predict |
| Historical backtest lookup | Missing | 🟡 High — cheapest way to find if any edge exists |
| Market regime | Missing | 🟡 High — filters out 30-40% of bad trades |
| Entry timing | Missing | 🟡 Medium — prevents chasing |
| Position correlation | Missing | 🟡 Medium — prevents cluster blowups |
| Post-entry management | Missing | 🟢 Medium — cuts losers faster |
| Pattern engine | Dead | 🟢 Low — can't fix until win rate improves |

**The #1 priority: prove whether the scanner signals have ANY predictive value.** Everything else is building on sand until that's answered.
