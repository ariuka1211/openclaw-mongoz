# V2 — AI Engine Plan

## Responsibility
Evaluate incoming signals and decide: trade, skip, or adjust size.

**NOT responsible for:** detecting signals, managing positions, or executing orders.

**NOTE: The AI engine is optional.** Signal sources can use `pipeline: "direct"` to bypass the AI entirely and go straight to the bot. The AI only runs for sources that configure `pipeline: "full"`.

## Position in the Pipeline

```
Full pipeline:    Scanner/Source ──→ [Signal] ──→ AI Engine ──→ [Decision] ──→ Bot
Direct pipeline:  Scanner/Source ──→ [Signal] ──→ Bot (skip AI)
```

When bypassed, the bot receives the Signal directly and makes sizing/risk decisions itself based on config defaults (no LLM, no reasoning — just rules).

## Interface

```python
class DecisionEngine(ABC):
    @abstractmethod
    async def evaluate(self, signal: Signal, context: MarketContext) -> Decision: ...

@dataclass
class MarketContext:
    """Everything the AI needs to make a decision."""
    signal: Signal
    open_positions: list[Position]    # current positions
    balance: float                    # available balance
    recent_outcomes: list[Outcome]    # last N trade results
    market_volatility: dict[str, float]  # symbol → ATR/volatility
```

## Implementation 1: AI Decision Engine (current logic)

File: `engines/ai_engine.py`

Wraps the current LLM-based decision logic:
- Takes signal + market context
- Builds a prompt with position state, recent outcomes, risk metrics
- Calls LLM (OpenRouter)
- Parses structured response into Decision

**Key behaviors to preserve:**
- Max concurrent positions check
- Score filtering (min composite score)
- Position sizing adjustments based on confidence
- Custom SL overrides per trade
- Cooldown after losses

## Implementation 2: Rule-Based Engine (for testing)

File: `engines/rule_engine.py`

```python
class RuleBasedEngine(DecisionEngine):
    """
    Simple rule engine — no LLM, fast, deterministic.
    Good for backtesting and integration tests.
    """
    def __init__(self, config: dict):
        self.min_strength = config.get("min_strength", 0.5)
        self.max_positions = config.get("max_positions", 5)
        # ... rules from config

    async def evaluate(self, signal: Signal, context: MarketContext) -> Decision:
        # Rule 1: strength threshold
        if signal.strength < self.min_strength:
            return Decision(skip, confidence=0, reasoning="too weak")

        # Rule 2: max positions
        if len(context.open_positions) >= self.max_positions:
            return Decision(skip, confidence=0, reasoning="max positions")

        # Rule 3: don't trade same direction on same symbol
        for pos in context.open_positions:
            if pos.symbol == signal.symbol and pos.side == signal.direction:
                return Decision(skip, confidence=0, reasoning="already in this direction")

        # Rule 4: accept
        return Decision(open, confidence=signal.strength, reasoning="rules passed")
```

## Implementation 3: Always Open (for backtesting)

```python
class AlwaysOpenEngine(DecisionEngine):
    async def evaluate(self, signal, context):
        return Decision(open, confidence=1.0, reasoning="accept all")
```

## Decision Output

```python
@dataclass
class Decision:
    signal_id: str
    action: str                    # "open_long" | "open_short" | "skip"
    confidence: float              # 0.0 - 1.0
    reasoning: str                 # human-readable
    size_multiplier: float = 1.0   # 1.0 = normal, 0.5 = half
    custom_sl_pct: float | None = None     # override exit strategy SL
    custom_exit_strategy: str | None = None  # override default exit strategy
```

The `custom_exit_strategy` field is key — the AI can say "use ATR strategy for this trade" or "use fixed stop" on a per-trade basis. The bot respects this override.

## Context Building

The AI engine needs a `MarketContext`. The bot builds this:

```python
class ContextBuilder:
    """Builds MarketContext for the AI engine."""

    def __init__(self, db: TradeDB, balance_provider, position_store):
        ...

    async def build(self, signal: Signal) -> MarketContext:
        return MarketContext(
            signal=signal,
            open_positions=await self.positions.get_all(),
            balance=await self.balance_provider.get(),
            recent_outcomes=await self.db.get_recent(limit=20),
            market_volatility=await self.get_volatility(signal.symbol),
        )
```

## Configuration

```yaml
ai_engine:
  strategy: "ai"                # "ai" | "rules" | "always_open"
  
  # AI-specific
  model: "openrouter/anthropic/claude-sonnet-4"
  max_tokens: 500
  timeout_seconds: 30
  
  # Rules (shared across strategies)
  min_score: 60
  max_positions: 5
  cooldown_after_loss_minutes: 15
  
  # Position sizing
  default_size_usd: 5.0
  min_size_usd: 1.0
  max_size_usd: 20.0
```

## Files

```
ai/
├── __init__.py
├── decision_engine.py        # DecisionEngine ABC + Decision dataclass
├── context_builder.py        # Builds MarketContext
├── engines/
│   ├── __init__.py
│   ├── ai_engine.py          # LLM-based (current logic)
│   ├── rule_engine.py        # Deterministic rules
│   └── always_open.py        # Accept everything
└── tests/
    ├── test_rule_engine.py
    └── test_context_builder.py
```

## Key Design Decisions

1. **AI engine doesn't know about exit strategies** — it just says "trade" or "skip." It can optionally suggest an exit strategy override, but doesn't manage positions.

2. **Context is injected, not fetched** — the engine receives context, doesn't call APIs. This makes testing easy (mock the context).

3. **Per-decision overrides** — the AI can override exit strategy and size per trade. This is how ATR-based strategies get selected dynamically without changing global config.
