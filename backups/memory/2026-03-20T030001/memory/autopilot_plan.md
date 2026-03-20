# Autopilot Plan — lighter-copilot

## Current State (Copilot Mode)
- Bot handles **exits only** (trailing TP/SL)
- Human makes **entries manually**
- Running as Docker container on srv1435474
- 61MB RAM, healthy ✅
- Position: LONG BTC, trailing TP + trailing SL active

## Target State (Autopilot Mode)
Bot handles **both entries and exits** with automated signal-driven trading.

---

## Stage 1: Signal Generation (NEXT)

### Recommended Starting Combo
1. **Funding Rate Contrarian** — Short when funding is extreme positive (>0.05%), long when extreme negative. Mean-reversion signal.
2. **Orderbook Imbalance** — Detect buy/sell pressure from L2 depth. Entry when imbalance exceeds threshold.
3. **MA Crossover (Trend Filter)** — Only take entries aligned with 50/200 EMA trend. Prevents counter-trend entries.

### External Signal Sources (User Preference)
User wants **external sources**, not custom-built indicators. Priority order:

| Source | Type | Cost | Integration |
|--------|------|------|-------------|
| **TradingView Webhooks** | Custom alerts → webhook | Free-$15/mo | Webhook receiver script |
| **Coinglass API** | Funding rate, liquidations | Free tier | REST polling every 5min |
| **Telegram Signal Groups** | Manual signals | Varies | Telegram bot listener |
| **CryptoQuant / Glassnode** | On-chain whale flows | $29+/mo | REST API |
| **Whale Alert API** | Large transfers | Free tier | REST polling |
| **3Commas / Bybit Copy Trading** | Copy trade signals | Platform fees | API integration |

### Recommended Implementation Path
```
TradingView alert → Webhook → bot receives signal → validates against trend filter → executes entry
```
- Simplest, most flexible, already proven infrastructure (Telegram alerts work)
- Can combine multiple TradingView strategies

---

## Stage 2: Signal Execution

### Entry Logic
```
1. Receive signal (webhook / API poll / Telegram)
2. Check: is there already a position? → skip if yes (one position at a time)
3. Check: does signal pass trend filter? (MA crossover direction)
4. Check: risk/reward ratio ≥ 2:1
5. Execute entry with position sizing
6. Activate trailing TP + trailing SL (existing logic)
7. Alert via Telegram
```

### Position Sizing
- Start: **1% of account per trade** (currently ~$0.15)
- Scale up after 2 weeks of positive paper/real results
- Max: 5% of account per trade

### Kill Switches
- **Daily loss limit**: Stop trading if down 3% of account in 24h
- **Consecutive loss limit**: Stop after 3 losing trades in a row
- **Manual override**: Telegram command `/pause` and `/resume`
- **Time-based cooldown**: 15min minimum between trades

---

## Stage 3: Risk Management

### Risk Framework
- Max 1 open position at a time (current: enforced)
- Max daily drawdown: 3% of account
- Trailing SL: 2% (current: implemented ✅)
- Trailing TP: 1% trail from peak (current: implemented ✅)
- R/R minimum: 2:1 before entering

### Logging & Audit Trail
- Every entry/exit logged with: signal source, signal strength, price, size, P&L
- Daily P&L summary
- Weekly win rate + average R:R tracking
- Store in `memory/trade-log.jsonl`

---

## Stage 4: Paper Trading (Before Real Money)

### Paper Mode
- Simulate entries with $0 (log only)
- Track hypothetical P&L
- Run for 1-2 weeks minimum
- Validate signal accuracy before risking real capital
- Bot already has light paper trading code in `scale_up.py` — extend it

### Transition Criteria
- Paper win rate ≥ 55%
- Paper R:R ≥ 1.5:1 average
- No paper drawdowns > 3%
- User explicit approval to go live

---

## Infrastructure

### Single Point of Failure (SPOF)
**Current:** Single VPS (srv1435474)
**Mitigation:**
- Docker container with `restart: unless-stopped` ✅
- Health checks every 30s ✅
- Crash recovery via container restart ✅
- **TODO:** Add external monitoring (uptime robot / Telegram heartbeat)
- **TODO:** Consider secondary VPS for failover if account grows

### Geo-Restriction Handling
- SOCKS5 proxy: 64.137.96.74:6641 (Netherlands, Webshare)
- Proxy tested and working ✅
- Keep proxy credentials secure (env vars, not config files)

---

