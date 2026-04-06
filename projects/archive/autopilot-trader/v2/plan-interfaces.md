# V2 — Interfaces (The Contracts)

Everything else depends on these. We nail these first, then build implementations.

## 1. SignalSource (Scanner → AI/Bot)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

class SignalSource(ABC):
    """Anything that produces trading signals."""

    @abstractmethod
    async def start(self) -> None:
        """Start collecting data and generating signals."""

    @abstractmethod
    async def stop(self) -> None:
        """Clean shutdown."""

    @abstractmethod
    async def poll(self) -> list["Signal"]:
        """Fetch new signals since last poll. Non-blocking."""
        ...

    @abstractmethod
    def get_price(self, symbol: str) -> float | None:
        """Latest known price for a symbol."""
        ...
```

**Implementations:**
- `ScannerV2` — our new dual-engine scanner
- `ScannerV1Adapter` — wraps current TS scanner (reads signals.json, converts to Signal objects)
- `TradingViewWebhook` — receives TradingView alerts via HTTP POST
- `ManualSignals` — CLI/JSON input for testing

## Pipeline Routing

Signal sources don't all need to go through the AI engine. Each source can choose its pipeline:

```
"full":    Source → AI Engine → Bot → Exchange
"direct":  Source → Bot → Exchange (skip AI)
```

Config:
```yaml
sources:
  - type: "scanner_v2"
    pipeline: "full"                      # scanner → AI → bot
  - type: "tradingview_webhook"
    pipeline: "direct"                    # TV → bot (trust the strategy)
    port: 8080
    secret: "webhook_auth_token"
  - type: "tradingview_webhook"
    pipeline: "full"                      # another TV strategy → AI filters it
    port: 8081
    secret: "other_token"
```

This means:
- **TradingView signals can go straight to execution** — fastest path, no AI delay
- **Our scanner signals can go through AI** — AI evaluates, adjusts size, risk-manages
- **You can run both simultaneously** — different sources, different pipelines
- **AI is optional** — if all sources use "direct", AI engine isn't needed at all

## TradingView Webhook Implementation

```python
class TradingViewWebhook(SignalSource):
    """
    HTTP server that receives TradingView alert webhooks.
    TradingView alert message format (configurable in TV):
    
    {
      "symbol": "{{ticker}}",
      "action": "{{strategy.order.action}}",
      "strategy": "{{strategy.name}}",
      "price": {{close}},
      "time": "{{time}}"
    }
    """
    def __init__(self, config: dict):
        self.port = config.get("port", 8080)
        self.secret = config.get("secret")
        self.symbol_map = config.get("symbol_map", {})  # TV symbol → our symbol
        self._signals: list[Signal] = []
        self._server = None

    async def start(self) -> None:
        # Start aiohttp web server on self.port
        # POST /webhook → validate secret → parse → append to self._signals
        ...

    async def poll(self) -> list[Signal]:
        signals = self._signals.copy()
        self._signals.clear()
        return signals

    def get_price(self, symbol: str) -> float | None:
        # Webhook doesn't track live prices — return last known from signal
        ...

    async def handle_webhook(self, request) -> dict:
        payload = await request.json()
        # Validate auth
        if self.secret and request.headers.get("X-Webhook-Secret") != self.secret:
            return {"error": "unauthorized"}
        
        signal = self._to_signal(payload)
        self._signals.append(signal)
        return {"status": "ok", "signal_id": signal.id}
```

## 2. ExitStrategy (Position lifecycle)

```python
class ExitAction(Enum):
    HOLD = "hold"
    TIER_LOCK = "tier_lock"       # DSL tier floor breached
    TRAILING_SL = "trailing_sl"   # trailing stop hit
    HARD_SL = "hard_sl"           # hard stop loss
    TAKE_PROFIT = "take_profit"   # take profit target
    STAGNATION = "stagnation"     # position stalled
    CLOSE = "close"               # generic close (AI-driven, etc.)

@dataclass
class ExitResult:
    action: ExitAction | None     # None = hold
    reason: str                   # human-readable explanation
    metadata: dict = {}           # strategy-specific data (tier, floor price, etc.)

class ExitStrategy(ABC):
    """Determines when to close a position."""

    @abstractmethod
    def on_open(self, position: "Position", signal: "Signal") -> None:
        """Called when a position is opened. Set up strategy state."""
        ...

    @abstractmethod
    def evaluate(self, position: "Position", price: float) -> ExitResult:
        """Called every tick. Return action or None to hold."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Strategy name for logging."""
        ...
```

**Implementations:**
- `DSLStrategy` — current DSL logic, ports dsl.py
- `TrailingSLStrategy` — simple trailing stop loss
- `ATRExitStrategy` — dynamic tiers based on ATR
- `FixedStopStrategy` — fixed stop loss + take profit
- `CompositeStrategy` — runs multiple strategies, takes the most conservative action

## 3. OrderExecutor (Bot → Exchange)

```python
class OrderExecutor(ABC):
    """Submits orders to an exchange."""

    @abstractmethod
    async def open_position(self, symbol: str, side: str, size: float,
                           leverage: float) -> "OrderResult":
        ...

    @abstractmethod
    async def close_position(self, position: "Position") -> "OrderResult":
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        ...

    @abstractmethod
    async def get_positions(self) -> list["Position"]:
        ...
