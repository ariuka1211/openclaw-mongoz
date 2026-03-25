# Autopilot Trader — Cheatsheet

> Read this first in new sessions. Full architecture: `autopilot-trader.md` (only if needed).

## Services
```bash
systemctl status lighter-bot lighter-scanner ai-trader
journalctl -u lighter-bot -f          # bot logs
journalctl -u ai-trader -f            # AI trader logs
tail -f projects/autopilot-trader/signals/scanner.log
```

## File Map
| File | What It Does |
|------|-------------|
| `executor/bot.py` | Main loop, position sync, AI decision execution |
| `executor/dsl.py` | Dynamic Stop Loss engine (tiered trailing) |
| `executor/config.yml` | Bot config (leverage, SL, DSL tiers) |
| `executor/auth_helper.py` | Lighter REST API auth tokens |
| `signals/ai-trader/ai_trader.py` | LLM decision loop (every 2 min) |
| `signals/ai-trader/context_builder.py` | Prompt assembly for LLM |
| `signals/ai-trader/safety.py` | Hard rules LLM can't override |
| `signals/ai-trader/llm_client.py` | Kilo Gateway HTTP client |
| `signals/ai-trader/db.py` | SQLite decision journal |
| `signals/ai-trader/reflection.py` | Periodic learning loop |
| `signals/scripts/opportunity-scanner.ts` | Signal scoring (every 5 min) |
| `signals/scripts/correlation-guard.ts` | Prevents correlated positions |
| `signals/scripts/funding-monitor.ts` | Funding rate dashboard |

## IPC Files
| File | Written By | Read By |
|------|-----------|---------|
| `signals/signals.json` | Scanner | AI Trader, Bot |
| `signals/ai-decision.json` | AI Trader | Bot |
| `signals/ai-result.json` | Bot | AI Trader |
| `executor/state/quota_state.json` | Bot | Bot (restart continuity) |

## Key Patterns
- **Equity:** Read from `.env`, never guess. Account 719758, L1: `0x1D73...3bEE`
- **Position confirmation:** Two-cycle sync before tracking (prevents phantom positions)
- **Close retries:** Progressive delay 15s → 60s → 300s → 900s
- **Hard SL:** 1.25% default, converts to ROE via leverage
- **DSL tiers:** Trigger at 7/12/15/20% ROE, tighter buffers as profit grows
- **Mark price:** Derived from `unrealized_pnl / size` (not recent_trades)
- **Quota:** Lighter volume quota, blocks new opens when < 50 to save for SL
- **Change detection:** AI trader hashes signals+positions, skips LLM if unchanged

## Environment
- venv: `projects/autopilot-trader/executor/venv/`
- secrets: `/root/.openclaw/workspace/.env`
- scanner runs via Bun: `bun run scripts/opportunity-scanner.ts`
