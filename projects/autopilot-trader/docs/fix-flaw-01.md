# Fix FLAW-01: Remove dead `_pending_positions` references

Removed misleading comments about "2 consecutive sync cycles" / "Two-cycle confirmation" from `bot.py`. `_pending_positions` was already removed; the leftover comments incorrectly described behavior that never existed. Positions are confirmed on first detection.
