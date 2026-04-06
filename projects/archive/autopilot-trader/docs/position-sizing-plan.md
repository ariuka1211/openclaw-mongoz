# Position Sizing Refactor Plan

> **Goal:** Move all position sizing, risk management, and account-aware logic out of the scanner and into the bot. Scanner becomes a pure signal generator. Bot owns all account-aware decisions with dynamic equity-based sizing.

---

## Problem Statement

The current architecture has responsibilities scattered across three services:

| Concern | Scanner (TS) | Bot (Python) | AI Decisions (Python) |
|---------|-------------|-------------|----------------------|
| Position sizing | ✅ calculates | ✅ reads & scales | ✅ sends `size_pct_equity` |
| Equity awareness | ✅ fetches balance | ✅ fetches balance | ✅ reads from signals config |
| Safety checks | ✅ NaN guards | ✅ max_position cap | ✅ full safety layer |
| Max positions | ✅ caps at 3 | ✅ caps at 3 | ✅ caps at N |

**Result:** Three places do overlapping work. Changing risk rules means touching all three. Position sizing uses a hardcoded $15 cap that doesn't scale with equity. The scanner fetches live balance it doesn't need.

---

## Target Architecture

```
┌─────────────┐     signals.json      ┌─────────────┐     ai-decision.json     ┌─────────────┐
│   Scanner    │ ──────────────────▶  │     Bot      │ ◀────────────────────── │ AI Decisions │
│  (pure TS)   │                       │  (Python)    │                          │  (Python)    │
│              │                       │              │                          │              │
│ Scores only: │                       │ Owns:        │                          │ Decides:     │
│ • composite  │                       │ • equity     │                          │ • action     │
│ • direction  │                       │ • sizing     │                          │ • symbol     │
│ • signals    │                       │ • risk rules │                          │ • direction  │
│ • volatility │                       │ • execution  │                          │ • size_pct   │
│ • price      │                       │ • DSL        │                          │ • confidence │
└─────────────┘                       └─────────────┘                          └─────────────┘
```

**Data flow:**
1. Scanner → `signals.json` (symbol, direction, score, price, volatility — NO sizing)
2. Bot reads signals + fetches live equity → calculates position sizes → opens positions
3. AI reads signals + positions → decides actions (open/close/hold) with `size_pct_equity`
4. Bot receives AI decision → converts `size_pct_equity` to `size_usd` using live equity → executes

---

## What Changes

### Phase 1: Scanner Cleanup (TS)

