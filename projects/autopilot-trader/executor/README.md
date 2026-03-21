# Lighter Copilot 🦊

Trailing take profit + stop loss bot for [Lighter.xyz](https://lighter.xyz) perpetual futures.

## Features

- **Trailing Take Profit** — locks in gains by trailing the peak price
- **Stop Loss** — automatic position protection
- **Telegram Alerts** — get notified when TP/SL triggers
- **Zero fees** — Lighter Standard accounts have 0% maker/taker fees

## Trailing TP Logic

1. You enter a LONG position at $100
2. Price rises to $103 → trailing activates (+3% trigger)
3. Price rises to $105 → new high-water mark
4. Price drops to $104 → trailing TP triggers (1% from $105 peak)
5. Position closed, profit locked in at ~+4%

For SHORT positions, same logic inverted.

## Setup

```bash
cd executor
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

## Architecture

```
bot.py
├── BotConfig           — YAML config loader
├── TelegramAlerter     — Telegram notification sender
├── PositionTracker     — Tracks positions, computes TP/SL levels
└── LighterCopilot      — Main bot loop, connects API + tracker + alerts
```

## Status

- [x] Skeleton with trailing TP/SL logic
- [ ] Real Lighter API integration (positions, prices, order execution)
- [ ] WebSocket price feed
- [ ] Suggestions engine
- [ ] Dashboard / web UI
