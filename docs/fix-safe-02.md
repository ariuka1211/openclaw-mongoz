# SAFE-02 Fix: Emergency Halt Wait Increased to 60s

**Date:** 2026-03-23

## Issue
The AI trader waited only 10 seconds after writing `close_all` before exiting. The bot may be in the middle of a price poll (5s) or position verification (up to 50s), meaning the `close_all` command might not be processed before the trader exits.

## Fix
Changed `asyncio.sleep(10)` to `asyncio.sleep(60)` in the cleanup section of the main run loop (line 222 in `ai_trader.py`).

## File Changed
- `projects/autopilot-trader/signals/ai-trader/ai_trader.py`
