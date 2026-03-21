# AI Trader Infrastructure Plan

> Generated: 2026-03-20 | Techno4k

## Executive Summary

Plan for adding an LLM-driven trading autopilot on top of the existing Lighter.xyz bot infrastructure. The system runs on a single 4vCPU/8GB VPS, replacing fixed-formula decisions with AI-generated trading decisions every 2-5 minutes, 24/7.

**Key constraint:** Everything runs on one VPS. No multi-node. No Kubernetes. Keep it dead simple.

---

## 1. Current State

| Component | Status |
|---|---|
| VPS | 4 vCPU, 8GB RAM, 80GB disk (64GB free) |
| Opportunity Scanner | TypeScript (bun), cron every 5 min, outputs `signals.json` |
| Bot | Python, reads `signals.json`, manages positions via Lighter API |
| Agent Framework | OpenClaw with subagent spawning |
| Runtime | Python 3.12, Node 22, Bun 1.3 |

The scanner already produces rich structured data (composite scores, funding rates, position sizing). The AI agent will **replace the decision logic** that currently interprets these signals.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        VPS (srv1435474)                         │
│                                                                 │
│  ┌──────────┐    ┌──────────────┐    ┌─────────────────────┐   │
│  │ Scanner  │───▶│ signals.json │───▶│  AI Decision Engine │   │
│  │ (cron/5m)│    │              │    │  (daemon, ~3min)    │   │
│  └──────────┘    └──────────────┘    │                     │   │
│                                      │  ┌───────────────┐  │   │
│  ┌──────────────┐                    │  │ Context       │  │   │
│  │ Market Data  │──────────────────▶│  │ Builder       │  │   │
│  │ Cache        │                    │  └───────┬───────┘  │   │
│  └──────────────┘                    │          │          │   │
│                                      │  ┌───────▼───────┐  │   │
│                                      │  │ LLM Call      │  │   │
│                                      │  │ (OpenRouter)  │  │   │
│                                      │  └───────┬───────┘  │   │
│                                      │          │          │   │
│                                      │  ┌───────▼───────┐  │   │
│                                      │  │ Decision      │  │   │
│                                      │  │ Parser        │  │   │
│                                      │  └───────┬───────┘  │   │
│                                      │          │          │   │
│                                      │  ┌───────▼───────┐  │   │
│                                      │  │ Safety Layer  │  │   │
│                                      │  └───────┬───────┘  │   │
│                                      └──────────┼──────────┘   │
│                                                 │              │
│                    ┌────────────────┐    ┌──────▼──────┐       │
│                    │ Decision Log   │◀───│ Bot Execute │       │
│                    │ (SQLite)       │    │ (Python)    │       │
│                    └────────────────┘    └─────────────┘       │
│                                                                 │
│                    ┌────────────────┐                           │
│                    │ Watchdog       │  monitors all processes   │
│                    │ (systemd)      │                           │
│                    └────────────────┘                           │
│                                                                 │
│                    ┌────────────────┐                           │
│                    │ Dashboard      │  FastAPI + static HTML    │
│                    │ :8080          │                           │
│                    └────────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Scanner** (cron, every 5 min) → writes `signals.json` with market opportunities
2. **Context Builder** reads `signals.json` + current positions + recent decisions + market snapshot
3. **LLM Call** sends structured prompt to OpenRouter, receives JSON decision
4. **Decision Parser** validates JSON schema, extracts actions
5. **Safety Layer** checks against hard limits (max drawdown, position caps, leverage)
6. **Bot Execute** applies approved decisions via existing bot.py interface
7. **Feedback Logger** writes decision + outcome to SQLite for audit

---

## 3. Decision Loop Reliability

### Recommendation: Hybrid Daemon + Cron Guard

**Primary:** Long-running Python daemon (`ai_trader.py`) with its own internal timer.

**Why NOT pure cron:**
- LLM calls can take 5-30s; cron doesn't know if previous run finished
- State needs to persist between cycles (positions, PnL, decision history)
- Cron overlap = double-trading risk
- Can't do backoff on failures

**Why NOT pure daemon:**
- Daemons can die silently
- Need a watchdog

**Solution: systemd service + cron health check**

