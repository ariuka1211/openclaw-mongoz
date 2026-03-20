#!/bin/bash
# System Health Dashboard — validates OUTPUT QUALITY, not just uptime
# Checks: LCM summaries, extensions, watchdog, gateway
# Usage: ./healthcheck.sh [--json] [--quiet]

LCM_DB="/root/.openclaw/lcm.db"
CONFIG="/root/.openclaw/openclaw.json"
JSON_MODE=false
QUIET=false

for arg in "$@"; do
  case "$arg" in
    --json) JSON_MODE=true ;;
    --quiet) QUIET=true ;;
  esac
done

REPORT=""
ISSUES=0
CHECKS=0

check() {
  local name="$1"
  local status="$2"  # ok|warn|fail
  local detail="$3"
  CHECKS=$((CHECKS + 1))
  
  if [ "$status" = "fail" ]; then
    ISSUES=$((ISSUES + 1))
    REPORT="${REPORT}\n🔴 ${name}: ${detail}"
  elif [ "$status" = "warn" ]; then
    REPORT="${REPORT}\n🟡 ${name}: ${detail}"
  else
    REPORT="${REPORT}\n🟢 ${name}: ${detail}"
  fi
}

# ─── 1. Gateway Process + HTTP ───
GW_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time 5 "http://127.0.0.1:18789/health" 2>/dev/null)
if [ "$GW_HTTP" = "200" ]; then
  GW_PID=$(pgrep -f "openclaw" | head -1)
  GW_MEM=$(ps -p "$GW_PID" -o rss= 2>/dev/null | awk '{printf "%.0f", $1/1024}')
  check "Gateway" "ok" "HTTP 200, PID ${GW_PID}, ${GW_MEM}MB RAM"
else
  check "Gateway" "fail" "HTTP ${GW_HTTP} — gateway not responding"
fi

# ─── 2. LCM Summary Quality (last 24h) ───
if [ -f "$LCM_DB" ]; then
  TOTAL_24H=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM summaries WHERE created_at > datetime('now', '-24 hours')" 2>/dev/null || echo 0)
  if [ "$TOTAL_24H" -gt 0 ] 2>/dev/null; then
    BAD_24H=$(sqlite3 "$LCM_DB" "
      SELECT COUNT(*) FROM summaries 
      WHERE created_at > datetime('now', '-24 hours') AND (
        content LIKE 'We need to%' OR
        content LIKE '%We need to summarize%' OR
        content LIKE '%We need to produce%' OR
        content LIKE '%[LCM fallback summary%' OR
        content LIKE '<!DOCTYPE%' OR
        token_count < 50
      )
    " 2>/dev/null || echo 0)
    REAL_24H=$((TOTAL_24H - BAD_24H))
    BAD_PCT=$(( BAD_24H * 100 / TOTAL_24H ))
    
    if [ "$BAD_PCT" -gt 50 ]; then
      check "LCM Quality (24h)" "fail" "${BAD_24H}/${TOTAL_24H} summaries are garbage (${BAD_PCT}% bad)"
    elif [ "$BAD_PCT" -gt 10 ]; then
      check "LCM Quality (24h)" "warn" "${BAD_24H}/${TOTAL_24H} summaries degraded (${BAD_PCT}% bad)"
    else
      check "LCM Quality (24h)" "ok" "${REAL_24H}/${TOTAL_24H} real summaries (${BAD_PCT}% bad)"
    fi
  else
    check "LCM Quality (24h)" "warn" "No summaries generated in last 24h"
  fi
  
  # LCM DB freshness
  LCM_AGE=$(( $(date +%s) - $(stat -c %Y "$LCM_DB" 2>/dev/null || echo 0) ))
  if [ "$LCM_AGE" -gt 3600 ]; then
    check "LCM Freshness" "warn" "DB not written in $((LCM_AGE/3600))h $((LCM_AGE%3600/60))m"
  else
    check "LCM Freshness" "ok" "Last write ${LCM_AGE}s ago"
  fi
  
  # LCM DB size
  LCM_SIZE=$(du -sh "$LCM_DB" 2>/dev/null | cut -f1)
  LCM_MSGS=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "?")
  LCM_SUMS=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM summaries" 2>/dev/null || echo "?")
  check "LCM Storage" "ok" "${LCM_SIZE}, ${LCM_MSGS} msgs, ${LCM_SUMS} summaries"
else
  check "LCM DB" "fail" "Database file not found at ${LCM_DB}"
fi

# ─── 3. Extensions Check ───
# Engram removed — no longer used

# Check if memory-core plugin is loaded (check config + extension files)
if [ -d "/root/.openclaw/extensions/memory-core" ] || grep -q '"memory-core"' "$CONFIG" 2>/dev/null; then
  check "Memory-Core Plugin" "ok" "Configured"
