# Crypto Trading Agent Projects — Pocketed Ideas

Saved: 2026-03-20

## MiroFish-Adjacent / Swarm Intelligence

### 1. TradingAgents ⭐ (most mature)
- **Repo:** github.com/TauricResearch/TradingAgents
- **What:** Multi-agent LLM framework mimicking a real trading firm
- **Architecture:** fundamental analyst → sentiment analyst → news analyst → technical analyst → bullish/bearish researchers debate → trader decides → risk managers approve
- **Backtest:** June-Nov 2024, outperformed Buy-and-Hold and MACD (top Sharpe, low drawdown)
- **Models:** GPT-5, Gemini 3, Claude 4, Grok 4
- **Paper:** arxiv.org/abs/2412.20138
- **Gap:** Built for stocks. No funding rate logic. Needs adaptation for crypto perps.
- **Use case:** Base architecture for a multi-agent crypto trading decision system.

### 2. AI Trading Platform (ruvnet gist)
- **Repo:** gist.github.com/ruvnet/eb28152cb122c9e0336cb8b1b25c01b3
- **What:** Swarm of 5-100 agents with neural forecasting
- **Features:** NHITS/NBEATSx models, Polymarket integration, Monte Carlo risk sims, sub-10ms inference, GPU accelerated, Claude Code / MCP native
- **Gap:** Experimental, less documented
- **Use case:** Polymarket prediction market trading, neural forecasting signals.

### 3. CryptoAgent (Swarms framework)
- **Repo:** github.com/The-Swarm-Corporation/CryptoAgent
- **What:** Swarms framework for real-time crypto analysis via CoinGecko + OpenAI
- **Gap:** More research assistant than trading system
- **Use case:** Simple multi-coin analysis, foundation for sentiment layer.

### 4. FinRL_Crypto (Reinforcement Learning)
- **Repo:** github.com/AI4Finance-Foundation/FinRL_Crypto
- **What:** Deep RL for crypto trading, overfitting reduction (walkforward, CPCV)
- **Gap:** No multi-agent / swarm component
- **Use case:** RL-based position sizing or entry/exit optimization.

### 5. FinRL-DeepSeek (2025 contest entry)
- **Repo:** github.com/Mattbusel/FinRL_DeepSeek_Crypto_Trading
- **What:** RL crypto agents with LLM-derived signals
- **Use case:** Combining LLM sentiment signals with RL execution.

### 6. Intel Swarm
- **Repo:** github.com/outsmartchad/intel-swarm
- **What:** Swarm of AI agents for web scraping/analysis/synthesis
- **Use case:** Adaptable to crypto sentiment gathering.

### 7. Swarm Prediction Engine
- **Repo:** github.com/vedangvatsa123/vedang-swarm-prediction
- **What:** Multi-agent AI debate system for predictions
- **Gap:** Not crypto-specific
- **Use case:** General prediction framework, could wrap crypto signals.

### 8. freqtrade + FreqAI
- **Repo:** github.com/freqtrade/freqtrade
- **What:** Classic open-source crypto bot with ML optimization
- **Features:** Binance Futures support, backtesting, hyperopt, Telegram control
- **Use case:** Battle-tested execution layer. Could plug LLM agents in as signal generators.

## MiroFish Itself
- **Repo:** github.com/666ghj/MiroFish (35k+ stars)
- **What:** Swarm intelligence simulation engine, 2k-500k agents
- **Math:** DeGroot-Neumann opinion updates, PageRank centrality, Granovetter threshold cascades
- **Trading use case:** Feed crypto news → simulate market participant reactions → get probability distribution
- **Limitation:** Heavy LLM cost per simulation, no benchmarks, no crypto-specific version
- **Polymarket hack:** Someone made $4,266 over 338 trades simulating 2,847 agents per trade

## The Gap (opportunity)
Nobody has built MiroFish's "spawn 2,000 simulated market participants and watch them react" specifically for crypto perps. The most promising path: TradingAgents architecture (multi-agent debate → decision → risk check) adapted for funding rate data, order flow, and on-chain signals.

## Key References
- X algorithm mechanics: /root/.openclaw/workspace/memory/x-algorithm-mechanics.md (self-learning agent pattern transferable to trading)
- Nansen API: Free tier (100 credits), Pro $49/mo (1,000 credits), x402 pay-per-request option
- MiroFish tweet: x.com/mikita_crypto/status/2034699190547489068
