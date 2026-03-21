# AI Autopilot — Implementation Spec

> 2026-03-20 | Blueprint for Mzinho

## TL;DR

Replace the current rule-based signal interpretation in `bot.py` with an LLM-driven decision engine.
The scanner still produces `signals.json`. The AI agent reads signals + positions + history, asks an LLM what to do, validates the answer against hard-coded safety rules, then passes approved decisions to the existing bot for execution. Human oversight via dashboard.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [What Changes, What Stays](#2-what-changes-what-stays)
3. [Data Flow](#3-data-flow)
4. [AI Trader Daemon](#4-ai-trader-daemon)
5. [Context Builder](#5-context-builder)
6. [Prompt Design](#6-prompt-design)
7. [Decision Schema](#7-decision-schema)
8. [Safety Layer](#8-safety-layer)
9. [Bot Integration](#9-bot-integration)
10. [Learning & Memory](#10-learning--memory)
11. [Dashboard](#11-dashboard)
12. [Deployment](#12-deployment)
13. [Phased Build Plan](#13-phased-build-plan)
14. [Config Reference](#14-config-reference)
15. [Failure Modes](#15-failure-modes)

---

## 1. System Overview

```
Current system (rule-based):
  Scanner → signals.json → Bot reads & opens top 3 by score → DSL manages exits

AI Autopilot (LLM-driven):
  Scanner → signals.json → AI Agent → LLM decision → Safety check → Bot executes → DSL manages exits
                                         ↓
                                    SQLite journal
                                         ↓
                                    Dashboard + Telegram
```

**The AI replaces step 2 (deciding what to trade), not step 1 (finding signals) or step 3 (managing exits).** The scanner stays. The DSL stays. What changes is the middle.

---

## 2. What Changes, What Stays

### Stays unchanged
- `opportunity-scanner.ts` — still runs on cron, produces `signals.json`
- `DSLState` / trailing stop logic in `bot.py` — manages exits
- `LighterAPI` — exchange communication
- `TelegramAlerter` — alerts

### New components
- `ai_trader.py` — main daemon (replaces signal-based `_process_signals`)
- `context_builder.py` — assembles LLM prompt from signals + positions + history
- `safety.py` — hard-coded rule engine, sits between LLM and bot
- `db.py` — SQLite helpers for decision journal
- `prompts/system.txt` + `prompts/decision.txt` — prompt templates
- `dashboard.py` — FastAPI web UI

### Modified components
- `bot.py` — add a `process_ai_decision(decision: dict)` method that replaces `_process_signals`. The bot becomes a pure executor: "here's what the AI decided, do it."
- `config.yml` — add AI trader settings

---

## 3. Data Flow

### Cycle (every 3 minutes)

```
1. Read signals.json (scanner output)
2. Read current positions from bot
3. Read recent decisions from SQLite (last 20)
4. Read trade outcomes from SQLite (last 10 closed trades)
5. Read strategy memory from disk
6. Build prompt (context_builder.py)
7. Call LLM via OpenRouter
8. Parse JSON response (validate schema)
9. Safety check (safety.py)
10. If approved → send decision to bot
11. Log everything to SQLite
12. Update strategy memory if cycle had a closed trade outcome
```

### Decision → Bot interface

The AI outputs a decision object. The bot receives it and either:
- `action: "open"` → calls `api.open_position()`
- `action: "close"` → calls `api.execute_sl()` or close market order
- `action: "adjust"` → modify existing position (not MVP)
- `action: "hold"` → do nothing

The bot does NOT decide. It only executes or rejects.

---

## 4. AI Trader Daemon

### File: `ai-trader/ai_trader.py`

```python
class AITrader:
    CYCLE_INTERVAL = 180  # 3 minutes
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, config: dict):
        self.config = config
        self.context_builder = ContextBuilder(config)
        self.safety = SafetyLayer(config)
        self.db = DecisionDB(config["db_path"])
        self.llm = LLMClient(config["llm"])
        self.running = True
        self.consecutive_failures = 0
        self.emergency_halt = False

    async def run_forever(self):
        while self.running and not self.emergency_halt:
            start = time.time()
            try:
                await self.execute_cycle()
                self.consecutive_failures = 0
            except Exception as e:
                self.consecutive_failures += 1
                log.error(f"Cycle failed ({self.consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}): {e}")
                if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self.emergency_halt = True
                    log.critical("🚨 Emergency halt — 3+ consecutive failures")
                    await self.alert("CRITICAL: AI trader halted after 3 consecutive failures")

            elapsed = time.time() - start
            await asyncio.sleep(max(0, self.CYCLE_INTERVAL - elapsed))

    async def execute_cycle(self):
        cycle_id = str(uuid.uuid4())[:8]

        # 1. Gather context
        signals = self.context_builder.read_signals()
        positions = self.context_builder.read_positions()
        history = self.context_builder.read_recent_decisions(limit=20)
        outcomes = self.context_builder.read_recent_outcomes(limit=10)
        memory = self.context_builder.read_strategy_memory()

        # 2. Build prompt
        prompt = self.context_builder.build_prompt(
            signals, positions, history, outcomes, memory
        )

        # 3. Call LLM
        t0 = time.time()
        raw_response = await self.llm.call(prompt)
        latency_ms = int((time.time() - t0) * 1000)

        # 4. Parse
        decision = parse_decision_json(raw_response)

        # 5. Safety check
        safe, reasons = self.safety.validate(decision, positions, signals)

        # 6. Execute (if approved)
        executed = False
        if safe and decision["action"] != "hold":
            executed = await self.send_to_bot(decision)

        # 7. Log
        self.db.log_decision(
            cycle_id=cycle_id,
            decision=decision,
            safety_approved=safe,
            safety_reasons=reasons,
            executed=executed,
            positions_snapshot=positions,
            signals_snapshot=signals,
            latency_ms=latency_ms,
        )

        # 8. Check kill switch
        daily_dd = self.safety.get_daily_drawdown()
        if daily_dd > self.config["max_daily_drawdown_pct"]:
            self.emergency_halt = True
            await self.close_all_positions()
            await self.alert(f"🚨 Kill switch: {daily_dd:.1f}% daily drawdown")

    async def send_to_bot(self, decision: dict) -> bool:
        """Write decision to shared file for bot to consume."""
        decision_path = Path(self.config["decision_file"])
        with open(decision_path, "w") as f:
            json.dump(decision, f, indent=2)
        return True
```

### Lifecycle
- systemd manages process (auto-restart on crash)
- Cron health check every 5 min (verifies daemon alive + last cycle < 10 min ago)
- Graceful shutdown on SIGTERM (finish current cycle, don't start new one)

---

## 5. Context Builder

### File: `ai-trader/context_builder.py`

Builds the prompt from multiple data sources. Key principle: **compress, don't dump**.

```python
class ContextBuilder:
    def build_prompt(self, signals, positions, history, outcomes, memory):
        """Assemble the LLM prompt from compressed context."""

        sections = []

        # 1. Current positions (compressed)
        if positions:
            pos_summary = []
            for p in positions:
                roe = (p["current_price"] - p["entry_price"]) / p["entry_price"]
                if p["side"] == "short":
                    roe = -roe
                pos_summary.append(
                    f"{p['symbol']} {p['side'].upper()} ${p['size_usd']:.0f} "
                    f"@ {p['entry_price']:.4f} (ROE: {roe:+.1f}%)"
                )
            sections.append("## Open Positions\n" + "\n".join(pos_summary))
        else:
            sections.append("## Open Positions\nNone")

        # 2. Market opportunities (from scanner, top 10 only)
        top_signals = sorted(signals, key=lambda s: s["compositeScore"], reverse=True)[:10]
        sig_summary = []
        for s in top_signals:
            sig_summary.append(
                f"{s['symbol']}: score={s['compositeScore']} "
                f"dir={s.get('direction', '?')} "
                f"funding={s.get('fundingSpread8h', 0):.3f}% "
                f"vol=${s.get('dailyVolumeUsd', 0)/1000:.0f}K "
                f"mom={s.get('dailyPriceChange', 0):+.1f}% "
                f"safety={'✅' if s.get('safetyPass') else '❌'}"
            )
        sections.append("## Market Opportunities (Top 10)\n" + "\n".join(sig_summary))

        # 3. Recent outcomes (last 5 closed trades)
        if outcomes:
            out_lines = []
            for o in outcomes:
                emoji = "🟢" if o["pnl"] > 0 else "🔴"
                out_lines.append(
                    f"{emoji} {o['symbol']} {o['direction']} → PnL: ${o['pnl']:+.2f} ({o['pnl_pct']:+.1f}%) "
                    f"held {o['hold_time']}"
                )
            sections.append("## Recent Trade Outcomes\n" + "\n".join(out_lines))

        # 4. Strategy memory (learned patterns)
        if memory:
            sections.append(f"## Learned Patterns\n{memory}")

        # 5. Account state
        sections.append(
            f"## Account\n"
            f"- Equity: ${self.get_equity():.2f}\n"
            f"- Unrealized PnL: ${self.get_unrealized_pnl():.2f}\n"
            f"- Daily realized PnL: ${self.get_daily_realized_pnl():.2f}\n"
            f"- Open positions: {len(positions)}/3 max"
        )

        return "\n\n".join(sections)
```

### What goes into the prompt (~800-1200 tokens)

| Section | Tokens | Update frequency |
|---------|--------|-----------------|
| System prompt | ~400 | Static |
| Open positions | ~50-200 | Every cycle |
| Top 10 opportunities | ~200-400 | Every 5 min (scanner) |
| Recent outcomes | ~100-200 | When trades close |
| Strategy memory | ~100-300 | Periodic (reflection) |
| Account state | ~50 | Every cycle |
| **Total input** | **~900-1500** | |
| **Expected output** | **~200-400** | JSON decision |

---

## 6. Prompt Design

### System Prompt (`prompts/system.txt`)

```
You are a crypto perpetual futures trading AI. You manage positions on Lighter.xyz.

YOUR ROLE: Analyze market data and current positions. Decide whether to open, close, or hold.

RULES:
- Max 3 concurrent positions
- Max 20x leverage (the safety layer enforces this)
- Every position must have a stop loss
- If uncertain, HOLD. A missed trade costs nothing. A bad trade costs real money.
- You see filtered, pre-computed data. Trust the scanner scores.
- Be specific about WHY you want to act. Vague reasoning = hold.

DECISION TYPES:
- "open": Open a new position. Requires: symbol, direction, size_usd, leverage, stop_loss_pct
- "close": Close an existing position. Requires: symbol, reason
- "hold": Do nothing. Always safe.

THINK ABOUT:
1. Is the current market regime favorable? (funding rates, volume, momentum)
2. Are my current positions working? (ROE trend, time held)
3. Do the top signals justify action? (score, safety, diversity)
4. Am I overtrading? (recent frequency, win rate)

Output ONLY valid JSON. No explanation outside the JSON.
```

### Decision Prompt Template (`prompts/decision.txt`)

```
{context}

Based on this data, output your trading decision as JSON:
{{
  "action": "open" | "close" | "hold",
  "symbol": "TOKEN" | null,
  "direction": "long" | "short" | null,
  "size_pct_equity": 0.0-5.0,
  "leverage": 1.0-20.0,
  "stop_loss_pct": 0.5-10.0,
  "reasoning": "why you chose this",
  "confidence": 0.0-1.0
}}
```

---

## 7. Decision Schema

### LLM Output → Parsed Decision

```json
{
  "action": "open",
  "symbol": "ROBO",
  "direction": "short",
  "size_pct_equity": 5.0,
  "leverage": 3.0,
  "stop_loss_pct": 5.0,
  "reasoning": "Extreme negative funding (-4%/8h) creates strong short bias. MA alignment bearish. Volume supports the move. Risk is 5% equity with SL at 5% distance.",
  "confidence": 0.75
}
```

### Schema Validation Rules

```python
DECISION_SCHEMA = {
    "required": ["action", "reasoning", "confidence"],
    "action": {"enum": ["open", "close", "hold"]},
    "confidence": {"type": "float", "min": 0.0, "max": 1.0},
    # If action == "open":
    "open_required": ["symbol", "direction", "size_pct_equity", "leverage", "stop_loss_pct"],
    "direction": {"enum": ["long", "short"]},
    "size_pct_equity": {"type": "float", "min": 0.1, "max": 5.0},
    "leverage": {"type": "float", "min": 1.0, "max": 20.0},
    "stop_loss_pct": {"type": "float", "min": 0.5, "max": 15.0},
    # If action == "close":
    "close_required": ["symbol"],
}
```

### Conversion to bot execution

```python
def decision_to_bot_action(decision: dict, equity: float) -> dict:
    if decision["action"] == "open":
        return {
            "action": "open",
            "symbol": decision["symbol"],
            "direction": decision["direction"],
            "size_usd": equity * decision["size_pct_equity"] / 100,
            "leverage": decision["leverage"],
            "stop_loss_pct": decision["stop_loss_pct"],
        }
    elif decision["action"] == "close":
        return {
            "action": "close",
            "symbol": decision["symbol"],
        }
    else:
        return {"action": "hold"}
```

---

## 8. Safety Layer

### File: `ai-trader/safety.py`

**The LLM proposes. Safety disposes.** Every decision passes through these checks BEFORE reaching the bot.

```python
class SafetyLayer:
    # ── Hard limits (cannot be overridden by LLM or prompt) ──

    MAX_POSITIONS = 3
    MAX_LEVERAGE = 20.0
    MAX_SIZE_PCT_EQUITY = 5.0
    MAX_DAILY_DRAWDOWN_PCT = 10.0
    MAX_TOTAL_EXPOSURE_PCT = 15.0  # of equity
    MIN_CONFIDENCE = 0.3
    MIN_SCORE_FOR_OPEN = 60  # scanner composite score
    REQUIRED_STOP_LOSS = True
    MIN_STOP_LOSS_PCT = 0.5
    MAX_ORDERS_PER_HOUR = 12  # rate limit
    COOLDOWN_AFTER_LOSS_SECONDS = 300  # 5 min pause after SL hit

    def validate(self, decision: dict, positions: list, signals: list) -> tuple[bool, list[str]]:
        reasons = []

        # Hold is always safe
        if decision["action"] == "hold":
            return True, ["hold — always safe"]

        # ── Format checks ──
        if decision.get("confidence", 0) < self.MIN_CONFIDENCE:
            reasons.append(f"confidence {decision.get('confidence', 0):.2f} < {self.MIN_CONFIDENCE}")

        if decision["action"] == "open":
            return self._validate_open(decision, positions, signals, reasons)
        elif decision["action"] == "close":
            return self._validate_close(decision, positions, reasons)

        return len(reasons) == 0, reasons

    def _validate_open(self, decision, positions, signals, reasons):
        symbol = decision.get("symbol", "")

        # Position count
        if len(positions) >= self.MAX_POSITIONS:
            reasons.append(f"max positions ({self.MAX_POSITIONS}) reached")

        # Already in this market
        if any(p["symbol"] == symbol for p in positions):
            reasons.append(f"already have position in {symbol}")

        # Leverage
        leverage = decision.get("leverage", 0)
        if leverage > self.MAX_LEVERAGE:
            reasons.append(f"leverage {leverage}x > max {self.MAX_LEVERAGE}x")

        # Size
        size_pct = decision.get("size_pct_equity", 0)
        if size_pct > self.MAX_SIZE_PCT_EQUITY:
            reasons.append(f"size {size_pct}% > max {self.MAX_SIZE_PCT_EQUITY}%")

        # Stop loss required
        sl_pct = decision.get("stop_loss_pct")
        if self.REQUIRED_STOP_LOSS and (not sl_pct or sl_pct < self.MIN_STOP_LOSS_PCT):
            reasons.append(f"stop loss {sl_pct}% invalid (min {self.MIN_STOP_LOSS_PCT}%)")

        # Signal score floor
        matching_signal = next((s for s in signals if s["symbol"] == symbol), None)
        if matching_signal and matching_signal.get("compositeScore", 0) < self.MIN_SCORE_FOR_OPEN:
            reasons.append(f"scanner score {matching_signal['compositeScore']} < {self.MIN_SCORE_FOR_OPEN}")

        # Scanner safety check
        if matching_signal and not matching_signal.get("safetyPass", False):
            reasons.append(f"scanner safety check failed: {matching_signal.get('safetyReason', '?')}")

        # Rate limit
        if not self._check_rate_limit():
            reasons.append("rate limit exceeded")

        # Cooldown after loss
        if self._in_cooldown():
            reasons.append("cooldown period after recent loss")

        # Daily drawdown
        if self.get_daily_drawdown() > self.MAX_DAILY_DRAWDOWN_PCT * 0.8:  # 80% of max = warn
            reasons.append(f"approaching daily drawdown limit ({self.get_daily_drawdown():.1f}%)")

        return len(reasons) == 0, reasons

    def _validate_close(self, decision, positions, reasons):
        symbol = decision.get("symbol", "")
        if not any(p["symbol"] == symbol for p in positions):
            reasons.append(f"no position in {symbol} to close")
        return len(reasons) == 0, reasons

    def get_daily_drawdown(self) -> float:
        """Calculate today's realized + unrealized PnL as % of starting equity."""
        # Query SQLite for today's realized PnL
        # Add current unrealized PnL from positions
        # Return negative value as percentage
        ...
```

### Kill Switch

```python
async def check_kill_switch(self):
    """Called every cycle. Halts trading if thresholds breached."""
    triggers = []

    if self.get_daily_drawdown() > self.MAX_DAILY_DRAWDOWN_PCT:
        triggers.append(f"Daily drawdown {self.get_daily_drawdown():.1f}% > {self.MAX_DAILY_DRAWDOWN_PCT}%")

    if self.consecutive_failures >= 3:
        triggers.append(f"{self.consecutive_failures} consecutive LLM failures")

    consecutive_rejections = self.db.count_recent_rejections(minutes=30)
    if consecutive_rejections >= 5:
        triggers.append(f"{consecutive_rejections} safety rejections in 30 min")

    if triggers:
        self.emergency_halt = True
        await self.close_all_positions()
        await self.alert(f"🚨 KILL SWITCH:\n" + "\n".join(triggers))
        # Only manual restart can resume trading
```

---

## 9. Bot Integration

### How the AI trader communicates with `bot.py`

**Mechanism: shared JSON file** (simple, debuggable, no IPC complexity).

```
ai_trader.py writes → /root/.openclaw/workspace/signals/ai-decision.json
bot.py reads → consumes decision in _tick()
bot.py writes → /root/.openclaw/workspace/signals/ai-result.json (outcome)
ai_trader.py reads → outcome for journal + learning
```

### Bot changes (`bot.py` additions)

```python
class LighterCopilot:
    def __init__(self, cfg):
        # ... existing ...
        self._ai_decision_file = "/root/.openclaw/workspace/signals/ai-decision.json"
        self._ai_result_file = "/root/.openclaw/workspace/signals/ai-result.json"
        self._last_ai_decision_ts: str | None = None

    async def _process_ai_decision(self):
        """Read AI decision and execute if valid."""
        path = Path(self._ai_decision_file)
        if not path.exists():
            return

        try:
            with open(path) as f:
                decision = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        # Only process new decisions
        ts = decision.get("timestamp", "")
        if ts == self._last_ai_decision_ts:
            return
        self._last_ai_decision_ts = ts

        action = decision.get("action")

        if action == "open":
            await self._execute_ai_open(decision)
        elif action == "close":
            await self._execute_ai_close(decision)
        # hold → do nothing

        # Write result back
        self._write_ai_result(decision, success=True)

    async def _execute_ai_open(self, decision):
        symbol = decision["symbol"]
        direction = decision["direction"]
        size_usd = decision["size_usd"]

        # Find market ID from positions or scanner data
        market_id = self._resolve_market_id(symbol)
        if market_id is None:
            logging.warning(f"AI decision: unknown symbol {symbol}")
            return

        if market_id in self.tracker.positions:
            logging.info(f"AI decision: already in {symbol}, skipping")
            return

        # Cap to 3 concurrent signal positions
        if len(self.tracker.positions) >= 3:
            logging.info("AI decision: max positions reached")
            return

        is_long = direction == "long"
        current_price = await self.api.get_price(market_id)
        if not current_price:
            return

        success = await self.api.open_position(market_id, size_usd, is_long, current_price)
        if success:
            actual_size = size_usd / current_price
            self.tracker.add_position(market_id, symbol, direction, current_price, actual_size)
            await self.alerts.send(
                f"🤖 *AI → OPENED*\n"
                f"{direction.upper()} {symbol}\n"
                f"Size: ${size_usd:.2f}\n"
                f"Reason: {decision.get('reasoning', '?')[:200]}"
            )

    # In _tick(), replace _process_signals with _process_ai_decision:
    async def _tick(self):
        if not self.api:
            return
        await self._sync_positions()
        await self._process_ai_decision()  # was _process_signals()
        await self._update_prices()
        await self._check_triggers()
```

### Disable rule-based signals when AI is active

When the AI trader daemon is running, the bot should NOT process `signals.json` directly.
Option A: Config flag `ai_mode: true` in `config.yml` skips `_process_signals()`.
Option B: Remove `_process_signals()` call from `_tick()` entirely (AI replaces it).

Recommend **Option A** — keep both code paths, toggle via config. Makes rollback easy.

---

## 10. Learning & Memory

### Trade Journal (SQLite)

Every cycle logs to `decisions` table. When a position closes, the outcome gets logged to `outcomes` table.

```sql
CREATE TABLE outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cycle_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT,
    entry_price REAL,
    exit_price REAL,
    size_usd REAL,
    pnl_usd REAL,
    pnl_pct REAL,
    hold_time_seconds INTEGER,
    max_drawdown_pct REAL,
    exit_reason TEXT,  -- 'sl_hit', 'dsl_triggered', 'ai_close', 'manual'
    decision_snapshot TEXT  -- the original AI decision JSON
);
```

### Reflection Agent (periodic, not every cycle)

Runs once every 2-4 hours or after N closed trades. Cheaper model (Gemini Flash).

```python
class ReflectionAgent:
    """Periodically reviews trade outcomes and updates strategy memory."""

    async def reflect(self, db: DecisionDB):
        outcomes = db.get_outcomes_since(hours=24)
        if len(outcomes) < 3:
            return  # not enough data

        # Build reflection prompt
        prompt = self._build_reflection_prompt(outcomes)

        # Call cheaper LLM
        response = await self.llm.call(
            model="xiaomi/mimo-v2-pro",
            prompt=prompt
        )

        # Update strategy memory file
        memory_path = Path("state/strategy_memory.md")
        memory_path.write_text(response)

    def _build_reflection_prompt(self, outcomes):
        lines = ["Review these recent trades and extract patterns:\n"]
        for o in outcomes:
            emoji = "🟢" if o["pnl"] > 0 else "🔴"
            lines.append(
                f"{emoji} {o['symbol']} {o['direction']} → ${o['pnl']:+.2f} "
                f"({o['pnl_pct']:+.1f}%) held {o['hold_time_seconds']//60}min "
                f"exit: {o['exit_reason']}"
            )
        lines.append(
            "\nWhat patterns do you see? What should change? "
            "Write 3-5 concise bullet points for the trading strategy. "
            "Focus on actionable adjustments, not descriptions."
        )
        return "\n".join(lines)
```

### Strategy Memory File (`state/strategy_memory.md`)

```markdown
## Learned Patterns (auto-updated by reflection agent)

- Weekend volume is thin: reduce position size 30% on Sat/Sun
- Funding rate > 0.05% 8h → contrarian short often wins within 4h
- Our SHORT win rate is 40% vs LONG 65% → bias toward longs
- Positions held > 12h tend to deteriorate → consider time-based exits
- After 2 consecutive losses, wait 30 min before new entry
```

This file gets injected into every decision prompt as context.

---

## 11. Dashboard

### File: `ai-trader/dashboard.py`

FastAPI app on `:8080`. Vanilla HTML/JS frontend. No frameworks.

### Pages

| Page | Shows |
|------|-------|
| `/` | Status: daemon alive, last cycle, model, latency, equity curve sparkline |
| `/positions` | Open positions with live PnL, entry vs current price |
| `/decisions` | Last 50 decisions, expandable reasoning, safety pass/fail |
| `/performance` | Win rate, avg win/loss, Sharpe, max drawdown, daily PnL chart |
| `/alerts` | Active alerts, acknowledgment |

### API Endpoints

```
GET /api/status          → { alive, last_cycle, model, latency_ms, equity }
GET /api/positions       → [ { symbol, side, size_usd, entry, current, pnl, roe } ]
GET /api/decisions?n=50  → [ { timestamp, action, symbol, reasoning, confidence, safe, executed } ]
GET /api/performance     → { win_rate, avg_win, avg_loss, total_pnl, max_dd, trades_today }
GET /api/alerts          → [ { timestamp, level, message } ]
```

### Frontend

Single `index.html` with `fetch()` calls. CSS grid layout. Auto-refresh every 10 seconds via `setInterval`. ~500 lines total.

```html
<!DOCTYPE html>
<html>
<head>
  <title>AI Trader</title>
  <style>
    /* Dark theme, compact layout, ~200 lines CSS */
  </style>
</head>
<body>
  <div id="status"></div>
  <div id="positions"></div>
  <div id="decisions"></div>
  <script>
    async function refresh() {
      const [status, positions, decisions] = await Promise.all([
        fetch('/api/status').then(r => r.json()),
        fetch('/api/positions').then(r => r.json()),
        fetch('/api/decisions?n=20').then(r => r.json()),
      ]);
      renderStatus(status);
      renderPositions(positions);
      renderDecisions(decisions);
    }
    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
```

---

## 12. Deployment

### File structure

```
signals/ai-trader/
├── ai_trader.py           # Main daemon
├── context_builder.py     # Prompt assembly
├── safety.py              # Rule engine
├── db.py                  # SQLite helpers
├── llm_client.py          # OpenRouter wrapper
├── dashboard.py           # FastAPI app
├── reflection.py          # Periodic learning agent
├── config.json            # All settings
├── prompts/
│   ├── system.txt         # System prompt
│   └── decision.txt       # Decision template
├── state/
│   ├── trader.db          # SQLite (decisions, outcomes, alerts)
│   ├── strategy_memory.md # Learned patterns
│   └── ai-decision.json   # Shared with bot.py
├── static/
│   └── index.html         # Dashboard UI
└── logs/
    └── ai-trader.log      # Rotating log
```

### systemd services

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
MemoryMax=512M
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Cron additions

```cron
# Health check every 5 min
*/5 * * * * /root/.openclaw/workspace/signals/ai-trader/healthcheck.sh

# SQLite backup every 6 hours
0 */6 * * * cp /root/.openclaw/workspace/signals/ai-trader/state/trader.db \
              /root/.openclaw/workspace/signals/ai-trader/state/trader.db.bak

# Reflection agent every 4 hours
0 */4 * * * /usr/bin/python3 /root/.openclaw/workspace/signals/ai-trader/reflection.py
```

---

## 13. Phased Build Plan

### Phase 1: Core Daemon (ai_trader.py + context_builder.py + safety.py + db.py)
- Daemon loop with 3-min interval
- Reads signals.json + positions
- Calls LLM, parses JSON
- Safety validates
- Logs to SQLite
- **Test:** Run in paper mode (log decisions, don't execute). Verify output.

### Phase 2: Bot Integration
- Add `_process_ai_decision()` to bot.py
- Shared JSON file communication
- Config toggle `ai_mode: true/false`
- **Test:** Run both daemon + bot. Verify decisions flow through.

### Phase 3: Dashboard
- FastAPI app + static HTML
- Read from SQLite, display decisions + positions
- **Test:** Open browser, verify all pages.

### Phase 4: Reflection Agent + Learning
- Periodic reflection (cheaper model)
- Strategy memory file
- Inject into decision prompt
- **Test:** Run 48h, check if patterns emerge.

### Phase 5: Monitoring & Hardening
- Health check cron
- Telegram alerts integration
- Kill switch testing
- Rate limiting
- **Test:** Kill daemon, verify watchdog. Trigger drawdown, verify kill switch.

### Phase 6: Live Trading (paper → real)
- Paper trade 48h minimum
- Review all AI decisions
- Gradual position size increase
- 24/7 monitoring first week

---

## 14. Config Reference

### `ai-trader/config.json`

```json
{
  "db_path": "state/trader.db",
  "decision_file": "../ai-decision.json",
  "signals_file": "../signals/signals.json",
  "strategy_memory_file": "state/strategy_memory.md",

  "cycle_interval_seconds": 180,

  "llm": {
    "primary_model": "xiaomi/mimo-v2-omni",
    "fallback_model": "xiaomi/mimo-v2-pro",
    "reflection_model": "xiaomi/mimo-v2-pro",
    "api_base": "https://openrouter.ai/api/v1",
    "timeout_seconds": 30,
    "max_retries": 2
  },

  "safety": {
    "max_positions": 3,
    "max_leverage": 20.0,
    "max_size_pct_equity": 5.0,
    "max_daily_drawdown_pct": 10.0,
    "max_total_exposure_pct": 15.0,
    "min_confidence": 0.3,
    "min_scanner_score": 60,
    "required_stop_loss": true,
    "min_stop_loss_pct": 0.5,
    "max_orders_per_hour": 12,
    "cooldown_after_loss_seconds": 300
  },

  "dashboard": {
    "host": "0.0.0.0",
    "port": 8080
  },

  "alerting": {
    "telegram_token": "...",
    "telegram_chat_id": "1736401643",
    "alert_on_decision": false,
    "alert_on_open": true,
    "alert_on_close": true,
    "alert_on_halt": true
  }
}
```

---

## 15. Failure Modes

| Failure | Detection | Response | Recovery |
|---------|-----------|----------|----------|
| LLM timeout (>30s) | asyncio timeout | Skip cycle, hold positions | Next cycle normal |
| LLM returns garbage | JSON parse error | Retry once, then skip | Log + next cycle |
| LLM returns valid JSON but bad trade | Safety layer rejects | Log rejection, don't execute | Next cycle normal |
| OpenRouter 429 | HTTP status | Backoff 60s, retry | Fallback model if >3 retries |
| OpenRouter 5xx | HTTP status | Retry 2x with backoff | Fallback model |
| OpenRouter down | Connection error | Hold all, alert | Fallback model, manual check |
| 3+ consecutive LLM failures | Counter | Emergency halt | Manual restart required |
| Daily drawdown > 10% | Safety layer | Close all, halt, alert | Manual review |
| 5+ safety rejections in 30 min | DB counter | Emergency halt | Prompt/model issue, manual |
| Bot dies mid-execution | Position mismatch on next sync | Alert, don't retry (no double trade) | Manual review |
| SQLite corruption | Integrity check in healthcheck | Restore from 6h backup | Automatic |
| Daemon crash | systemd watchdog | Auto-restart (max 5/60s) | Automatic |
| Disk full | Healthcheck | Halt trading, alert | Manual cleanup |
| VPS OOM | systemd MemoryMax | Restart with limit | Increase swap |

**Core philosophy:** Every failure mode leads to HOLD. The system never acts on bad or missing data. HOLD is free. Bad trades aren't.

---

## Quick Start (for Mzinho)

1. Read this spec end to end
2. Read `executor/bot.py` — understand current signal processing and position management
3. Read `signals/scripts/opportunity-scanner.ts` — understand signals.json format
4. Build Phase 1: `ai_trader.py` + `context_builder.py` + `safety.py` + `db.py`
5. Test in paper mode (log decisions to console + SQLite, don't touch the bot)
6. Show results, then we integrate