## Implementation Order

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Dockerize bot | ✅ Done | 61MB, healthy |
| 2 | Trailing SL | ✅ Done | Ratchets up, never down |
| 3 | Webhook receiver | 🔲 Next | Flask/FastAPI endpoint for TradingView |
| 4 | Coinglass polling | 🔲 | Funding rate alerts |
| 5 | Entry signal framework | 🔲 | Signal → validation → execution pipeline |
| 6 | Kill switch / risk guards | 🔲 | Daily loss, consecutive losses, cooldown |
| 7 | Paper trading mode | 🔲 | Simulate before going live |
| 8 | Telegram commands | 🔲 | `/pause`, `/resume`, `/status`, `/positions` |
| 9 | Trade logging | 🔲 | JSONL audit trail |
| 10 | Scale up testing | 🔲 | After 2 weeks positive paper results |

---

## Config Changes Needed

### config.yml additions (future)
```yaml
signals:
  enabled: false  # flip to true when ready
  mode: "paper"   # paper | live
  sources:
    - type: webhook
      port: 8080
    - type: coinglass
      poll_interval: 300
  filters:
    trend_filter: true
    min_rr_ratio: 2.0
  risk:
    max_daily_loss_pct: 3.0
    max_consecutive_losses: 3
    cooldown_seconds: 900
    max_position_pct: 1.0  # % of account
```

---

---

## Open-Source Repo Patterns to Adopt (Mar 19)

Reviewed 4 repos for reusable patterns. Key findings:

### Signal Generation (immediate priority)

**From hkirat/ai-trading-agent (Lighter + LLM):**
- Tool-call execution architecture (Zod schemas → LLM → Lighter SDK)
- Dual timeframe indicator injection (5m + 4h raw arrays into prompt)
- Prisma DB logging for post-trade analysis
- ⚠️ No risk management — don't copy that part

**From enarjord/passivbot (Perp Futures Specialist):**
- **Forager coin selection** — volume_score → volatility_score funnel (mirrors our OSA, production-tested)
- **Pareto multi-objective optimizer** — evaluate signal quality across profit, drawdown, sharpe, win_rate (not just win rate)
- Trailing entries with retracement confirmation
- Warmup system for pre-training EMA state from historical data
- ⚠️ Martingale grid entries — risky, don't adopt

**From freqtrade/freqtrade + FreqAI (ML Powerhouse):**
- Sliding window retraining pipeline (train on window N, predict N+1, slide)
- **Dissimilarity Index (DI)** — detect when live data differs too much from training data → skip or retrain
- Feature importance + PCA for dimensionality reduction
- Reinforcement Learning environment (define reward → agent learns policy)
- Lookahead bias detection tools

**From drakkar-software/OctoBot (AI-Native):**
- Signal history storage for backtesting LLM strategies
- LLM service abstraction with rate limiting + model policies
- Tool-calling orchestration loop with max iterations
- MCP tool injection for external data sources
- ⚠️ GPT evaluator prompt is too trivial ("predict up/down") — ours is better

### Autopilot (next phase)

**From passivbot:**
- Rust orchestrator pattern — compute all orders in one place, execute in another
- "Unstucking" stuck positions algorithm (realize small losses over time)
- Self-healing multi-instance locks

**From OctoBot:**
- Daily token budget / rate limiting for LLM calls

**From freqtrade:**
- Sliding window + DI threshold for model freshness
- RL agent for learning execution policy

### Don't Touch
- Hummingbot — no Lighter support
- Jesse — backtesting-focused, no perp DEX
- Full OctoBot framework — too heavy, write lightweight versions of useful parts

### Priority Implementation Order (updated)
| # | Task | Source | Notes |
|---|------|--------|-------|
| 1 | Dockerize bot | — | ✅ Done |
| 2 | Trailing SL | — | ✅ Done |
| 3 | Opportunity Scanner (OSA) | — | ✅ Done (mirrors passivbot Forager) |
| 4 | DSL (trailing stop) | — | ✅ Done |
| 5 | Webhook receiver | hkirat pattern | Flask/FastAPI for signal intake |
| 6 | Entry signal framework | hkirat tool-call + OSA | Signal → validation → execution |
| 7 | Pareto signal evaluator | passivbot | Multi-metric signal quality |
| 8 | DI threshold for scanner | freqtrade FreqAI | Skip coins with stale/noisy data |
| 9 | Kill switch / risk guards | — | Daily loss, consecutive losses, cooldown |
| 10 | Paper trading mode | — | Simulate before going live |
| 11 | Signal history storage | OctoBot | Store every LLM prediction for replay |
| 12 | LLM rate limiting | OctoBot | Daily token budget |
| 13 | Telegram commands | — | `/pause`, `/resume`, `/status`, `/positions` |
| 14 | Trade logging (JSONL) | — | Audit trail |
| 15 | RL execution agent | freqtrade | Learn entry/exit policy from rewards |
| 16 | Scale up testing | — | After 2 weeks positive paper results |

---

*Last updated: 2026-03-19 04:11 UTC*
*Status: Planning complete + repo patterns integrated, implementation pending*
