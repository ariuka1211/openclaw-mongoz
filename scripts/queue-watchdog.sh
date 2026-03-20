#!/bin/bash
# OpenClaw Queue Watchdog
# Monitors gateway logs for stuck messages and alerts via Telegram
# Runs every 2 minutes via cron

BOT_TOKEN="8677962428:AAEHnCJsk3g0YXfpXfvwU06-IMx8IbYIbi8"
CHAT_ID="1736401643"
LOG="/tmp/openclaw/openclaw-$(date -u +%Y-%m-%d).log"
STATE_FILE="/tmp/queue-watchdog-last-alert"
SUMMARY_STATE="/tmp/queue-watchdog-last-summary"
COOLDOWN=300  # Don't spam - min 5 min between alerts

# Thresholds
LANE_WAIT_WARN_MS=60000    # 60 seconds lane wait = warn
LANE_WAIT_CRIT_MS=300000   # 5 minutes = critical

NOW=$(date +%s)

# Check last alert time (cooldown) — applies to ALL alerts including gateway-down
if [ -f "$STATE_FILE" ]; then
  LAST_ALERT=$(cat "$STATE_FILE")
  ELAPSED=$((NOW - LAST_ALERT))
  if [ "$ELAPSED" -lt "$COOLDOWN" ]; then
    exit 0
  fi
fi

# Check if gateway is even running (via health endpoint, works in cron)
if ! curl -sf http://127.0.0.1:18789/health > /dev/null 2>&1; then
  # Send critical alert - gateway is DOWN (cooldown already checked above)
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    -d "text=🚨 *Queue Watchdog*\n\nGateway is DOWN! Not responding on port 18789." \
    -d "parse_mode=Markdown" > /dev/null 2>&1
  echo "$NOW" > "$STATE_FILE"
  echo "Gateway down - alert sent"
  exit 0
fi

if [ ! -f "$LOG" ]; then
  exit 0
fi

# Look for recent lane wait errors (last 10 min to match cron interval)
# Use epoch-based filtering instead of fragile date -d
LOG_LINES=$(tail -500 "$LOG" 2>/dev/null | grep "lane wait exceeded\|lane task error\|embedded run timeout\|FailoverError.*timed out\|empty normalized summary\|all extraction attempts exhausted\|retry succeeded")
if [ -z "$LOG_LINES" ]; then
  exit 0
fi

# Filter to lines from the last 10 minutes by extracting timestamp and comparing
NOW_EPOCH=$(date +%s)
RECENT=""
while IFS= read -r line; do
  # Extract ISO timestamp from log line (e.g., 2026-03-17T00:15:30...)
  LOG_TIME=$(echo "$line" | grep -oP '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' | head -1)
  if [ -n "$LOG_TIME" ]; then
    LOG_EPOCH=$(date -d "$LOG_TIME" +%s 2>/dev/null)
    if [ -n "$LOG_EPOCH" ]; then
      AGE=$((NOW_EPOCH - LOG_EPOCH))
      if [ "$AGE" -le 600 ]; then
        RECENT="$RECENT
$line"
      fi
    fi
  fi
done <<< "$LOG_LINES"

# Track successful LCM summaries — alert which model is working
LCM_SUCCESS=$(tail -100 "$LOG" 2>/dev/null | grep "\[lcm\].*retry succeeded" | tail -1)
if [ -n "$LCM_SUCCESS" ]; then
  SUM_TIME=$(echo "$LCM_SUCCESS" | grep -oP '"date":"\K[^"]+')
  SUM_MODEL=$(echo "$LCM_SUCCESS" | grep -oP 'model=\K[^;, ]+' | tail -1 | sed 's|.*/||;s|:.*||')
  if [ -f "$SUMMARY_STATE" ]; then
    LAST_SUM_TIME=$(cat "$SUMMARY_STATE")
  else
    LAST_SUM_TIME=""
  fi
  if [ "$SUM_TIME" != "$LAST_SUM_TIME" ] && [ -n "$SUM_MODEL" ]; then
    echo "$SUM_TIME" > "$SUMMARY_STATE"
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
      -d "chat_id=${CHAT_ID}" \
      -d "text=📝 *LCM Summary Succeeded*\n\nModel: \`${SUM_MODEL}\`\n(after retry)" \
      -d "parse_mode=Markdown" > /dev/null 2>&1
  fi
fi

if [ -z "$RECENT" ]; then
  exit 0
fi

