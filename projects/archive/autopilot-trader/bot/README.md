# Lighter Copilot 🦊

Autopilot trading bot for [Lighter.xyz](https://lighter.xyz) perpetual futures.

## Features

- **Trailing Take Profit** — locks in gains by trailing the peak price
- **Dynamic Stop Loss (DSL)** — tiered trailing stop with high-water mark tracking
- **AI Autopilot Mode** — reads open/close decisions from `ai-decision.json` and executes them automatically
- **Position Verification** — confirms positions on exchange after open, progressive retry for close
- **Telegram Alerts** — notifications for all major events (entry, TP/SL triggers, errors)
- **Kill Switch** — file-based emergency stop (`state/KILL_SWITCH` file triggers immediate halt)
- **Crash Recovery** — state persisted to disk, resumes on restart
- **Volume Quota Management** — exponential backoff when hitting rate limits
- **Circuit Breaker** — halts after repeated failed close attempts
- **Idle Polling** — reduced API calls when flat
- **SOCKS5 Proxy Support** — for network-level privacy
- **Cross Margin ROE** — accurate PnL calculation
- **Zero fees** — Lighter Standard accounts have 0% maker/taker fees

## Architecture

```
bot.py
├── BotConfig           — YAML config loader
├── TelegramAlerter     — Telegram notification sender
├── PositionTracker     — Tracks positions, computes TP/SL/DSL levels
└── LighterCopilot      — Main bot loop, connects API + tracker + alerts
```

## Setup

```bash
cd bot
pip install -r requirements.txt
cp config.example.yml config.yml
# Edit config.yml with your Lighter credentials + Telegram token
python3 bot.py
```

### Getting your Lighter API key

1. Go to [app.lighter.xyz](https://app.lighter.xyz)
2. Settings → API Keys
3. Create key (Standard account = 0% fees, 60 req/min)
4. Copy the private key, account index, and API key index

### Getting Telegram alerts (optional)

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram
2. Create a bot, copy the token
3. Send `/start` to your bot
4. Get your chat ID via `https://api.telegram.org/bot<TOKEN>/getUpdates`
