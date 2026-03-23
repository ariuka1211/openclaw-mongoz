# FLAW-04: Mark Price Staleness During Long Verification Loops

## Problem

Mark prices were updated once per tick from `get_positions()`. During close_all verification (up to 50 seconds via `_verify_position_closed`), mark prices became stale. DSL evaluation used stale prices for ROE calculation, potentially triggering at the wrong time or missing a trigger entirely.

## Root Cause

`_mark_prices` was a simple `dict[int, float]` with no timestamp tracking. The stale mark price was returned by `get_mark_price()` unconditionally, so `get_price_with_mark_fallback()` would use a 50+ second old price for ROE/stop-loss decisions.

## Fix

1. Changed `_mark_prices` from `dict[int, float]` to `dict[int, dict]` storing `{"price": float, "time": float}` (Unix timestamp).

2. Updated `update_mark_prices_from_positions()` to store `time.time()` alongside each price.

3. Updated `get_mark_price()` to check age: if `(now - cached["time"]) > 30` seconds, returns `None` instead of the stale price.

4. When mark price is stale, `get_price_with_mark_fallback()` falls through to `get_price()` (recent trades API call), which fetches the actual current market price.

## Threshold

30 seconds is appropriate — mark prices from the exchange should update frequently (every few seconds during active markets). If the cache is older than 30s, something is wrong and we need a fresh price from the order book.

## Files Changed

- `projects/autopilot-trader/executor/bot.py` — 3 edits (dict type, storage, staleness check)
