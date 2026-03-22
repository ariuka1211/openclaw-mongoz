#!/usr/bin/env bash
# daily-summary.sh — Sends a daily cron job summary to Telegram
# Runs once per morning, summarizes all cron job results from the past 24h

set -euo pipefail

CONFIG="/root/.openclaw/openclaw.json"
BOT_TOKEN=$(jq -r '.channels.telegram.accounts.default.botToken' "$CONFIG" 2>/dev/null)
CHAT_ID="1736401643"
DATE=$(TZ='America/Denver' date '+%Y-%m-%d')
NOW=$(TZ='America/Denver' date '+%I:%M %p %Z')
STATE_DIR="/tmp/cron-state"

# Helper: get age from .last-run timestamp file (written by cron jobs on success)
get_age() {
  local name="$1"
  local stamp="${STATE_DIR}/${name}.last-run"
  if [ ! -f "$stamp" ]; then
    echo "N/A"
    return
  fi
  local mtime
  mtime=$(stat -c %Y "$stamp" 2>/dev/null) || true
  if [ -z "$mtime" ]; then
    echo "N/A"
    return
  fi
  local now=$(date +%s)
  local diff=$((now - mtime))
  local hours=$((diff / 3600))
  local mins=$(( (diff % 3600) / 60 ))
  if [ $hours -gt 0 ]; then
    echo "${hours}h ${mins}m ago"
  else
    echo "${mins}m ago"
  fi
}

# Helper: check status by looking at the last run block in log file
check_status() {
  local log="$1"
  if [ ! -f "$log" ]; then
    echo "❌ Missing"
    return
  fi
  local last_block=""
  last_block=$(awk '/=== .*start ===/{block=""} {block=block"\n"$0} END{print block}' "$log" 2>/dev/null)
  if [ -z "$last_block" ]; then
    last_block=$(cat "$log")
  fi
  if echo "$last_block" | grep -qiE '❌|FATAL|exhausted retries|failed after|command not found|permission denied|No such file'; then
    echo "⚠️ Warnings"
  else
    echo "✅ OK"
  fi
}

# ─── Collect results ───

# Backup
BK_STATUS=$(check_status "/tmp/backup-memory.log")
BK_AGE=$(get_age "backup")

# Memory extract
ME_STATUS=$(check_status "/tmp/memory-session-extract.log")
ME_AGE=$(get_age "memory-extract")

# Memory cleanup
MC_STATUS=$(check_status "/tmp/memory-nightly-cleanup.log")
MC_AGE=$(get_age "memory-cleanup")

# Learning graduate (weekly, Sunday)
LG_STATUS=$(check_status "/tmp/learning-graduate.log")
LG_AGE=$(get_age "learning-graduate")

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
  backup: ${BK_STATUS} (${BK_AGE})
  memory-extract: ${ME_STATUS} (${ME_AGE})
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
  echo "Summary sent to Telegram at $(TZ='America/Denver' date '+%H:%M %Z')"
else
  echo "Error: Bot token not found"
  echo "$MSG"
fi
