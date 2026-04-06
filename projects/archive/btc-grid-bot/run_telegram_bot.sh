#!/bin/bash
cd /root/.openclaw/workspace/projects/btc-grid-bot
source /root/.venv/bin/activate 2>/dev/null || true
exec python telegram_bot.py