```
ai-trader.service     — systemd manages the daemon, auto-restarts on crash
ai-trader-watchdog    — cron every 5 min, checks service is alive, alerts if dead
```

### Internal Loop Design

```python
class AITraderLoop:
    CYCLE_INTERVAL = 180  # 3 minutes

    async def run_forever(self):
        while True:
            try:
                start = time.time()
                await self.execute_cycle()
                elapsed = time.time() - start
                sleep_time = max(0, self.CYCLE_INTERVAL - elapsed)
                await asyncio.sleep(sleep_time)
            except CriticalError:
                # Stop trading, alert
                await self.emergency_shutdown()
                break
            except Exception as e:
                self.logger.error(f"Cycle failed: {e}")
                await asyncio.sleep(60)  # backoff on errors
```

### LLM Timeout Handling

| Scenario | Action |
|---|---|
| LLM responds in <15s | Normal operation |
| LLM takes 15-45s | Log warning, use response if valid |
| LLM takes >45s | Cancel call, skip cycle, hold positions |
| LLM returns invalid JSON | Retry once, then skip cycle |
| LLM returns valid JSON but nonsensical | Safety layer rejects, log anomaly |
| OpenRouter 429 (rate limit) | Backoff 60s, retry |
| OpenRouter 5xx | Retry with exponential backoff (2s, 4s, 8s) |
| OpenRouter unreachable | Skip cycle, hold all positions, alert |
| 3+ consecutive failures | Emergency: stop all new decisions, hold existing |

**Golden rule:** If the AI can't think, the system does nothing. Hold > act on bad data.

---

## 4. State Management

### Recommendation: SQLite + JSON files

**Why SQLite over Postgres/Redis:**
- Single VPS, single writer — SQLite is perfect
- Zero ops (no server to run)
- Atomic writes, crash-safe with WAL mode
- Already have Python `sqlite3` built-in
- 80GB disk, we'll use <100MB

### Schema

```sql
-- decisions: every AI decision with full reasoning
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,           -- ISO 8601
    cycle_id TEXT NOT NULL,            -- UUID for this decision cycle
    market_symbol TEXT,                -- e.g., 'ROBO'
    action TEXT NOT NULL,              -- 'open', 'close', 'adjust', 'hold'
    direction TEXT,                    -- 'long', 'short', NULL for hold
    size_usd REAL,
    leverage REAL,
    stop_loss_pct REAL,
    reasoning TEXT NOT NULL,           -- LLM's full reasoning
    confidence REAL,                   -- LLM self-reported confidence 0-1
    safety_approved INTEGER DEFAULT 0, -- passed safety layer?
    safety_reasons TEXT,               -- rejection reasons if any
    executed INTEGER DEFAULT 0,        -- actually sent to bot?
    execution_result TEXT,             -- bot response or error
    pnl_at_decision REAL,             -- portfolio PnL when decision made
    positions_snapshot TEXT,           -- JSON snapshot of positions
    signals_snapshot TEXT,             -- JSON snapshot of signals.json
    model_used TEXT,                   -- which LLM model
    tokens_input INTEGER,
    tokens_output INTEGER,
    latency_ms INTEGER
);

-- performance: periodic snapshots
CREATE TABLE performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    equity REAL,
    unrealized_pnl REAL,
    realized_pnl REAL,
    open_positions INTEGER,
    daily_trades INTEGER,
    max_drawdown_pct REAL,
    win_rate REAL
);

-- alerts: things that need attention
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,               -- 'info', 'warn', 'critical'
    category TEXT NOT NULL,            -- 'llm', 'safety', 'execution', 'api'
    message TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0
);

CREATE INDEX idx_decisions_ts ON decisions(timestamp);
CREATE INDEX idx_performance_ts ON performance(timestamp);
CREATE INDEX idx_alerts_level ON alerts(level, acknowledged);
```

### File-based state (simple, debuggable)

```
signals/ai-trader/
├── ai_trader.py           — main daemon
├── config.json            — all settings
├── context_builder.py     — builds LLM prompts
├── safety.py              — safety layer
├── db.py                  — SQLite helpers
├── dashboard.py           — FastAPI dashboard
├── prompts/
│   ├── system.txt         — system prompt
│   └── decision.txt       — decision template
├── state/
│   ├── trader.db          — SQLite database
│   ├── current_positions.json  — live position cache
│   └── last_cycle.json    — last cycle metadata
├── static/
│   └── index.html         — dashboard UI
└── logs/
    └── ai-trader.log      — rotating log
```

