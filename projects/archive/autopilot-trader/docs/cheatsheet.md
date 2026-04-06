# Autopilot Trader — Cheatsheet

> Read this first in new sessions. Full architecture: `autopilot-trader.md` (only if needed).

## Services
```bash
systemctl status bot scanner ai-decisions
journalctl -u bot -f          # bot logs
journalctl -u ai-decisions -f            # AI trader logs
tail -f projects/autopilot-trader/scanner/scanner.log
```

## File Map
| File | What It Does |
|------|-------------|
| **Bot modules** | |
| `bot/bot.py` | Coordinator: init, main loop, tick orchestration |
| `bot/config.py` | BotConfig dataclass, YAML loading |
| `bot/config.yml` | Bot config (leverage, SL, DSL tiers) |
| `bot/dsl.py` | Dynamic Stop Loss engine (tiered trailing) |
| `bot/auth_helper.py` | Lighter REST API auth tokens |
| `bot/api/lighter_api.py` | DEX API wrapper: orders, positions, prices |
| `bot/api/proxy_patch.py` | SOCKS5 proxy monkey-patch |
| `bot/core/models.py` | TrackedPosition + BotState dataclasses |
| `bot/core/position_tracker.py` | DSL + legacy trailing TP/SL |
| `bot/core/execution_engine.py` | Main tick loop + position processing |
| `bot/core/signal_processor.py` | Thin wrapper (compatibility) |
| `bot/core/signal_handler.py` | Scanner signal processing |
| `bot/core/decision_handler.py` | AI decision validation + dispatch |
| `bot/core/executor.py` | AI open/close/close_all execution |
| `bot/core/verifier.py` | Position verification + fill polling |
| `bot/core/result_writer.py` | AI result IPC writing |
| `bot/core/shared_utils.py` | Pacing, quota, market ID, outcome logging |
| `bot/core/state_manager.py` | Save/load/reconcile state |
| `bot/core/order_manager.py` | Quota checks + order pacing |
| `bot/alerts/telegram.py` | Telegram alerts |
| **Scanner modules** | |
| `scanner/src/main.ts` | Entry point, module wiring |
| `scanner/src/types.ts` | All interfaces |
| `scanner/src/config.ts` | Constants + settings |
| `scanner/src/output.ts` | Console display + signals.json write |
| `scanner/src/position-sizing.ts` | Risk-based sizing + safety checks |
| `scanner/src/direction-vote.ts` | Long/short majority vote |
| `scanner/src/api/lighter.ts` | Lighter REST calls |
| `scanner/src/api/okx.ts` | OKX klines + cache |
| `scanner/src/signals/funding-spread.ts` | Funding rate spread score |
| `scanner/src/signals/price-momentum.ts` | Daily price change score |
| `scanner/src/signals/moving-average-alignment.ts` | MA structure score |
| `scanner/src/signals/order-block.ts` | Order block detection + score |
| `scanner/src/signals/oi-trend.ts` | OI change score |
| **AI Decisions modules** | |
| `ai-decisions/ai_trader.py` | Thin coordinator — init, daemon loop, main() |
| `ai-decisions/cycle_runner.py` | Cycle orchestration — context → LLM → parse → safety → IPC → log |
| `ai-decisions/db.py` | SQLite decision journal (unchanged) |
| `ai-decisions/llm_client.py` | OpenRouter LLM client (unchanged) |
| `ai-decisions/safety.py` | Hard safety rules (unchanged) |
| `ai-decisions/llm/parser.py` | Parse LLM JSON responses |
| `ai-decisions/context/data_reader.py` | Read signals + positions from files, DB fallback |
| `ai-decisions/context/pattern_engine.py` | Learned pattern rules with decay/reinforcement |
| `ai-decisions/context/outcome_analyzer.py` | Extracts patterns from trade outcomes → reinforces pattern engine |
| `ai-decisions/context/stats_formatter.py` | Performance stats + hold regret formatting |
| `ai-decisions/context/prompt_builder.py` | LLM prompt assembly with token budget |
| `ai-decisions/context/sanitizer.py` | Prompt injection detection |
| `ai-decisions/context/token_estimator.py` | Token counting (tiktoken with fallback) |
| `ai-decisions/ipc/bot_protocol.py` | IPC: send decisions, check results, emergency halt |

## IPC Files
| File | Written By | Read By |
|------|-----------|---------|
| `signals/signals.json` | Scanner | AI Trader, Bot |
| `signals/ai-decision.json` | `ipc/bot_protocol.py` | Bot |
| `signals/ai-result.json` | Bot | AI Trader |
| `bot/state/quota_state.json` | Bot | Bot (restart continuity) |

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
- venv: `projects/autopilot-trader/bot/venv/`
- secrets: `/root/.openclaw/workspace/.env`
- scanner runs via Bun: `bun run scanner/src/main.ts`
