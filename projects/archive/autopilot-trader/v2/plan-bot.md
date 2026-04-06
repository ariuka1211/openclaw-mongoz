# V2 — Bot Plan

## Responsibility
Execute trades, manage positions, apply exit strategies.

**NOT responsible for:** detecting signals or deciding whether to trade.

**Handles two input types:**
- `Decision` from AI Engine (full pipeline) — already filtered, has sizing/confidence
- `Signal` directly from source (direct pipeline) — bot applies default rules for sizing

## Position in the Pipeline

```
Full pipeline:    AI Engine ──→ [Decision] ──→ Bot ──→ [Order] ──→ Exchange
Direct pipeline:  Source ────→ [Signal] ────→ Bot ──→ [Order] ──→ Exchange
                                                      ↑
                                                ExitStrategy
                                                (evaluated every tick)
```

## Core Loop

```
1. Receive Decision (from AI) or Signal (direct) 
2. If "open" → create Position, attach ExitStrategy, submit order
3. Every tick:
   a. Get live price
   b. For each position: run ExitStrategy.evaluate(price)
   c. If action → close position
4. Log outcome to trade DB
```

When receiving a raw Signal (direct pipeline), the bot applies default rules:
- Check max positions
- Check min score/strength threshold
- Apply default size from config
- No LLM reasoning — just pass/fail rules

## The PositionManager (central orchestrator)

```python
class PositionManager:
    """
    Owns all open positions. Runs the tick loop.
    Accepts both Decisions (from AI) and Signals (direct).
    Swaps strategies via config. Doesn't implement any strategy itself.
    """
    def __init__(self, config: BotConfig, executor: OrderExecutor,
                 strategy_factory: StrategyFactory, alerter: Alerter):
        self.executor = executor
        self.strategy_factory = strategy_factory
        self.positions: dict[str, TrackedPosition] = {}
        self.min_strength = config.get("min_signal_strength", 0.3)
        self.max_positions = config.max_positions
        ...

    async def handle_decision(self, decision: Decision, signal: Signal) -> None:
        """Full pipeline: AI already evaluated, just execute."""
        if decision.action == "skip":
            return
        # 1. Create position with AI's sizing/confidence
        # 2. Use AI's exit strategy override or config default
        # 3. Initialize strategy via on_open()
        # 4. Submit order
        ...

    async def handle_signal(self, signal: Signal) -> None:
        """Direct pipeline: no AI, apply default rules."""
        # 1. Check min strength
        if signal.strength < self.min_strength:
            return
        # 2. Check max positions
        if len(self.positions) >= self.max_positions:
            return
        # 3. Check not already in same direction on same symbol
        for pos in self.positions.values():
            if pos.symbol == signal.symbol and pos.side == signal.direction:
                return
        # 4. Create position with default sizing
        # 5. Attach default exit strategy
        # 6. Submit order
        ...

    async def tick(self, prices: dict[str, float]) -> list[Outcome]:
        """Called every cycle. Evaluate all positions."""
        outcomes = []
        for symbol, pos in self.positions.items():
            price = prices.get(symbol)
            if not price:
                continue

            result = pos.exit_strategy.evaluate(pos, price)
            if result.action:
                outcome = await self._close_position(pos, price, result)
                outcomes.append(outcome)
        return outcomes
```

## StrategyFactory (the swap mechanism)

Simple factory — maps config name to class. Not over-engineered.

```python
class StrategyFactory:
    """
    Creates exit strategies from config names.
    Change the name → get a different strategy. That's it.
    """
    def __init__(self, config: dict):
        self._strategies: dict[str, type[ExitStrategy]] = {}
        self._default: str = config.get("default_strategy", "dsl")

    def register(self, name: str, strategy_class: type[ExitStrategy]) -> None:
        self._strategies[name] = strategy_class

    def create(self, name: str | None = None, **kwargs) -> ExitStrategy:
        strategy_name = name or self._default
        cls = self._strategies.get(strategy_name)
        if not cls:
            raise ValueError(f"Unknown exit strategy: {strategy_name}")
        return cls(**kwargs)
```

Usage:
```python
factory = StrategyFactory(config)
factory.register("dsl", DSLStrategy)
factory.register("trailing_sl", TrailingSLStrategy)
factory.register("atr", ATRExitStrategy)

# AI says "use ATR for this trade" → factory.create("atr")
# AI says nothing → factory.create() → uses default from config
```

