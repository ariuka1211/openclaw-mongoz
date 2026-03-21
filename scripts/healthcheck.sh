#!/bin/bash
# System Health Dashboard — validates system health
# Checks: gateway, watchdog, system resources
# Usage: ./healthcheck.sh [--json] [--quiet]

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

# ─── 2. Extensions Check ───
# Check if memory-core plugin is loaded (check config + extension files)
if [ -d "/root/.openclaw/extensions/memory-core" ] || grep -q '"memory-core"' "$CONFIG" 2>/dev/null; then
  check "Memory-Core Plugin" "ok" "Configured"
else
  check "Memory-Core Plugin" "warn" "Not configured or not installed"
fi

# ─── 3. Watchdog ───
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

# ─── 4. System Resources ───
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
