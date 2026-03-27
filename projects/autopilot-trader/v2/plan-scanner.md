# V2 — Scanner Plan

## Responsibility
Collect market data, run signal detectors, output clean Signal objects.

**NOT responsible for:** deciding whether to trade, managing positions, or executing orders.

**NOTE:** The scanner is one of multiple SignalSources. TradingView webhooks, manual input, or any other source can produce signals alongside it. Each source independently follows the Signal interface.

## Data Flow

```
Lighter REST API ──→ Candle Buffer ──→ Signal Engines ──→ Merge/Dedup ──→ Signal Output
                         ↑                    ↑
Lighter WebSocket ──→ Live Price ──→  Engine 1: Classic (RSI, EMA, MACD, Volume)
                                     Engine 2: Smart (Breakout, OI Div, Liquidity Sweep, etc.)
```

## Data Collection

### REST Polling (every 60s)
- Endpoint: `GET /api/v1/candlesticks?market={symbol}&interval=5m&count=100`
- Markets: configurable list (default: BTC-PERP, ETH-PERP, SOL-PERP)
- Stores in a ring buffer per symbol (last 100 candles)

### WebSocket (continuous)
- Endpoint: `wss://mainnet.lighter.xyz/ws`
- Subscribe to ticker/trade streams for configured markets
- Updates live price between REST polls
- Reconnect with exponential backoff

### Data Structures

```python
@dataclass
class Candle:
    timestamp: int      # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class MarketData:
    symbol: str
    candles: list[Candle]     # last N candles
    live_price: float         # latest WS price
    atr: float | None         # calculated ATR (14-period)
```

## Engine 1: Classic Indicators

File: `engines/classic.py`

Each indicator is a standalone function:

```python
def rsi_signal(closes: list[float], period: int = 14) -> Signal | None
def ema_crossover_signal(closes: list[float], fast: int = 9, slow: int = 21) -> Signal | None
def macd_signal(closes: list[float]) -> Signal | None
def volume_spike_signal(volumes: list[float], threshold: float = 2.0) -> Signal | None
```

Each function:
- Takes raw candle data
- Returns a Signal or None
- No state, no side effects, no imports from other modules
- Pure function: same input → same output

**Indicators:**
| Indicator | Long condition | Short condition |
|-----------|---------------|-----------------|
| RSI | < 30 (oversold) | > 70 (overbought) |
| EMA Cross | 9 crosses above 21 | 9 crosses below 21 |
| MACD | MACD line > signal line | MACD line < signal line |
| Volume Spike | volume > 2x rolling avg | volume > 2x rolling avg |

## Engine 2: Smart Detectors

File: `engines/smart.py`

Same interface — pure functions:

```python
def momentum_breakout(closes, highs, lows, volumes) -> Signal | None
def oi_divergence(funding_rate, price_change) -> Signal | None
def liquidity_sweep(highs, lows, closes) -> Signal | None
def trend_setup(closes, volumes) -> Signal | None
def delta_divergence(buy_volume, sell_volume, price_change) -> Signal | None
```

**Detectors:**
| Detector | What it catches |
|----------|----------------|
| Momentum Breakout | Price closes above 20-bar high/low with volume |
| OI Divergence | Funding contradicts price direction (crowded trade) |
| Liquidity Sweep | Long wick pierces level then snaps back (stop hunt) |
| Trend Setup | EMA cross confirmed by non-extreme RSI |
| Delta Divergence | Buy/sell pressure contradicts price |

## Merge & Dedup

```python
def merge_signals(signals: list[Signal]) -> list[Signal]:
    """
    1. Group by (symbol, direction)
    2. If same type fires from both engines → keep stronger
    3. If different types, same direction → keep all (they're independent)
    4. If opposite directions → keep stronger, discard weaker
    """
```

## ATR Calculation

```python
def calculate_atr(candles: list[Candle], period: int = 14) -> float:
    """Average True Range. Included in Signal.atr field."""
```

ATR is calculated once per poll cycle and attached to every Signal emitted.

## Configuration

```yaml
scanner:
  poll_interval_seconds: 60
  candle_interval: 5m
  candle_count: 100
  markets:
    - BTC-PERP
    - ETH-PERP
    - SOL-PERP
  atr_period: 14

  engines:
    classic:
      enabled: true
      rsi_period: 14
      rsi_oversold: 30
      rsi_overbought: 70
      ema_fast: 9
      ema_slow: 21
      volume_threshold: 2.0
    smart:
      enabled: true
      momentum_lookback: 20
      volume_threshold: 1.8

  merge:
    min_strength: 0.3        # discard signals below this
    dedup_window_seconds: 300 # don't re-fire same signal within 5 min
```

## Output Interface

```python
class ScannerV2(SignalSource):
    async def poll(self) -> list[Signal]:
        # 1. Fetch new candles from REST
        # 2. Run both engines
        # 3. Merge & dedup
        # 4. Attach ATR
        # 5. Return Signal objects
```

## Files

```
scanner/
├── __init__.py
├── scanner.py              # ScannerV2 implementation (SignalSource)
├── data_collector.py       # REST polling + WS management
├── merge.py                # Signal merge/dedup logic
├── atr.py                  # ATR calculation
├── engines/
│   ├── __init__.py
│   ├── classic.py          # RSI, EMA, MACD, Volume
│   └── smart.py            # Breakout, OI Div, Liquidity, etc.
└── tests/
    ├── test_classic.py
    ├── test_smart.py
    └── test_merge.py
```

## Testing Strategy
- Each indicator function is independently testable (pure functions)
- Feed known candle patterns, assert correct signals
- Test merge logic with conflicting signals
- Test ATR against known values