This is intentionally simple. The complexity lives in the strategies, not in the wiring.

## TrackedPosition

```python
@dataclass
class TrackedPosition:
    symbol: str
    side: str                    # "long" | "short"
    entry_price: float
    size: float                  # base units
    size_usd: float              # notional
    leverage: float
    opened_at: datetime
    market_id: int
    signal_id: str | None
    exit_strategy: ExitStrategy  # attached at open time
    high_water_price: float
    state: dict                  # strategy-specific (kept in ExitStrategy actually)
```

The `exit_strategy` field is the key — each position carries its own strategy instance. Different positions can have different strategies.

## Executor

```python
class OrderExecutor(ABC):
    @abstractmethod
    async def open_position(self, symbol, side, size, leverage) -> OrderResult: ...
    @abstractmethod
    async def close_position(self, position) -> OrderResult: ...
    @abstractmethod
    async def get_balance(self) -> float: ...
    @abstractmethod
    async def get_positions(self) -> list[dict]: ...
```

**LighterExecutor** wraps the current `lighter_api.py`. Clean interface, no bot logic inside.

**PaperExecutor** — simulated trading for backtesting and integration testing:
- Fills at signal price (no slippage, no spread)
- Tracks virtual balance in memory (starting balance from config)
- Supports both long and short
- `get_positions()` returns in-memory tracked positions
- No network calls — instant fills
- Balance deducted on open, returned with PnL on close

## Outcome Logging

```python
@dataclass
class Outcome:
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    pnl_pct: float              # raw price move %
    hold_time_seconds: int
    exit_reason: str            # from ExitResult.action.value
    exit_strategy: str          # which strategy closed it
    signal_id: str | None
    timestamp: str
```

Written to SQLite after every close. Same schema as v1 but with `exit_strategy` field added.

## Configuration

```yaml
bot:
  # Position management
  max_positions: 5
  default_size_usd: 5.0
  default_leverage: 10

  # Exit strategy (global default — AI can override per trade)
  exit_strategy:
    default: "dsl"              # "dsl" | "trailing_sl" | "atr" | "fixed"
    
    # DSL config (used when strategy = "dsl")
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
        # ... etc
    
    # Trailing SL config
    trailing_sl:
      trigger_pct: 0.5
      step_pct: 0.95
      hard_sl_pct: 1.25
    
    # ATR config
    atr:
      multiplier_trigger: [1.0, 2.0, 3.0, 5.0]   # tiers as ATR multiples
      multiplier_buffer: [0.5, 0.8, 1.0, 1.0]
      hard_sl_multiplier: 1.5                      # SL at 1.5x ATR

  # Tick loop
  tick_interval_seconds: 2

  # Alerts
  alerts:
    telegram: true
    on_open: true
    on_close: true
    on_error: true
```

## Files

```
bot/
├── __init__.py
├── position_manager.py        # Core loop, position lifecycle
├── strategy_factory.py        # Creates strategies by name
├── config.py                  # Config parsing
├── outcome_logger.py          # Writes outcomes to DB
├── strategies/
│   ├── __init__.py
│   ├── base.py                # ExitStrategy ABC + ExitResult
│   ├── dsl.py                 # Port from v1
│   ├── trailing_sl.py
│   ├── atr_exit.py
│   └── fixed_stop.py
├── executor/
│   ├── __init__.py
│   ├── base.py                # OrderExecutor ABC
│   ├── lighter.py             # Lighter exchange
│   └── paper.py               # Paper trading (sim fills, no slippage, in-memory balance)
├── alerts/
│   ├── __init__.py
│   └── telegram.py
└── tests/
    ├── test_position_manager.py
    ├── test_strategy_factory.py
    ├── test_dsl_strategy.py
    ├── test_trailing_sl.py
    └── test_integration.py
```

## Key Design Decisions

1. **Strategy attached at position open, not per-tick** — once a position is opened with a strategy, it keeps that strategy for its lifetime. No mid-flight strategy swaps.

2. **Factory pattern for strategy creation** — PositionManager never imports DSLStrategy directly. It asks the factory for one by name.

3. **Executor is thin** — just translates to exchange API calls. No position logic, no strategy logic.

4. **Outcome logging is separate** — PositionManager calls OutcomeLogger, not the DB directly. Easy to swap for a different storage backend.

5. **Config drives everything** — want to change default strategy? Change config. Want to add a new strategy? Register it in main.py and add config section.
