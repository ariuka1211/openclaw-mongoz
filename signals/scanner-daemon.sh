#!/bin/bash
# Persistent scanner — runs continuously with interval, no cron respawn needed
INTERVAL=300  # 5 minutes
LOG="/root/.openclaw/workspace/signals/scanner.log"
cd /root/.openclaw/workspace/signals

while true; do
  /usr/local/bin/bun run scripts/opportunity-scanner.ts --max-positions 3 >> "$LOG" 2>&1
  sleep $INTERVAL
done