# Parse the worst wait time
MAX_WAIT=0
ALERT_MSG=""
while IFS= read -r line; do
  if echo "$line" | grep -q "lane wait exceeded"; then
    # Extract waitedMs value
    WAIT_MS=$(echo "$line" | grep -oP 'waitedMs=\K\d+')
    if [ -n "$WAIT_MS" ] && [ "$WAIT_MS" -gt "$MAX_WAIT" ]; then
      MAX_WAIT=$WAIT_MS
      LANE=$(echo "$line" | grep -oP 'lane=\K[^ ]+')
      QUEUE=$(echo "$line" | grep -oP 'queueAhead=\K\d+')
      WAIT_SEC=$((WAIT_MS / 1000))
      # Grab model from recent LCM log lines
      RECENT_MODEL=$(tail -50 "$LOG" 2>/dev/null | grep -oP 'model=\K[^;, ]+' | tail -1 | sed 's|.*/||;s|:.*||')
      [ -n "$RECENT_MODEL" ] && MODEL_TAG=" (model: \`${RECENT_MODEL}\`)" || MODEL_TAG=""
      ALERT_MSG="⚠️ *Queue Watchdog*\n\nLane \`$LANE\` stuck for *${WAIT_SEC}s*\nQueue ahead: ${QUEUE}${MODEL_TAG}\n\nLLM request may be hanging."
    fi
  elif echo "$line" | grep -q "lane task error"; then
    WAIT_MS=$(echo "$line" | grep -oP 'durationMs=\K\d+')
    if [ -n "$WAIT_MS" ] && [ "$WAIT_MS" -gt "$MAX_WAIT" ]; then
      MAX_WAIT=$WAIT_MS
      ERROR=$(echo "$line" | grep -oP 'error="\K[^"]+')
      WAIT_SEC=$((WAIT_MS / 1000))
      RECENT_MODEL=$(tail -50 "$LOG" 2>/dev/null | grep -oP 'model=\K[^;, ]+' | tail -1 | sed 's|.*/||;s|:.*||')
      [ -n "$RECENT_MODEL" ] && MODEL_TAG=" (model: \`${RECENT_MODEL}\`)" || MODEL_TAG=""
      ALERT_MSG="🚨 *Queue Watchdog*\n\nLane error after *${WAIT_SEC}s*\n\`$ERROR\`${MODEL_TAG}\n\nGateway may need restart."
    fi
  elif echo "$line" | grep -q "embedded run timeout"; then
    ALERT_MSG="🔥 *Queue Watchdog*\n\nEmbedded run timed out.\nSub-agent may be blocking the main lane."
  elif echo "$line" | grep -q "empty normalized summary"; then
    FAILED_MODEL=$(echo "$line" | grep -oP 'model=\K[^;, ]+' | head -1 | sed 's|.*/||;s|:.*||')
    [ -n "$FAILED_MODEL" ] && MODEL_TAG="\`${FAILED_MODEL}\`" || MODEL_TAG="unknown"
    RETRY_OK=$(echo "$RECENT" | grep "retry succeeded" | wc -l)
    ALERT_MSG="🔄 *LCM Summary Alert*\n\nModel $MODEL_TAG returning thinking-only blocks.\nRetries this window: $RETRY_OK\n\nSummary model may need changing."
  elif echo "$line" | grep -q "all extraction attempts exhausted"; then
    FAILED_MODEL=$(echo "$line" | grep -oP 'model=\K[^;, ]+' | head -1 | sed 's|.*/||;s|:.*||')
    [ -n "$FAILED_MODEL" ] && MODEL_TAG="\`${FAILED_MODEL}\`" || MODEL_TAG="unknown"
    # Check if rate limited
    RATE_LIMITED=$(echo "$RECENT" | grep "429\|rate.limit\|Rate limit" | wc -l)
    if [ "$RATE_LIMITED" -gt 0 ]; then
      ALERT_MSG="🔥 *LCM Summary Alert*\n\nModel $MODEL_TAG — rate limited (429).\nAll extraction attempts exhausted.\nFree tier limits hit — need paid model or own API key."
    else
      ALERT_MSG="🔥 *LCM Summary Alert*\n\nModel $MODEL_TAG — all extraction attempts exhausted.\nSummary model failing completely."
    fi
  fi
done <<< "$RECENT"

if [ -n "$ALERT_MSG" ]; then
  # Send Telegram alert
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    -d "text=$(echo -e "$ALERT_MSG")" \
    -d "parse_mode=Markdown" \
    -d "disable_notification=false" > /dev/null 2>&1

  # Record alert time
  echo "$NOW" > "$STATE_FILE"
  echo "Alert sent: $ALERT_MSG"
fi