---

## 5. API Rate Limits & Cost Estimates

### OpenRouter Pricing (as of early 2026)

| Model | Input $/1M | Output $/1M | Quality | Latency | Best For |
|---|---|---|---|---|---|
| `anthropic/claude-3.5-haiku` | $0.80 | $4.00 | Good | ~1-2s | **Primary choice** |
| `google/gemini-2.0-flash` | $0.10 | $0.40 | Good | ~1-2s | Budget alternative |
| `openai/gpt-4o-mini` | $0.15 | $0.60 | Good | ~1-2s | Budget alternative |
| `anthropic/claude-3.5-sonnet` | $3.00 | $15.00 | Great | ~3-5s | Complex analysis |
| `openai/gpt-4o` | $2.50 | $10.00 | Great | ~2-4s | Complex analysis |

### Cost Calculation

**Assumptions:**
- Decision cycle every 3 minutes = 480 cycles/day
- Each cycle: ~1500 input tokens (context + signals) + ~300 output tokens (decision JSON)
- Monthly: 480 × 30 = 14,400 cycles

| Model | Monthly Input Tokens | Monthly Output Tokens | Monthly Cost |
|---|---|---|---|
| Claude 3.5 Haiku | 21.6M | 4.32M | **$34.56** |
| Gemini 2.0 Flash | 21.6M | 4.32M | **$3.89** |
| GPT-4o Mini | 21.6M | 4.32M | **$5.83** |
| Claude 3.5 Sonnet | 21.6M | 4.32M | **$129.60** |

### Recommendation

**Primary: Claude 3.5 Haiku** (~$35/mo)
- Fast enough for 3-min cycles
- Good at structured JSON output
- Reasonable cost for the quality

**Fallback: Gemini 2.0 Flash** (~$4/mo)
- Ultra-cheap, decent quality
- Good for when Haiku is down or rate-limited

**Strategy: Tiered model routing**
```python
MODEL_TIERS = {
    "primary": "anthropic/claude-3.5-haiku",
    "fallback": "google/gemini-2.0-flash",
    "deep": "anthropic/claude-3.5-sonnet",  # for weekly deep analysis
}
```

Most cycles use `primary`. If primary fails, auto-fallback. Use `deep` model once per day for portfolio-level strategy review (adds ~$9/mo).

**Estimated total: $40-50/month** for 24/7 operation.

---

## 6. Fault Tolerance Matrix

| Failure | Detection | Response | Recovery |
|---|---|---|---|
| LLM timeout (>45s) | asyncio timeout | Skip cycle, hold | Next cycle normal |
| LLM bad JSON | JSON parse fail | Retry once, then skip | Log, next cycle |
| LLM nonsensical output | Safety layer validation | Reject decision, log | Next cycle normal |
| OpenRouter 429 | HTTP status | Backoff 60s, retry | Exponential up to 5min |
| OpenRouter 5xx | HTTP status | Retry 3x with backoff | Fallback model |
| OpenRouter unreachable | Connection error | Skip cycle, alert | Fallback model |
| 3+ consecutive LLM failures | Counter in memory | Emergency hold mode | Manual restart needed |
| Safety layer bug | Exception | Reject all, alert | Code fix |
| Bot execution failure | API error response | Log, don't retry (no double-trade) | Manual review |
| SQLite corruption | Integrity check | Restore from WAL backup | Cron backup |
| Python daemon crash | systemd watchdog | Auto-restart (max 5 in 10min) | systemd restart |
| Disk full | df check in watchdog | Stop trading, alert | Manual cleanup |
| VPS OOM | systemd OOMKill | Restart with memory limit | Increase swap |
| Clock skew | NTP check | Alert | Fix NTP |

### Emergency Shutdown Triggers

The system will auto-halt (stop opening new positions, hold existing) if:
1. Daily drawdown exceeds 10% of equity
2. 3+ consecutive LLM failures
3. 5+ consecutive safety rejections (suggests bad prompt or model degradation)
4. Open positions exceed max_concurrent_positions × 2 (something is very wrong)
5. Manual kill signal (`SIGUSR1`)