**Files changed:**
- `scanner/src/config.ts` — remove `accountEquity`, `riskPct`, `stopLossMultiple`, `maxPositionUsd`
- `scanner/src/types.ts` — remove `positionSizeUsd`, `riskAmountUsd`, `stopLossDistanceAbs`, `stopLossDistancePct`, `safetyPass`, `safetyReason` from `MarketOpportunity`
- `scanner/src/main.ts` — remove balance fetch, remove `calculatePosition()` call, remove safety filtering, remove max-positions cap, remove `--equity` CLI arg
- `scanner/src/output.ts` — remove sizing columns from display, remove risk/exposure summaries, remove sizing fields from signals.json
- `scanner/src/position-sizing.ts` — **DELETE** (no longer scanner's job)
- `scanner/tests/unit/position-sizing.test.ts` — **DELETE**

**New fields in signals.json per opportunity:**
```json
{
  "symbol": "BTC",
  "marketId": 1,
  "compositeScore": 82,
  "direction": "long",
  "lastPrice": 97500,
  "dailyVolatility": 0.032,
  "dailyVolumeUsd": 5000000,
  "dailyPriceChange": 2.5,
  "fundingSpread8h": 0.015,
  "fundingSpreadScore": 75,
  "oiTrendScore": 68,
  "momentumScore": 72,
  "maAlignmentScore": 80,
  "orderBlockScore": 65,
  "maDirection": "↑",
  "obType": "support",
  "obDistancePct": 2.1,
  "detectedAt": "2026-03-26T21:00:00Z"
}
```

> **Note:** `dailyVolatility` is a NEW field. Scanner computes `(dailyHigh - dailyLow) / lastPrice` from data it already fetches. Currently this is only computed inside `calculatePosition()` — it needs to become an output field in `MarketOpportunity` before that function is deleted.

**Removed from signals.json:**
```json
// ❌ REMOVED — bot calculates these
{
  "positionSizeUsd": 15,
  "riskAmountUsd": 3,
  "stopLossDistanceAbs": 31.20,
  "stopLossDistancePct": 0.032,
  "safetyPass": true,
  "safetyReason": "PASS"
}
// ❌ REMOVED from config block
{
  "config": {
    "accountEquity": 60,     // scanner doesn't know equity anymore
    "riskPct": 0.05,
    "stopLossMultiple": 1.0,
    "maxPositionUsd": 15
  }
}
```

**What breaks:**
- `scanner/tests/integration/pipeline.test.ts` — tests for `positionSizeUsd`, `riskAmountUsd`, `safetyPass`, `accountEquity` in output. **Rewrite** to test signal-only output.
- Console display changes — less info shown (no sizing columns, no risk exposure).

---

### Phase 2: Bot Position Sizing Engine (Python)

**New file:** `bot/core/position_sizer.py`

Responsibilities:
- Read live equity from Lighter API (already have `bot._get_balance()`)
- Read `dailyVolatility` from scanner signals
- Calculate position size from risk rules:

```python
class PositionSizer:
    def __init__(self, cfg):
        self.max_risk_pct = 0.02        # 2% of equity per trade
        self.max_margin_pct = 0.15      # 15% of equity per position margin
        self.min_rr = 1.5               # minimum risk/reward ratio
        self.max_concurrent = 3         # max simultaneous signal positions
        self.hard_sl_pct = cfg.hard_sl_pct  # 1.25% default
    
    def size_position(self, equity, signal):
        """Returns (size_usd, risk_usd, sl_distance_pct) or (0, 0, 0, reason)"""
        
        # 1. Risk budget
        risk_usd = equity * self.max_risk_pct  # $1.20 on $60
        
        # 2. SL distance from signal volatility
        sl_pct = signal.daily_volatility * 1.0  # stopLossMultiple
        sl_pct = max(sl_pct, self.hard_sl_pct)  # never less than hard SL
        
        # 3. Position size from risk
        size_usd = risk_usd / sl_pct  # $1.20 / 0.0125 = $96
        
        # 4. Margin cap
        max_notional = equity * self.max_margin_pct * 10  # leverage
        size_usd = min(size_usd, max_notional)
        
        # 5. R:R check (needs OB distance or fallback)
        if signal.ob_distance_pct and signal.ob_distance_pct > 0:
            rr = signal.ob_distance_pct / sl_pct
            if rr < self.min_rr:
                return (0, 0, 0, f"R:R {rr:.1f} < {self.min_rr}")
        
        return (size_usd, risk_usd, sl_pct, "OK")
```

**Modified files:**

- `bot/core/signal_handler.py` — major rewrite:
  - Remove: reading `positionSizeUsd`, `safetyPass`, `safetyReason` from signals
  - Remove: `scale = balance / scanner_equity` logic
  - Remove: `CONFIG.accountEquity` dependency
  - Add: import `PositionSizer`, call `size_position()` for each signal
  - Keep: pacing, quota, cooldown, verification logic (all still needed)
  - Keep: min score filter, max concurrent check

- `bot/config.yml` — replace old sizing params:
  ```yaml
  # OLD (remove)
  max_position_usd: 15.0
  dsl_leverage: 10.0
  
  # NEW
  max_risk_pct: 0.02          # 2% equity risk per trade
  max_margin_pct: 0.15        # 15% equity margin per position
  min_risk_reward: 1.5        # minimum R:R ratio
  max_concurrent_signals: 3   # max positions from scanner signals
  hard_sl_pct: 1.25           # keep existing
  ```

- `bot/config.py` — update BotConfig dataclass:
  - Remove: `max_position_usd` (replaced by dynamic calc)
  - Add: `max_risk_pct`, `max_margin_pct`, `min_risk_reward`, `max_concurrent_signals`
  - Keep: `dsl_leverage` (still used for DSL ROE calculation — that's a different concern)
  - Update `validate()` for new fields

- `bot/core/executor.py` — update `execute_ai_open()`:
  - Remove: `cfg.max_position_usd` cap check (sizing now done by PositionSizer or AI)
  - Keep: all verification, retry, and alert logic

**What breaks:**
- `bot/tests/test_signal_processor.py` — 6+ test cases use `positionSizeUsd`, `safetyPass`, `accountEquity` in mock signals. **All need rewriting** to use new signal format + mock PositionSizer.
- `bot/config.py` `validate()` — references `max_position_usd`. **Update** for new fields.

---

### Phase 3: AI Decisions Layer Updates (Python)

**Modified files:**

- `ai-decisions/context/prompt_builder.py`:
  - Remove: `safetyPass` display (no longer in signals)
  - Change: equity source — read from a dedicated equity file or API, NOT from signals config
  - Keep: ROE calculation, signal display (score, direction, funding, volume, momentum)

- `ai-decisions/context/data_reader.py`:
  - No structural changes — still reads signals.json, just with different fields
  - `read_signals()` returns `(opportunities, config)` — config block will be smaller

- `ai-decisions/safety.py`:
  - Remove: `safetyPass` check on matching signal (line 157) — scanner no longer provides this
  - Keep: all other safety rules (max positions, max size %, drawdown, rate limit, etc.)
  - The safety layer already does its own checks — the scanner's safety was redundant

- `ai-decisions/cycle_runner.py`:
  - Change equity source: instead of `signals_config.get("accountEquity", 1000)`, read from bot result file or dedicated equity IPC file
  - The bot already writes equity to `signals/equity.json` via `write_equity_file()` — use that

- `ai-decisions/ai_trader.py`:
  - Same equity source change as cycle_runner

**What breaks:**
- `ai-decisions/tests/test_cycle_runner.py` — mock signals include `accountEquity` in config. **Update** mocks.
- `ai-decisions/tests/test_prompt_builder.py` — mock signals include `safetyPass`. **Update** mocks.
- Prompt format changes slightly (no ✅/❌ safety emoji on signals).

---

### Phase 4: Equity IPC

Currently equity is scattered — scanner fetches it, bot fetches it, AI reads it from signals config. Need a single source of truth.

**Approach:** Bot is the equity owner. It already writes `ai-decisions/state/equity.json` via `write_equity_file()` in `shared_utils.py`. Current format: `{"equity": 62.50, "timestamp": "..."}`.

Changes needed:
- AI reads from `ai-decisions/state/equity.json` instead of `signals.json` → `config.accountEquity`
- `cycle_runner.py` and `ai_trader.py`: replace `signals_config.get("accountEquity", 1000)` with equity file read
- `prompt_builder.py`: same equity source change
- Scanner doesn't touch equity at all (already handled in Phase 1)

**What breaks:**
- Any code reading equity from `signals.json` → `config.accountEquity`. All need to switch to `equity.json`.

---

## Impact Matrix

| Component | File | Impact | Action |
|-----------|------|--------|--------|
| **Scanner** | `config.ts` | 🔴 High | Remove 4 config fields |
| **Scanner** | `types.ts` | 🔴 High | Remove 6 fields from MarketOpportunity |
| **Scanner** | `main.ts` | 🔴 High | Remove balance fetch, sizing, safety filter |
| **Scanner** | `output.ts` | 🟡 Med | Simplify display, clean signals.json output |
| **Scanner** | `position-sizing.ts` | 🔴 Delete | Entire file removed |
| **Scanner tests** | `position-sizing.test.ts` | 🔴 Delete | Entire file removed |
| **Scanner tests** | `pipeline.test.ts` | 🟡 Med | Rewrite output assertions |
| **Bot** | `config.yml` | 🟡 Med | Replace sizing params |
| **Bot** | `config.py` | 🟡 Med | New fields, update validate() |
| **Bot** | `signal_handler.py` | 🔴 High | Major rewrite — use PositionSizer |
| **Bot** | `executor.py` | 🟢 Low | Remove max_position_usd cap |
| **Bot** | `position_sizer.py` | 🟢 New | New file |
| **Bot tests** | `test_signal_processor.py` | 🔴 High | Rewrite 6+ test cases |
| **Bot tests** | `conftest.py` | 🟡 Med | Update config fixture (remove `max_position_usd`, add new fields) |
| **AI** | `prompt_builder.py` | 🟡 Med | Remove safetyPass, change equity source |
| **AI** | `safety.py` | 🟢 Low | Remove 1 safetyPass check |
| **AI** | `cycle_runner.py` | 🟢 Low | Change equity source |
| **AI** | `ai_trader.py` | 🟢 Low | Change equity source |
| **AI tests** | `test_cycle_runner.py` | 🟡 Med | Update mocks |
| **AI tests** | `test_prompt_builder.py` | 🟡 Med | Update mocks |
| **IPC** | `ai-decisions/state/equity.json` | 🟢 Exists | Bot already writes this, AI needs to read it |

---

## Verification Plan

After each phase:

### Phase 1 (Scanner):
- [ ] `cd scanner && bun build` — no compile errors
- [ ] `bun test` — all tests pass (after rewrites)
- [ ] Run scanner manually, verify signals.json has no sizing fields
- [ ] Verify console output doesn't show equity/risk/sizing columns

### Phase 2 (Bot):
- [ ] `cd bot && python -m pytest tests/ -x` — all tests pass
- [ ] Verify PositionSizer math: $60 equity, 1.25% SL → $96 position (not $15)
- [ ] Verify dynamic scaling: $500 equity → $800 position
- [ ] Verify R:R filter blocks low-reward trades
- [ ] Verify max concurrent still works

### Phase 3 (AI):
- [ ] `cd ai-decisions && python -m pytest tests/ -x` — all tests pass
- [ ] Verify prompt builds without safetyPass references
- [ ] Verify equity reads from equity.json

### Integration:
- [ ] All 3 services start without errors
- [ ] Scanner writes signals, bot reads and sizes, AI reads and decides
- [ ] Manual test: open a position, verify sizing matches new rules

---

## Risk/Reward Math (for reference)

With new rules on $60 equity:

| Scenario | Risk/trade | Max margin | Max notional (@10x) | SL 1.25% → position |
|----------|-----------|------------|---------------------|---------------------|
| $60 equity | $1.20 (2%) | $9 (15%) | $90 | $96 → capped at $90 |
| $100 equity | $2.00 (2%) | $15 (15%) | $150 | $160 → capped at $150 |
| $500 equity | $10 (2%) | $75 (15%) | $750 | $800 → capped at $750 |

Max loss per trade = position × SL% = $90 × 1.25% = $1.125 (1.88% of $60 equity) ✅
3 concurrent positions = max 5.6% equity at risk (3 × 1.88%) ✅

Breakeven recovery:
| Streak losses | Drawdown | Trades to recover at 1.88% avg win |
|--------------|----------|-------------------------------------|
| 3 | 5.6% | ~3 |
| 5 | 9.4% | ~5.3 |
| 10 | 18.8% | ~11.4 |

---

## Execution Order

1. **Phase 1** (Scanner) — can be done independently, breaks nothing in bot/AI until Phase 2 is ready
2. **Phase 4** (Equity IPC) — small change, unblocks Phase 3
3. **Phase 2** (Bot sizing) — depends on Phase 1 signal format
4. **Phase 3** (AI updates) — depends on Phase 4 equity source

**Recommended:** Do Phase 1 + 4 first (scanner cleanup + equity IPC), then Phase 2 + 3 together (bot sizing + AI updates).
