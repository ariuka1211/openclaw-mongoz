#!/bin/bash
cd /root/.openclaw/workspace/projects/btc-grid-bot
exec python3 -u main.py 2>&1 | tee -a logs/bot.log
