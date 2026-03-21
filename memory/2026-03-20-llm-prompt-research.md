# 2026-03-19 (continued) — LLM Prompt Engineering for Crypto Trading

## Task
Research: LLM prompt engineering for crypto trading — confidence calibration, funding arb framing, position management prompts, sizing rubrics.

## Deliverable
Full report with actionable recommendations for integrating LLM prompts into the autopilot pipeline.
See: /root/.openclaw/agents/blitz/agent/memory/llm-prompt-engineering-trading-report.md

---

# 2026-03-20/21 — Blitz Daily Log

## FLOOP Pro Indicator Analysis
- John shared TradingView indicator: "FLOOP Pro" by TheRealDrip2Rip
- ML-optimized range filter with 14-point scoring system
- Conclusion: architecture is solid (weighted multi-factor scoring), but performance claims (83-88% win rate, 4-5 PF) are curve-fitted/backtested without slippage
- Worth porting the scoring rubric concept, not the specific weights

## Full Trading Setup Audit
- Audited the entire autopilot pipeline: scanner, AI trader, safety layer, DSL, executor
- Identified 12 flaws ranked by severity
- Critical: 5% risk per trade too high, no correlation guard, no daily drawdown circuit breaker
- Major: 60-min stagnation too slow, signal weights never auto-applied, LLM flip-flop guard missing
- Moderate: no backtest framework, reflection loop disconnected, no slippage tracking

## Correlation Guard — Built & Tested
- Created `signals/scripts/correlation-guard.ts`
- Pearson correlation from 30-day daily returns via OKX API
- Tested live: BTC↔ETH=0.93, BTC↔SOL=0.95, ETH↔LINK=0.96 (all blocked at 0.75 threshold)
- SOL↔TON=0.43 (allowed — diversified enough)
- Module ready for integration, John holding until needed
- Integration spec at: `memory/2026-03-21-correlation-guard.md`
