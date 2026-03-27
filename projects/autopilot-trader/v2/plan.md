# Trader V2 — Architecture Plan

## Philosophy
- **Modular from day 1.** Every component is swappable.
- **Interfaces first.** Define contracts before implementations.
- **No shortcuts.** No "hardcode for now." No "we'll fix it later."
- **Current bot keeps running.** V2 is built in parallel, not refactored from v1.

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

## Implementation Order
1. **Interfaces + data types** — Signal, ExitStrategy, Position (the contracts that matter)
2. **Exit Strategy implementations** — port DSL, build factory pattern, test each independently
3. **Scanner skeleton** — dual-engine scanner with clean Signal output
4. **Bot skeleton** — position manager + executor (stable plumbing)
5. **AI engine** — wraps current logic, minimal changes
6. **Integration** — wire them together
7. **Migration** — port v1 logic piece by piece

## Status: 🟡 PLANNING — no code until plans are approved
