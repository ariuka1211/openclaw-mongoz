# Session Handoff — 2026-03-26

## What We Did

### Position Sizing Implementation (Complete ✅)
Replaced leverage-based safety with fixed USD position sizing across all 3 services.

**Key Changes:**
- `default_leverage` → `max_position_usd: 15.0` + `dsl_leverage: 10.0` (config.py, config.yml)
- Signal handler & AI executor both cap at $15 notional per position
- `set_leverage()` call removed from `open_position()` (method preserved)
- Exchange leverage passed to DSL via `verified_pos["leverage"]` (actual IMF-based)
- Scanner caps at $15 in position-sizing.ts
- 219/219 bot tests pass, scanner tests pass

**Files Changed:**
- `bot/config.py` — new fields, validation
- `bot/config.yml` — new fields
- `bot/config.example.yml` — same
- `bot/core/signal_handler.py` — position cap + leverage pass-through
- `bot/core/executor.py` — position cap + leverage pass-through
- `bot/core/position_tracker.py` — `default_leverage` → `dsl_leverage`
- `bot/core/result_writer.py` — same rename
- `bot/core/state_manager.py` — same rename
- `bot/core/shared_utils.py` — same rename
- `bot/bot.py` — same rename
- `bot/api/lighter_api.py` — same rename + removed set_leverage call
- `scanner/src/config.ts` — added `maxPositionUsd: 15`
- `scanner/src/position-sizing.ts` — added `Math.min(size, CONFIG.maxPositionUsd)`

**DSL Math (unchanged behavior):**
- `ROE% = price_move% × exchange_leverage`
- Lower leverage → more margin → wider stops before hard SL
- `hard_sl_pct = 1.25%` × leverage → -12.5% ROE at 10x

## Current State
- Bot running, no errors, all positions tracked
- Scanner producing $15 capped signals
- No `default_leverage` in production code
- All tests passing

## What To Do Next
- Monitor for position sizing issues in live trading
- Consider adjusting `max_position_usd` if needed
- Watch for DSL stop-loss behavior with variable exchange leverage
- No immediate action needed — system is stable

## How To Verify
```bash
# Bot tests
cd bot && ./venv/bin/python -m pytest tests/ -q -k "not lighter"

# Check config
./venv/bin/python -c "from config import BotConfig; cfg = BotConfig.from_yaml('config.yml'); print(f'max_usd={cfg.max_position_usd}, dsl_lev={cfg.dsl_leverage}')"

# Monitor live
tail -f bot.log | grep -E "cap|error|position"
```