else
  check "Memory-Core Plugin" "warn" "Not configured or not installed"
fi

# ─── 4. Lossless-Claw Extension ───
LC_EXT="/root/.openclaw/extensions/lossless-claw"
if [ -d "$LC_EXT" ]; then
  # Check if summarize.ts has the thinking fix
  if grep -q "thinking" "$LC_EXT/src/summarize.ts" 2>/dev/null; then
    THINKING_EXCLUDED=$(grep -c "thinking" "$LC_EXT/src/summarize.ts" 2>/dev/null || echo 0)
    # If thinking appears only in comments/type defs but NOT in text extraction = good
    if grep "content.*thinking\|text.*thinking\|\.thinking" "$LC_EXT/src/summarize.ts" 2>/dev/null | grep -v "//\|type\|interface\|export" | grep -q "thinking"; then
      check "Lossless-Claw" "fail" "thinking field still in text extraction — needs patch"
    else
      check "Lossless-Claw" "ok" "Installed, thinking field excluded from summaries"
    fi
  else
    check "Lossless-Claw" "ok" "Installed, no thinking references (clean)"
  fi
  
  # RAM usage — LCM runs inside gateway, so report gateway RAM
  GW_PID=$(pgrep -f "openclaw" | head -1)
  LC_RAM=$(ps -p "$GW_PID" -o rss= 2>/dev/null | awk '{printf "%.0fMB (gateway)", $1/1024}')
  check "Lossless-Claw RAM" "ok" "${LC_RAM:-N/A}"
else
  check "Lossless-Claw" "fail" "Extension not found at ${LC_EXT}"
fi

# ─── 5. Watchdog ───
WD_PID_FILE="/tmp/openclaw-watchdog/daemon.pid"
if [ -f "$WD_PID_FILE" ]; then
  WD_PID=$(cat "$WD_PID_FILE" 2>/dev/null)
  if [ -n "$WD_PID" ] && kill -0 "$WD_PID" 2>/dev/null; then
    WD_UPTIME=$(ps -p "$WD_PID" -o etime= 2>/dev/null | tr -d ' ')
    check "Watchdog" "ok" "PID ${WD_PID}, uptime ${WD_UPTIME}"
  else
    check "Watchdog" "fail" "Stale PID file — daemon not running"
  fi
else
  WD_PROC=$(pgrep -f "watchdog-daemon.sh" | head -1)
  if [ -n "$WD_PROC" ]; then
    check "Watchdog" "ok" "PID ${WD_PROC} (no PID file)"
  else
    check "Watchdog" "fail" "Not running — start with: scripts/watchdog-daemon.sh start"
  fi
fi

# ─── 6. System Resources ───
DISK_PCT=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
if [ "$DISK_PCT" -gt 90 ]; then
  check "Disk" "fail" "${DISK_PCT}% used — critical"
elif [ "$DISK_PCT" -gt 80 ]; then
  check "Disk" "warn" "${DISK_PCT}% used"
else
  check "Disk" "ok" "${DISK_PCT}% used"
fi

LOAD=$(cat /proc/loadavg | awk '{print $1}')
check "Load" "ok" "${LOAD} (1min avg)"

# ─── Output ───
if [ "$JSON_MODE" = true ]; then
  echo "{\"checks\":${CHECKS},\"issues\":${ISSUES},\"status\":\"$([ $ISSUES -eq 0 ] && echo ok || echo degraded)\"}"
else
  echo "═══ System Health ═══"
  echo -e "$REPORT"
  echo ""
  echo "─── ${CHECKS} checks, ${ISSUES} issues ───"
  if [ $ISSUES -gt 0 ]; then
    echo "Status: ⚠️  DEGRADED"
  else
    echo "Status: ✅ HEALTHY"
  fi
fi

# ─── Telegram Alert (cron mode) ───
if [ "$QUIET" = false ] && [ "$JSON_MODE" = false ] && [ $ISSUES -gt 0 ]; then
  BOT_TOKEN=$(jq -r '.channels.telegram.botToken' "$CONFIG" 2>/dev/null)
  CHAT_ID="1736401643"
  if [ -n "$BOT_TOKEN" ] && [ "$BOT_TOKEN" != "null" ]; then
    ALERT_MSG="🏥 Health Check — ⚠️ ${ISSUES} issue(s) found
$(echo -e "$REPORT" | head -15)
Checked at $(date -u '+%H:%M UTC')"
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
      -d "chat_id=${CHAT_ID}" \
      --data-urlencode "text=${ALERT_MSG}" >/dev/null 2>&1
  fi
fi