---

## 7. Prompt Design (Key Decisions)

### System Prompt Structure

```
You are a crypto perps trading AI. You receive market data and current positions.
You must output a JSON decision object. You have HARD SAFETY LIMITS that cannot be overridden.

RULES:
- Max 3 concurrent positions
- Max 20x leverage per position
- Max 5% risk per trade
- If uncertain, output {"action": "hold", "reasoning": "..."}
- NEVER output actions that exceed safety limits

INPUT FORMAT:
{market_data_json}

OUTPUT FORMAT (strict JSON):
{
  "action": "open|close|adjust|hold",
  "symbol": "TOKEN",
  "direction": "long|short|null",
  "size_usd": number,
  "leverage": number,
  "stop_loss_pct": number,
  "reasoning": "detailed explanation",
  "confidence": 0.0-1.0
}
```

The reasoning field is critical — it's the audit trail. Every decision must explain *why*.

---

## 8. Monitoring & Alerting

### What to Monitor

| Metric | Check Interval | Alert Threshold |
|---|---|---|
| Daemon alive | 5 min (cron) | Not responding |
| LLM latency | Every cycle | >30s avg over 5 cycles |
| LLM failure rate | Rolling 1-hour | >30% failure rate |
| Decision quality | Rolling 24-hour | >50% safety rejections |
| Daily PnL | Hourly | Drawdown >8% |
| Disk space | Hourly | <10GB free |
| Memory usage | 5 min | >90% used |
| SQLite size | Daily | >500MB |

### Alerting Channels

1. **Telegram** — immediate alerts via existing OpenClaw integration
2. **Dashboard** — visual status on web UI
3. **Log file** — everything logged for post-mortem

### Integration with Existing Watchdog

Extend the existing cron healthcheck to also verify:
- `ai-trader.service` is running
- Last successful cycle was <10 min ago
- No unacknowledged critical alerts in the DB

---

## 9. Dashboard Tech Recommendation

### Winner: FastAPI + Vanilla HTML/JS

**Why not Streamlit:** Too heavy for a VPS, restarts on every change, not great for real-time.
**Why not Grafana:** Overkill, needs InfluxDB/Prometheus, complex setup for what we need.
**Why FastAPI:**
- Lightweight (~50MB RAM)
- Built-in async (matches the daemon)
- Serves static files natively
- WebSocket support for live updates
- Python — same stack as the bot
- Can run alongside the daemon in the same process or as a separate service

### Dashboard Pages

1. **Status** — daemon health, last cycle time, current model, LLM latency
2. **Positions** — open positions with unrealized PnL
3. **Decisions** — last 50 decisions with reasoning (expandable)
4. **Performance** — equity curve, win rate, drawdown chart
5. **Alerts** — active alerts, acknowledgment

### Implementation

```python
# Simple FastAPI app, ~200 lines
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/api/status")
async def get_status():
    # query SQLite, return JSON

@app.get("/api/decisions")
async def get_decisions(limit: int = 50):
    # query SQLite, return JSON

@app.get("/api/positions")
async def get_positions():
    # read current_positions.json
```

Single `index.html` with fetch() calls. No framework. No build step. ~500 lines total.

---

## 10. Deployment Approach

### Systemd Services

```ini
# /etc/systemd/system/ai-trader.service
[Unit]
Description=AI Trading Decision Engine
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/.openclaw/workspace/signals/ai-trader
ExecStart=/usr/bin/python3 ai_trader.py
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=5
MemoryMax=1G
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/ai-dashboard.service
[Unit]
Description=AI Trader Dashboard
After=network.target ai-trader.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/.openclaw/workspace/signals/ai-trader
ExecStart=/usr/bin/python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Cron Additions

```cron
# AI Trader health check (every 5 min)
*/5 * * * * /root/.openclaw/workspace/signals/ai-trader/healthcheck.sh >> /tmp/ai-trader-health.log 2>&1

# SQLite backup (every 6 hours)
0 */6 * * * cp /root/.openclaw/workspace/signals/ai-trader/state/trader.db /root/.openclaw/workspace/signals/ai-trader/state/trader.db.bak

