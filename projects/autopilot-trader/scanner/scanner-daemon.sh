#!/bin/bash
# Persistent scanner — runs continuously with interval, no cron respawn needed
INTERVAL=300  # 5 minutes
LOG="/root/.openclaw/workspace/projects/autopilot-trader/scanner/scanner.log"
cd /root/.openclaw/workspace/projects/autopilot-trader/scanner

while true; do
  /usr/local/bin/bun run opportunity-scanner.ts --max-positions 3 >> "$LOG" 2>&1
  sleep $INTERVAL
done