```

**Implementations:**
- `LighterExecutor` — wraps current Lighter API
- `PaperExecutor` — simulated for backtesting
- `BinanceExecutor` — future exchange support

## 4. DecisionEngine (Signal evaluation)

```python
class DecisionEngine(ABC):
    """Evaluates signals and decides whether to trade."""

    @abstractmethod
    async def evaluate(self, signal: Signal, context: "MarketContext") -> "Decision":
        """Should we trade this signal? Returns decision with reasoning."""
        ...

@dataclass
class Decision:
    signal_id: str
    action: str              # "open_long" | "open_short" | "skip"
    confidence: float        # 0.0 - 1.0
    reasoning: str           # why
    size_multiplier: float   # 1.0 = normal, 0.5 = half size, etc.
    custom_sl_pct: float | None = None  # override exit strategy SL for this trade
```

**Implementations:**
- `AIDecisionEngine` — wraps current AI logic (LLM-based)
- `RuleBasedEngine` — simple rules, no LLM, for testing
- `AlwaysOpenEngine` — accepts everything, for backtesting

## 5. Position Data Type (shared across all)

```python
@dataclass
class Position:
    symbol: str
    side: str                # "long" | "short"
    entry_price: float
    size: float              # base units
    size_usd: float          # notional USD
    leverage: float
    opened_at: str           # ISO-8601
    market_id: int | None = None
    signal_id: str | None = None
    exit_strategy: str | None = None   # which strategy is active
    state: dict = {}         # strategy-specific state (DSL state, trailing level, etc.)
```

## 6. Shared Types (types.py)

All shared data types live in one file — `interfaces/types.py`. This is the single source of truth for data contracts.

```python
# interfaces/types.py

@dataclass
class Signal:
    id: str
    symbol: str
    direction: str             # "long" | "short"
    type: str                  # detector name: "rsi_oversold", "momentum_breakout", etc.
    strength: float            # 0.0 - 1.0
    price: float
    timestamp: str             # ISO-8601
    metadata: dict             # engine-specific context (non-essential, for logging/debug)
    atr: float | None = None

@dataclass
class Position:
    symbol: str
    side: str                  # "long" | "short"
    entry_price: float
    size: float                # base units
    size_usd: float            # notional USD
    leverage: float
    opened_at: str             # ISO-8601
    market_id: int | None = None
    signal_id: str | None = None
    exit_strategy: str | None = None
    state: dict = {}

@dataclass
class ExitResult:
    action: ExitAction | None  # None = hold
    reason: str
    metadata: dict = {}

@dataclass
class Decision:
    signal_id: str
    action: str                # "open_long" | "open_short" | "skip"
    confidence: float
    reasoning: str
    size_multiplier: float = 1.0
    custom_sl_pct: float | None = None
    custom_exit_strategy: str | None = None

@dataclass
class Outcome:
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    pnl_pct: float             # raw price move %
    hold_time_seconds: int
    exit_reason: str           # from ExitResult.action.value
    exit_strategy: str         # which strategy closed it
    signal_id: str | None
    timestamp: str

@dataclass
class MarketContext:
    signal: Signal
    open_positions: list[Position]
    balance: float
    recent_outcomes: list[Outcome]
    market_volatility: dict[str, float]
```

## 7. Orchestrator (app/main.py)

Top-level wiring. This is the entry point that connects sources, pipeline routing, AI engine, and bot.

Responsibilities:
- Read config.yml, create configured signal sources
- Route each source's signals through the correct pipeline ("full" → AI → bot, "direct" → bot)
- Manage lifecycle: start sources, poll loop, graceful shutdown
- NOT a service itself — runs as a single process

```
app/
├── __init__.py
├── main.py                    # Entry point — wires sources → pipelines → bot
├── pipeline.py                # PipelineRouter: routes Signal through full or direct
└── config.py                  # Top-level config parsing
```

```python
class PipelineRouter:
    def __init__(self, ai_engine: DecisionEngine | None, bot: PositionManager, config: dict):
        self.ai_engine = ai_engine
        self.bot = bot
        # source_id → "full" | "direct"
        self.routes: dict[str, str] = {}

    async def route(self, source_id: str, signal: Signal) -> None:
        pipeline = self.routes.get(source_id, "direct")
        if pipeline == "full" and self.ai_engine:
            context = await self.context_builder.build(signal)
            decision = await self.ai_engine.evaluate(signal, context)
            await self.bot.handle_decision(decision, signal)
        else:
            await self.bot.handle_signal(signal)
```

## Dependency Rules

```
interfaces/        ← NO imports from anywhere (pure contracts)
    signal_source.py
    exit_strategy.py
    executor.py
    decision_engine.py
    types.py         ← Signal, Position, ExitResult, Decision, Outcome, MarketContext

strategies/        ← imports from interfaces/ only
sources/           ← imports from interfaces/ only
execution/         ← imports from interfaces/ only
app/               ← imports from all (wiring layer only)
```

**Forbidden:**
- `strategies/` importing from `sources/`
- `execution/` importing from `strategies/`
- Any module importing a concrete class from another module
- Config via code — everything wired through config.yml
- `app/` logic leaking into other modules (app is the top, not imported by anyone)
