# Trader V2 — Architecture Plan

## Philosophy
- **Modular from day 1.** Every component is swappable.
- **Interfaces first.** Define contracts before implementations.
- **No shortcuts.** No "hardcode for now." No "we'll fix it later."
- **Current bot keeps running.** V2 is built in parallel, not refactored from v1.

## Dependencies
- `aiohttp` — TradingView webhook HTTP server
- `lighter-sdk` (or direct REST/WS) — exchange API
- `sqlite3` — trade DB (stdlib)
- Python 3.12+

## Services Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Signal Sources                                │
│  ┌──────────┐  ┌───────────────┐  ┌─────────────────┐  ┌─────────┐  │
│  │ Scanner  │  │ TradingView   │  │ V1 Scanner      │  │ Manual  │  │
│  │ V2       │  │ Webhook       │  │ Adapter         │  │         │  │
│  └────┬─────┘  └──────┬────────┘  └────────┬────────┘  └────┬────┘  │
│       │               │                    │                │        │
│       │    pipeline: "full" or "direct"    │                │        │
└───────┼───────────────┼────────────────────┼────────────────┼────────┘
        │               │                    │                │
        ▼               │                    ▼                │
  ┌──────────┐          │              ┌──────────┐          │
  │ AI Engine│ ◄────────┘              │ Direct   │ ◄────────┘
  │ (optional│                         │ to Bot   │
  └────┬─────┘                         └────┬─────┘
       │                                    │
       ▼                                    ▼
  ┌──────────────────────────────────────────────┐
  │               Bot / Position Manager          │
  │  ┌─────────────────────────────────────────┐  │
  │  │  StrategyFactory → ExitStrategy         │  │
  │  │  (DSL | Trailing | ATR | Fixed | Custom)│  │
  │  └─────────────────────────────────────────┘  │
  └──────────────────┬───────────────────────────┘
                     │
                     ▼
               ┌──────────┐
               │ Exchange │
               │ (Lighter)│
               └──────────┘
```

**Key insight: AI is optional.** With "direct" pipeline, signals go straight from source to bot. The AI engine only runs when a source configures `pipeline: "full"`.

**Key insight: Scanner and Exit Strategy are the tuning knobs.** Everything else is stable plumbing.

## Shared Contract: Signal

All services communicate through a single data type:

```python
@dataclass
class Signal:
    id: str                    # unique signal ID
    symbol: str                # e.g. "BTC-PERP"
    direction: str             # "long" | "short"
    type: str                  # detector name: "rsi_oversold", "momentum_breakout", etc.
    strength: float            # 0.0 - 1.0
    price: float               # price at signal time
    timestamp: str             # ISO-8601
    metadata: dict             # engine-specific context (non-essential, for logging/debug)
    atr: float | None = None   # optional ATR at signal time (for dynamic strategies)
```

## Files
- [Scanner Plan](plan-scanner.md)
- [AI Engine Plan](plan-ai.md)
- [Bot Plan](plan-bot.md)
- [Interfaces](plan-interfaces.md)

## Modular Priority (tuned from John's insight)

Not everything needs the same level of abstraction. The real experimentation happens in two places:

| Component | Swap Frequency | Modularity Priority |
|-----------|---------------|-------------------|
| **Scanner** | High — different detectors, data sources, markets | 🔴 Critical — clean Signal interface |
| **Exit Strategy** | High — DSL, trailing, ATR, fixed, composites | 🔴 Critical — clean ExitStrategy interface |
| **Position Manager** | Low — tick loop is a tick loop | 🟡 Clean but not over-abstracted |
| **AI Engine** | Low — logic evolves but doesn't get "swapped" | 🟡 Clean but not over-abstracted |
| **Order Executor** | Very Low — exchange API is stable | 🟢 Simple wrapper, thin interface |

**Focus the abstraction effort where it matters: Scanner + Exit Strategy.**

## Top-Level File Structure

```
autopilot-trader-v2/
├── app/                       # Orchestrator — wiring layer
│   ├── __init__.py
│   ├── main.py                # Entry point, lifecycle, signal poll loop
│   ├── pipeline.py            # PipelineRouter: full vs direct
│   └── config.py              # Top-level config parsing
├── interfaces/                # Pure contracts — NO imports
│   ├── __init__.py
│   ├── types.py               # Signal, Position, ExitResult, Decision, Outcome, MarketContext
│   ├── signal_source.py       # SignalSource ABC
│   ├── exit_strategy.py       # ExitStrategy ABC + ExitAction enum
│   ├── executor.py            # OrderExecutor ABC
│   └── decision_engine.py     # DecisionEngine ABC
├── sources/                   # SignalSource implementations
│   ├── __init__.py
│   ├── scanner_v2/            # Dual-engine scanner
│   │   ├── __init__.py
│   │   ├── scanner.py         # ScannerV2 (SignalSource impl)
│   │   ├── data_collector.py  # REST polling + WS
│   │   ├── merge.py           # Signal merge/dedup
│   │   ├── atr.py             # ATR calculation
│   │   └── engines/
│   │       ├── __init__.py
│   │       ├── classic.py     # RSI, EMA, MACD, Volume
│   │       └── smart.py       # Breakout, OI Div, Liquidity, etc.
│   ├── tradingview_webhook.py # HTTP webhook receiver
│   ├── manual.py              # CLI/JSON input for testing
│   └── v1_adapter.py          # Wraps v1 TS scanner output
├── bot/                       # Position management + execution
│   ├── (see plan-bot.md)
├── ai/                        # Decision engines (optional)
│   ├── (see plan-ai.md)
├── config.example.yml         # Full example config
├── requirements.txt           # aiohttp, lighter-sdk, etc.
└── tests/                     # Integration tests
    ├── test_pipeline_routing.py
    ├── test_paper_executor.py
    └── test_e2e.py
```

## Unified Config Example (config.yml)

```yaml
# --- Signal Sources ---
sources:
  - type: "scanner_v2"
    pipeline: "full"                          # scanner → AI → bot
    poll_interval_seconds: 60
    candle_interval: 5m
    candle_count: 100
    markets: [BTC-PERP, ETH-PERP, SOL-PERP]
    engines:
      classic:
        enabled: true
        rsi_period: 14
        rsi_oversold: 30
        rsi_overbought: 70
        ema_fast: 9
        ema_slow: 21
      smart:
        enabled: true
        momentum_lookback: 20
    merge:
      min_strength: 0.3
      dedup_window_seconds: 300

  - type: "tradingview_webhook"
    pipeline: "direct"                        # TV → bot (no AI)
    port: 8080
    secret: "your_webhook_token"
    symbol_map:
      BTCUSDT: "BTC-PERP"

# --- AI Engine (only used by "full" pipeline sources) ---
ai_engine:
  strategy: "ai"                              # "ai" | "rules" | "always_open"
  model: "openrouter/anthropic/claude-sonnet-4"
  max_tokens: 500
  timeout_seconds: 30
  min_score: 60
  max_positions: 5
  cooldown_after_loss_minutes: 15

# --- Bot ---
bot:
  max_positions: 5
  default_size_usd: 5.0
  default_leverage: 10
  tick_interval_seconds: 2

  exit_strategy:
    default: "dsl"
    dsl:
      hard_sl_pct: 1.0
      stagnation_move_pct: 0.5
      stagnation_minutes: 90
      tiers:
        - trigger_pct: 0.75
          trailing_buffer_pct: 0.25
          consecutive_breaches: 3
        - trigger_pct: 1.5
          trailing_buffer_pct: 0.4
          consecutive_breaches: 3
        - trigger_pct: 3.0
          trailing_buffer_pct: 0.5
          consecutive_breaches: 3
    trailing_sl:
      trigger_pct: 0.5
      step_pct: 0.95
      hard_sl_pct: 1.25
    atr:
      multiplier_trigger: [1.0, 2.0, 3.0, 5.0]
      multiplier_buffer: [0.5, 0.8, 1.0, 1.0]
      hard_sl_multiplier: 1.5

# --- Exchange ---
exchange:
  type: "lighter"                             # "lighter" | "paper"
  api_key: "${LIGHTER_API_KEY}"
  # paper:
  #   starting_balance: 1000.0

# --- Alerts ---
alerts:
  telegram: true
  on_open: true
  on_close: true
  on_error: true
```

## Implementation Order
1. **Interfaces + data types** — Signal, ExitStrategy, Position (the contracts that matter)
2. **Exit Strategy implementations** — port DSL, build factory pattern, test each independently
3. **Scanner skeleton** — dual-engine scanner with clean Signal output
4. **Bot skeleton** — position manager + executor (stable plumbing)
5. **AI engine** — wraps current logic, minimal changes
6. **Integration** — wire them together
7. **Migration** — port v1 logic piece by piece

## Open Questions / Review Findings

### Issues Found (2026-03-28)
1. **No orchestrator layer.** No top-level wiring for source → pipeline router → AI/bot. Need a `main.py` at project root that reads config, creates sources, routes signals through the right pipeline, and feeds to bot.
2. **No top-level config example.** `plan-interfaces.md` shows `sources` config with pipeline routing, but scanner/bot config sections don't reflect it. Need one unified `config.yml` with all sections.
3. **`types.py` referenced but not in any file tree.** `Signal`, `Position`, `ExitResult`, `Decision` should live in `interfaces/types.py` — add to file structure.
4. **`Outcome` defined in plan-bot but shared.** Both AI engine (`recent_outcomes`) and bot use it. Move to `types.py`.
5. **`ContextBuilder` in `ai/` has cross-module deps.** Needs DB, balance, position store. Acceptable if dependencies are injected, but document the dependency direction.
6. **`PaperExecutor` in file tree only.** No behavior description. Add: fills at signal price, no slippage, tracks virtual balance in memory.
7. **`aiohttp` dependency for TradingViewWebhook.** Not in deps list — add to `requirements.txt`.

### Resolution
- [ ] Add `app/main.py` (orchestrator) to file trees
- [ ] Add `interfaces/types.py` to file trees
- [ ] Move `Outcome` to `interfaces/types.py`
- [ ] Add unified `config.yml` example to plan.md
- [ ] Document `PaperExecutor` behavior in plan-bot
- [ ] Add `aiohttp` to deps

## Status: 🟡 PLANNING — plan updates, then implementation
