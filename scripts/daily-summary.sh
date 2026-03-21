#!/usr/bin/env bash
# daily-summary.sh — Sends a daily cron job summary to Telegram
# Runs once per morning, summarizes all cron job results from the past 24h

set -euo pipefail

CONFIG="/root/.openclaw/openclaw.json"
BOT_TOKEN=$(jq -r '.channels.telegram.botToken' "$CONFIG" 2>/dev/null)
CHAT_ID="1736401643"
DATE=$(TZ='America/Denver' date '+%Y-%m-%d')
NOW=$(TZ='America/Denver' date '+%I:%M %p %Z')

# Helper: get last run time from log mtime, or "N/A"
get_age() {
  local log="$1"
  if [ -f "$log" ]; then
    local mtime=$(stat -c %Y "$log" 2>/dev/null)
    local now=$(date +%s)
    local diff=$((now - mtime))
    local hours=$((diff / 3600))
    local mins=$(( (diff % 3600) / 60 ))
    if [ $hours -gt 0 ]; then
      echo "${hours}h ${mins}m ago"
    else
      echo "${mins}m ago"
    fi
  else
    echo "N/A"
  fi
}

# Helper: check for errors in log (look for ❌ or "error" or non-zero exit)
check_status() {
  local log="$1"
  if [ ! -f "$log" ]; then
    echo "❌ Missing"
    return
  fi
  if grep -qiE '❌|error|failed|FATAL' "$log" 2>/dev/null; then
    echo "⚠️ Warnings"
  else
    echo "✅ OK"
  fi
}

# ─── Collect results ───

# Healthcheck
HC_STATUS=$(check_status "/tmp/healthcheck.log")
HC_AGE=$(get_age "/tmp/healthcheck.log")
HC_ISSUES="0 issues"
if [ -f "/tmp/healthcheck.log" ]; then
  HC_ISSUES=$(grep -oP '\d+ issues?' /tmp/healthcheck.log | tail -1 || echo "0 issues")
fi

# Backup
BK_STATUS=$(check_status "/tmp/backup-memory.log")
BK_AGE=$(get_age "/tmp/backup-memory.log")
BK_DEST="—"
if [ -f "/tmp/backup-memory.log" ]; then
  BK_DEST=$(grep -oP '→ \K.+' /tmp/backup-memory.log | tail -1 || echo "—")
fi

# Memory extract
ME_STATUS=$(check_status "/tmp/memory-extract.log")
ME_AGE=$(get_age "/tmp/memory-extract.log")

# Signal decay
SD_STATUS=$(check_status "/tmp/signal-decay.log")
SD_AGE=$(get_age "/tmp/signal-decay.log")
SD_INFO="—"
if [ -f "/tmp/signal-decay.log" ]; then
  SD_INFO=$(tail -1 /tmp/signal-decay.log)
fi

# Memory cleanup
MC_STATUS=$(check_status "/tmp/memory-cleanup.log")
MC_AGE=$(get_age "/tmp/memory-cleanup.log")

# Learning graduate (weekly, Sunday)
LG_STATUS=$(check_status "/tmp/learning-graduate.log")
LG_AGE=$(get_age "/tmp/learning-graduate.log")

# ─── System quick stats ───
DISK_USED=$(df -h / | awk 'NR==2{print $5}')
DISK_AVAIL=$(df -h / | awk 'NR==2{print $4}')
LOAD=$(awk '{print $1}' /proc/loadavg)
UPTIME=$(uptime -p 2>/dev/null || uptime | sed 's/.*up /up /' | sed 's/,.*//')
RAM_USED=$(free -h | awk '/Mem:/{print $3}')
RAM_TOTAL=$(free -h | awk '/Mem:/{print $2}')

# ─── Build message ───
MSG="☀️ Daily Summary — ${DATE}

🔧 Cron Jobs
  healthcheck: ${HC_STATUS} (${HC_AGE}) — ${HC_ISSUES}
  backup: ${BK_STATUS} (${BK_AGE})
  memory-extract: ${ME_STATUS} (${ME_AGE})
  signal-decay: ${SD_STATUS} (${SD_AGE})
  memory-cleanup: ${MC_STATUS} (${MC_AGE})
  learning-graduate: ${LG_STATUS} (${LG_AGE}) [weekly]

💾 System
  Disk: ${DISK_USED} used (${DISK_AVAIL} free)
  RAM: ${RAM_USED}/${RAM_TOTAL}
  Load: ${LOAD}
  Uptime: ${UPTIME}

Generated at ${NOW}"

# ─── Send via Telegram ───
if [ -n "$BOT_TOKEN" ] && [ "$BOT_TOKEN" != "null" ]; then
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${MSG}" >/dev/null 2>&1
  echo "Summary sent to Telegram at $(date -u '+%H:%M UTC')"
else
  echo "Error: Bot token not found"
  echo "$MSG"
fi
