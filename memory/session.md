Full audit of btc-grid-bot — found 18 issues, fixed 15 across 5 restarts.

## Fixes applied (16 verified issues → 15 fixed, 2 retracted):
- #2: 30m candles dropped in roll_grid — gather_indicators got 15m twice
- #3: PnL used config 1000 instead of equity_at_reset
- #5: Replacement orders now tracked in state (fill chain works)
- #6: Removed fake "cancel filled order" — now sends UNHEDGED alert
- #7: datetime.utcnow() → datetime.now(timezone.utc) in market_intel.py
- #8: Roll buffer 2% → 10% + 5min cooldown between rolls
- #10: Config keys fixed (num_buy_levels → min/max_levels), safety check moved after analyst
- #11: Persistent 24h loss lockout via state/loss_lockout.json
- #12: daily_pnl now computed from equity delta, fill_count added
- #14: Calculator returns adjusted_num_buy/sell_levels
- #15: Shallow copy → copy.deepcopy for roll backup
- #16 (model): set LLM to qwen/qwen3.6-plus:free (stripped openrouter/ prefix for OpenRouter API)
- Startup crash fixed: ZeroDivisionError when analyst pauses — calculate_grid moved after pause check
- #13 was already done in batch 1

## Retracted:
- #16 (max_tokens cost) — OpenRouter charges per token used, not requested
- #23 (double-slash URL) — lstrip('/') prevents it

## Current grid state (as of session):
- Active: True, 5 buys + 5 sells, 0.001651 BTC/level
- Range: $65,700 – $68,050, Equity at reset: $87.70
- 10% roll buffer, 5min cooldown between rolls
- Model: qwen/qwen3.6-plus:free