# Daily performance snapshot
0 0 * * * /usr/bin/python3 /root/.openclaw/workspace/signals/ai-trader/daily_report.py >> /tmp/ai-trader-daily.log 2>&1
```

### Startup Sequence

```
1. systemd starts ai-trader.service
2. ai_trader.py initializes:
   a. Load config.json
   b. Open/create SQLite DB (run migrations)
   c. Load current positions from bot
   d. Verify OpenRouter API key works (test call)
   e. Start event loop
3. systemd starts ai-dashboard.service
4. Cron runs healthcheck every 5 min
```

---

## 11. Resource Budget

| Component | CPU | RAM | Disk |
|---|---|---|---|
| Existing scanner (cron) | ~5% burst | ~100MB | - |
| Existing bot.py | ~2% | ~150MB | - |
| OpenClaw framework | ~5% | ~500MB | - |
| **AI Trader daemon** | ~5% avg, 20% during LLM call | ~100MB | ~50MB (DB+logs) |
| **Dashboard (FastAPI)** | ~2% | ~80MB | ~10MB |
| OS + misc | ~10% | ~500MB | ~33GB |
| **Total** | ~30-40% avg | ~1.4GB | ~33GB |
| **Available** | 60-70% headroom | ~6.6GB free | ~47GB free |

**Verdict:** Plenty of room. The LLM call is network-bound, not CPU-bound.

---

## 12. Implementation Phases

### Phase 1: Core Daemon (Mzinho)
- `ai_trader.py` — main loop, config, logging
- `context_builder.py` — reads signals.json + positions + history
- `db.py` — SQLite schema + helpers
- `safety.py` — hard limit checks
- systemd service file
- **Test:** Manual run, verify it reads signals and calls LLM

### Phase 2: Decision Pipeline (Mzinho)
- Prompt templates
- LLM call with timeout + retry + fallback
- Decision parser (JSON validation)
- Safety layer integration
- Bot execution bridge
- **Test:** Paper trading (log decisions, don't execute)

### Phase 3: Dashboard (Mzinho)
- FastAPI app
- HTML dashboard
- systemd service
- **Test:** Open browser, verify all pages work

### Phase 4: Monitoring & Hardening (Techno4k)
- Health check script
- Cron additions
- Alert integration (Telegram)
- Emergency shutdown logic
- Backup automation
- **Test:** Kill the daemon, verify watchdog catches it

### Phase 5: Live Trading (All)
- Paper trade for 48h minimum
- Review all decisions
- Gradual position size increase
- 24/7 monitoring first week

---

## 13. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| LLM gives bad trade advice | **Critical** | Safety layer rejects impossible trades. Max position caps. Daily drawdown kill switch. |
| LLM hallucinates market data | High | Context builder includes raw data; prompt emphasizes using provided data only. |
| OpenRouter outage | Medium | Fallback model. System holds positions during outage. |
| Cost overrun (model upgrades) | Low | Hard budget alert at $100/mo. Cheaper fallback model always available. |
| SQLite corruption | Medium | WAL mode + 6-hour backups + integrity checks. |
| VPS single point of failure | High | Accept it for now. Weekly DB exports to local backup. |
| Prompt injection via market data | Low | Safety layer validates decisions regardless of reasoning. |
| Over-trading (AI too active) | Medium | Minimum 3-min cycle. Max 3 positions. Hold is always safe. |
| Clock drift affecting funding arb | Low | NTP enforced. Funding rates are the primary signal — drift matters. |

---

## 14. Summary

| Item | Decision |
|---|---|
| Loop mechanism | Python daemon + systemd + cron watchdog |
| Cycle interval | 3 minutes |
| State storage | SQLite (WAL mode) |
| Primary LLM | Claude 3.5 Haiku via OpenRouter (~$35/mo) |
| Fallback LLM | Gemini 2.0 Flash (~$4/mo) |
| Dashboard | FastAPI + vanilla HTML/JS on :8080 |
| Monitoring | systemd + cron healthcheck + Telegram alerts |
| Total monthly cost | ~$40-50 (LLM only, VPS already paid) |
| Resource impact | ~15% CPU, ~180MB RAM additional |

**The golden rule:** When in doubt, hold. A missed trade costs nothing. A bad trade costs real money.
