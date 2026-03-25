#!/bin/bash
# OSA hourly scan — sends Telegram alerts if opportunities found
cd /root/.openclaw/workspace/projects/autopilot-trader/scanner
/usr/bin/python3 scanner.py --alert >> /root/.openclaw/workspace/projects/autopilot-trader/bot/osa.log 2>&1
echo "--- Scan completed at $(date) ---" >> /root/.openclaw/workspace/projects/autopilot-trader/bot/osa.log
