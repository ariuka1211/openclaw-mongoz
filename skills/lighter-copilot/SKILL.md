# Lighter Copilot Skill

## What It Does
Automated trailing stop-loss bot for Lighter.xyz perpetual futures. Monitors positions via API through a Netherlands proxy (geo-restriction bypass). Sends Telegram alerts to @freddy_lit_bot.

## When To Use
- User asks about trading, positions, P&L, balance → run `status.sh`
- User wants to open a trade → run `open-position.sh <symbol> <side> <size_usdc> <leverage>`
- User wants to close a trade → run `close-position.sh <symbol>`
- User asks about bot health → check `bot.log`
- User wants to change trailing settings → edit `config.yml` then restart bot

## Architecture
```
User → Maaraa (me) → CLI scripts → Lighter API (via Netherlands proxy)
                  ↘ bot.py (background) → monitors + auto-closes via trailing SL
                  ↘ Telegram alerts → @freddy_lit_bot
```

## Files
- `bot.py` — Main monitoring bot (async, 5s polling loop)
- `config.yml` — Credentials, strategy params, proxy
- `scripts/status.sh` — Get positions, balance, P&L
- `scripts/open-position.sh` — Place market order
- `scripts/close-position.sh` — Close position via market order
- `scripts/restart.sh` — Restart bot process
- `scripts/bot-status.sh` — Check if bot process is running

## Key Config (config.yml)
| Param | Current | Meaning |
|-------|---------|---------|
| trailing_tp_trigger_pct | 0 | Trail starts immediately (0 = from entry) |
| trailing_tp_delta_pct | 1 | Trail 1% below peak |
| sl_pct | 2 | Hard stop loss at 2% below entry |

## Bot Management
- Bot runs as background Python process
- PID tracked in `bot.pid`
- Logs to `bot.log`
- Restart: `bash scripts/restart.sh`

## Geo-Restriction
Lighter blocks US IPs. Bot routes all API calls through Netherlands SOCKS5 proxy at `64.137.96.74:6641`. If proxy dies, bot stops working.

## Telegram Alerts
Bot `@freddy_lit_bot` sends alerts for:
- New position opened
- Trailing SL activated/updated
- TP/SL triggered

## Important Notes
- Position amounts are in integer units based on Lighter's size_decimals (6) and price_decimals (1)
- Market IDs: BTC=1, ETH=2
- Bot uses `create_market_order` for closing positions
- All API calls go through proxy — don't bypass it
