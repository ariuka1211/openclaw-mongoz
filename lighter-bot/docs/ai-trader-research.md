# AI-Driven Trading Agent Research

> Blitz research doc — March 2026

## Table of Contents
1. [Architecture Patterns](#1-architecture-patterns)
2. [Market Context Pipeline](#2-market-context-pipeline)
3. [Existing Open-Source Projects](#3-existing-open-source-projects)
4. [Decision Output Format (JSON)](#4-decision-output-format)
5. [Learning & Feedback Loops](#5-learning--feedback-loops)
6. [Safety Patterns](#6-safety-patterns)
7. [Recommended Stack](#7-recommended-stack)
8. [Risks & Limitations](#8-risks--limitations)

---

## 1. Architecture Patterns

### The Perceive → Think → Decide → Act → Learn Loop

The dominant pattern across research (TradingAgents, FinMem, AI-Hedge-Fund) is a **5-phase loop**:

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────┐    ┌────────┐
│ PERCEIVE │───▶│  THINK  │───▶│ DECIDE  │───▶│ ACT  │───▶│ LEARN  │
│          │    │         │    │         │    │      │    │        │
│ Collect  │    │ Debate  │    │ Trade   │    │ Send │    │ Reflect│
│ data     │    │ Bull vs │    │ size &  │    │ order│    │ P&L,   │
│ from all │    │ Bear    │    │ timing  │    │ to   │    │ update │
│ sources  │    │ agents  │    │         │    │ exch │    │ memory │
└─────────┘    └─────────┘    └─────────┘    └──────┘    └────────┘
      │                                                               │
      └───────────────────────────────────────────────────────────────┘
                          Continuous feedback
```

### Multi-Agent Firm Model (TradingAgents)

Based on the paper [arXiv:2412.20138](https://arxiv.org/abs/2412.20138), the best-performing architecture simulates a **trading firm** with specialized agent roles:

| Team | Agents | Phase |
|------|--------|-------|
| **Analysts** | Fundamental, Sentiment, News, Technical | Perceive |
| **Researchers** | Bullish Researcher, Bearish Researcher | Think |
| **Traders** | Aggressive Trader, Conservative Trader | Decide |
| **Risk Mgmt** | Volatility Monitor, Drawdown Guard, Position Sizer | Decide (gate) |
| **Execution** | Order Router, Slippage Monitor | Act |

Key insight: **debate between bull/bear researchers** produces better decisions than single-agent analysis. The paper shows this outperforms baselines in cumulative returns, Sharpe ratio, and max drawdown.

### ReAct Framework

Most implementations use **ReAct (Reason + Act)** prompting — the LLM explicitly reasons through its analysis before producing a structured action. This is superior to direct "tell me what to trade" because:
- Chain-of-thought is visible and auditable
- The model can call tools mid-reasoning (fetch price, check indicator)
- Errors in reasoning are caught before execution

### Memory Layers

FinMem pioneered **layered memory** that all serious implementations now use:

| Layer | Retention | Purpose |
|-------|-----------|---------|
| **Short-term** | Current session | Real-time events, current position state |
| **Middle-term** | Recent trades | Strategy effectiveness, recent P&L |
| **Long-term** | Persistent | Historical patterns, lessons learned |

---

## 2. Market Context Pipeline

### What to Feed the LLM

Efficient context is the key constraint. LLMs have limited context windows and tokens are expensive. The optimal data mix:

**Tier 1 — Always Include (high signal, low token cost):**
- Current position state (side, size, entry price, unrealized P&L)
- Current price + key levels (support/resistance from order book)
- Recent trade history (last 10 trades with outcomes)
- Active orders

**Tier 2 — Include on Each Cycle (medium cost):**
- Technical indicators (RSI, MACD, Bollinger Bands — pre-computed as numbers, not charts)
- Order book snapshot (top 5-10 levels each side)
- Recent funding rates (for perps)
- Current portfolio P&L summary

**Tier 3 — Periodic or Event-Triggered (high cost, high value):**
- News sentiment scores (pre-processed by FinGPT or similar)
- Social media sentiment (aggregated, not raw tweets)
- Macroeconomic indicators
- Cross-asset correlations (BTC vs ETH, crypto vs equities)

### Compression Strategy

Don't dump raw data. Pre-compute and compress:

```
❌ Bad:  500 candles of OHLCV data (tokens: ~5000)
✅ Good: "BTC: $67,432 (+2.1% 24h), RSI: 62, MACD: bullish cross, 
          near resistance $67,800, 24h vol above avg" (tokens: ~50)

❌ Bad:  Full order book (tokens: ~3000)
✅ Good: "Bid wall at $67,200 (50 BTC), Ask wall at $67,600 (35 BTC),
          spread: 0.02%, imbalance: 60% bid" (tokens: ~30)
```

### Tool-Augmented Approach

Instead of stuffing everything into context, use **function calling**:
- LLM asks "what's the 1h RSI?" → tool returns single number
- LLM asks "any news in last hour?" → tool returns sentiment score
- This keeps initial context lean and lets the LLM drill down on demand

---

## 3. Existing Open-Source Projects

### Tier 1: Research-Backed, Actively Maintained

| Project | Focus | Stars | Key Insight |
|---------|-------|-------|-------------|
| **[TradingAgents](https://github.com/TauricResearch/TradingAgents)** | Multi-agent LLM trading firm | ~2k+ | Bull/bear debate architecture, ReAct, layered memory |
| **[FinRL](https://github.com/AI4Finance-Foundation/FinRL)** | RL-based trading | ~10k+ | Deep reinforcement learning for trading; Contest 2025 integrates FinGPT signals with RL agents |
| **[FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)** | Financial LLM fine-tuning | ~14k+ | Sentiment analysis rivaling GPT-4, financial statement analysis (86% benchmark) |
| **[FinMem](https://github.com/xxx/FinMem)** | LLM + layered memory | — | Won IJCAI 2024 FinLLM challenge; hierarchical context retention |

### Tier 2: Practical Bots

| Project | Focus | Notes |
|---------|-------|-------|
| **[Freqtrade + FreqAI](https://github.com/freqtrade/freqtrade)** | Python trading bot with ML | Huge community, supports LLM/transformer models, live retraining, multi-exchange |
| **[LLM_trader](https://github.com/xxx/LLM_trader)** | Multi-LLM crypto bot | Chain-of-thought, 20+ indicators, sentiment, fallback models |
| **[AI-Hedge-Fund](https://github.com/xxx/ai-hedge-fund)** | Ensemble LLM agents | LangChain orchestration, technical+sentiment+news agents |
| **[FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)** | Agentic finance platform | Strong on valuation (75%), good framework for building custom agents |

### Key Takeaway from FinAI Contest 2025

The current state-of-the-art approach: **FinGPT for signal extraction (news/tweets → sentiment scores) → FinRL for RL-based execution (market data + signals → trade decisions via GRPO algorithm)**.

---

## 4. Decision Output Format

### Recommended JSON Schema

The LLM should output a structured decision object. Here's a practical schema tailored for **perpetual futures** on an exchange like Lighter:

```json
{
  "timestamp": "2026-03-20T00:24:00Z",
  "market": "BTC-USD",
  "analysis": {
    "sentiment": "bullish",
    "confidence": 0.72,
    "key_factors": [
      "RSI at 62 — not overbought",
      "Funding rate turning positive",
      "Strong bid wall at $67,200"
    ],
    "risk_factors": [
      "Approaching 4h resistance at $67,800",
      "Low weekend volume"
    ]
  },
  "decision": {
    "action": "LONG",
    "size_pct": 5.0,
    "leverage": 3,
    "entry_type": "limit",
    "entry_price": 67250.0,
    "stop_loss": 66800.0,
    "take_profit": 68200.0,
    "time_in_force": "GTC",
    "reduce_only": false
  },
  "sizing_rationale": "5% of equity — moderate confidence, approaching resistance so keeping size small",
  "abort_conditions": [
    "Price breaks below $67,000 before fill",
    "Funding rate spikes above 0.05%",
    "Open interest drops 20% in 1 hour"
  ]
}
```

### Schema Rules

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `analysis.sentiment` | enum | Yes | `bullish`, `bearish`, `neutral` |
| `analysis.confidence` | float 0-1 | Yes | Below 0.4 → skip trade |
| `decision.action` | enum | Yes | `LONG`, `SHORT`, `CLOSE`, `NONE` |
| `decision.size_pct` | float | Yes | % of equity, **capped by safety** |
| `decision.leverage` | int | Yes | **Capped by safety** (e.g., max 5x) |
| `decision.stop_loss` | float | **Always required** | Non-negotiable |
| `decision.take_profit` | float | Recommended | Can be null for trailing |
| `abort_conditions` | array | Yes | Pre-trade invalidation criteria |

### Critical: Schema Enforcement

The LLM output should be **validated by code before execution**, never trusted raw:
- JSON.parse → JSON Schema validation → business rule validation → execution
- If any step fails → reject trade, log error

---

## 5. Learning & Feedback Loops

### How to Make the AI Learn From Past Trades

**Layer 1 — Trade Journal (Always-On)**
Every trade gets logged with full context:
```json
{
  "trade_id": "abc123",
  "decision_snapshot": { /* the full JSON decision */ },
  "execution": { "actual_entry": 67251.5, "actual_exit": 68150.0, "slippage": 1.5 },
  "outcome": { "pnl": 4.2, "pnl_pct": 1.8, "hold_time": "4h 22m", "max_drawdown": -0.5 },
  "post_mortem": null  // filled by reflection agent
}
```

**Layer 2 — Reflection Agent (Periodic)**
A separate LLM call that reviews recent trades:
- "Your last 10 LONG trades had 70% win rate but avg winner was 1.2% vs avg loser 2.1%"
- "You tend to overtrade during low-volume weekends"
- "Your best trades came when RSI was 55-65, not when oversold"
- Output: updated strategy notes fed into next decision cycle

**Layer 3 — Strategy Memory (Persistent)**
Structured notes that persist across sessions:
```markdown
## Learned Patterns (auto-updated)
- BTC weekend trades underperform: reduce size by 50% on Sat/Sun
- High funding rate (>0.03%) → contrarian short often wins
- News-driven pumps: wait 15min for reversion before entering
- My avg win rate on LONG: 62%, SHORT: 45% → bias toward longs
```

**Layer 4 — Performance Metrics Dashboard**
Track over time:
- Win rate by direction (long vs short)
- Win rate by time of day / day of week
- Avg win/loss ratio
- Sharpe ratio of AI decisions vs random baseline
- "Did I follow my own rules?" compliance rate

### What NOT to Do

- ❌ Don't fine-tune the base model on your trade history (too expensive, too slow, data hungry)
- ❌ Don't let the LLM modify its own safety rules based on outcomes
- ✅ Do use in-context learning: feed trade outcomes as part of the prompt
- ✅ Do use the reflection agent to update strategy notes
- ✅ Do track metrics externally (not in LLM context)

---

## 6. Safety Patterns

### Rules That the LLM Cannot Override

These are **hard-coded in the execution layer**, not prompt instructions:

```python
# SAFETY_INVIOLABLE — these run in code, not in the LLM

MAX_POSITION_SIZE_PCT = 20      # Max 20% of equity per position
MAX_LEVERAGE = 5                # Max 5x leverage
MAX_DAILY_DRAWDOWN_PCT = 10     # Kill switch: pause all trading
MAX_TOTAL_EXPOSURE_PCT = 60     # Max 60% of equity deployed
MIN_CONFIDENCE_THRESHOLD = 0.4  # Below this → reject trade
REQUIRED_STOP_LOSS = True       # Every position MUST have SL
MAX_ORDERS_PER_MINUTE = 6       # Rate limit
COOLDOWN_AFTER_LOSS = 300       # 5 min pause after hitting SL
```

### The Kill Switch

```
if daily_drawdown > MAX_DAILY_DRAWDOWN_PCT:
    close_all_positions()
    pause_trading()
    alert_human()
    # LLM has NO ability to unpause
```

### Multi-Layer Safety Architecture

```
Layer 1: LLM proposes trade (advisory only)
    ↓
Layer 2: Schema validation (correct format?)
    ↓
Layer 3: Business rules (within position limits? has SL?)
    ↓
Layer 4: Pre-trade checks (sufficient margin? market open?)
    ↓
Layer 5: Execution (with slippage protection)
    ↓
Layer 6: Post-trade monitoring (SL/TP active? kill switch armed?)
```

**The LLM only has input at Layer 1. Everything else is code.**

### Prompt-Level Safety (Defense in Depth)

While the hard rules above are non-negotiable, also include guardrails in the system prompt:
- "You MUST always specify a stop loss"
- "Never suggest position sizes above X%"
- "If confidence is below 0.5, output action: NONE"
- "Never suggest trading during major economic announcements (FOMC, CPI)"

These are the *first* line of defense, not the *only* one.

---

## 7. Recommended Stack

### For Our Lighter Bot

| Component | Recommendation | Why |
|-----------|---------------|-----|
| **LLM** | Claude Sonnet / GPT-4o via API | Best structured output, function calling, reasonable cost |
| **Orchestration** | LangGraph or custom loop | Stateful agent with tool use, memory management |
| **Trading Engine** | Existing Lighter SDK | We already have this |
| **Signal Processing** | Pre-compute indicators in Python | Don't burn tokens on raw data |
| **Memory** | SQLite + JSON files | Lightweight, queryable, no infra overhead |
| **Reflection** | Separate LLM call on schedule | Cheaper model (Haiku/mini) for post-mortems |
| **Safety** | Python middleware layer | Code between LLM and exchange, non-negotiable |

### Recommended Architecture

```
┌──────────────────────────────────────────────────────┐
│                    TRADING LOOP                       │
│                                                      │
│  ┌─────────┐   ┌─────────┐   ┌──────────┐          │
│  │ Market  │──▶│ Context │──▶│   LLM    │          │
│  │ Data    │   │ Builder │   │ (Claude) │          │
│  │ Fetcher │   │         │   │          │          │
│  └─────────┘   └─────────┘   └────┬─────┘          │
│                                    │                 │
│                              JSON decision           │
│                                    │                 │
│                           ┌────────▼────────┐       │
│                           │ Safety Validator│       │
│                           │ (Python code)   │       │
│                           └────────┬────────┘       │
│                                    │                 │
│                          ┌─────────▼──────────┐     │
│                          │ Lighter Exchange   │     │
│                          │ SDK (execution)    │     │
│                          └─────────┬──────────┘     │
│                                    │                 │
│                           ┌────────▼────────┐       │
│                           │ Trade Journal   │       │
│                           │ (SQLite/JSON)   │       │
│                           └────────┬────────┘       │
│                                    │                 │
│                           ┌────────▼────────┐       │
│                           │ Reflection Agent│       │
│                           │ (periodic)      │       │
│                           └─────────────────┘       │
└──────────────────────────────────────────────────────┘
```

### Cycle Timing

| Market Regime | Decision Interval | Rationale |
|---------------|-------------------|-----------|
| High volatility | Every 5-15 min | Rapid regime changes |
| Normal | Every 30-60 min | Avoid overtrading |
| Low volume | Every 1-2 hours | Minimal signal, save tokens |
| During news events | Every 2-5 min | Fast reactions needed |

---

## 8. Risks & Limitations

### Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **LLM hallucination** — invents market data or signals | Critical | Never feed raw LLM output to exchange; validate against actual market data |
| **Prompt injection** — adversarial inputs from news/social | High | Sanitize all external data before feeding to LLM |
| **API rate limits** — LLM API throttling during volatile markets | Medium | Fallback to "hold" decision if API unavailable |
| **Latency** — LLM inference is slow (1-5s) vs HFT (μs) | Medium | Accept we're not HFT; use for strategic decisions, not tick-level |
| **Context window overflow** — too much data in prompt | Medium | Aggressive compression; tier 1 always, tier 2/3 selectively |
| **Model drift** — LLM behavior changes between versions | Medium | Pin model version; test before upgrading |

### Financial Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Overfitting to backtest** | High | Out-of-sample testing, walk-forward validation |
| **Black swan events** | Critical | Kill switch, max exposure limits, diversification |
| **Overtrading** — LLM generates too many signals | Medium | Minimum confidence threshold, cooldown periods |
| **Correlation risk** — all agents see same data | Medium | Position correlation limits |
| **Cost creep** — LLM API costs exceed trading profits | Low | Track cost per decision; use cheaper models for reflection |

### Fundamental Limitations

1. **LLMs don't "understand" markets** — they pattern-match on text. A sufficiently novel market event will break them.
2. **No alpha from public data alone** — if the LLM sees it, everyone sees it. Edge must come from synthesis, speed, or unique data.
3. **Backtest != Live** — LLM decisions in backtests are deterministic given the same prompt, but live markets are not.
4. **Regulatory uncertainty** — autonomous trading bots may face regulation in some jurisdictions.
5. **Security** — API keys for exchange access are high-value targets. Protect them aggressively.

### Honest Assessment

An LLM trading agent is best positioned as a **decision-support system with execution capability**, not a fully autonomous money printer. The LLM's value is:
- Synthesizing multiple data sources faster than a human
- Maintaining discipline (always sets SL, follows rules)
- Operating 24/7 without fatigue
- Producing auditable reasoning for every trade

The LLM's weakness is:
- No true market understanding
- Susceptible to novel events
- Expensive at scale (API costs)
- Slow compared to quantitative strategies

**Best approach: LLM for strategy/signals, code for execution/safety, humans for oversight.**

---

## Sources

- TradingAgents paper: [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) (Tauric Research, Dec 2024)
- FinAI Contest 2025: [open-finance-lab.github.io](https://open-finance-lab.github.io/FinAI_Contest_2025/)
- FinRL: [github.com/AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL)
- FinGPT: [github.com/AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)
- TradingAgents repo: [github.com/TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- LLM Trading Bots Comparison: [flowhunt.io](https://www.flowhunt.io/blog/llm-trading-bots-comparison/)
- DigitalOcean TradingAgents deep dive: [digitalocean.com](https://www.digitalocean.com/resources/articles/tradingagents-llm-framework)
