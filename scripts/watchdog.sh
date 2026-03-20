#!/bin/bash
# OpenClaw System Watchdog v3
# Monitors: Gateway, Mission Control, LCM, QMD
# Alerts via Telegram on failures (one-shot per outage)
# Run via cron every 2 minutes

CONFIG_FILE="/root/.openclaw/openclaw.json"
BOT_TOKEN=$(jq -r '.channels.telegram.botToken' "$CONFIG_FILE")
CHAT_ID="1736401643"
STATE_DIR="/tmp/openclaw-watchdog"
mkdir -p "$STATE_DIR"

send_alert() {
    local component="$1"
    local status="$2"
    local detail="$3"
    local flag_file="${STATE_DIR}/${component}.alerted"

    if [ ! -f "$flag_file" ]; then
        local timestamp=$(date -u +"%Y-%m-%d %H:%M UTC")
        local msg="🐕 WATCHDOG — ${component} ${status}
${detail}
⏰ ${timestamp}"

        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            --data-urlencode "text=${msg}" >/dev/null 2>&1

        touch "$flag_file"
    fi
}

send_recovery() {
    local component="$1"
    local flag_file="${STATE_DIR}/${component}.alerted"

    if [ -f "$flag_file" ]; then
        rm -f "$flag_file"
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            --data-urlencode "text=🐕 WATCHDOG — ✅ ${component} recovered" >/dev/null 2>&1
    fi
}

# ─── 1. Gateway Health ───
GW_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time 5 "http://127.0.0.1:18789/health" 2>/dev/null)

if [ "$GW_HTTP" = "200" ]; then
    send_recovery "Gateway"
    GW_OK=1

    # Track gateway uptime start (for LCM grace period)
    GW_SINCE_FILE="${STATE_DIR}/gw-up-since"
    if [ ! -f "$GW_SINCE_FILE" ]; then
        date +%s > "$GW_SINCE_FILE"
    fi
    GW_UP_SINCE=$(cat "$GW_SINCE_FILE" 2>/dev/null || echo 0)
    GW_UPTIME=$(( $(date +%s) - GW_UP_SINCE ))
else
    # Gateway down — clear uptime tracker so grace period resets
    rm -f "${STATE_DIR}/gw-up-since"

    SVC_STATUS=$(systemctl is-active openclaw-gateway 2>/dev/null || echo "unknown")
    send_alert "Gateway" "Down" "HTTP: ${GW_HTTP}
Service: ${SVC_STATUS}
Systemd auto-restart should kick in (Restart=always)."
    GW_OK=0
    GW_UPTIME=0
fi

# ─── 2. Mission Control (Next.js on port 3000) ───
if pgrep -f "next-server" >/dev/null 2>&1; then
    send_recovery "Mission Control"
else
    if [ "$GW_OK" = "1" ]; then
        send_alert "Mission Control" "Down" "next-server process not found on port 3000."
    fi
fi

# ─── 3. LCM (check db file is being written to) ───
LCM_DB="/root/.openclaw/lcm.db"
LCM_GRACE_SECONDS=5400  # 90 minutes after gateway restart — LCM needs context to fill

if [ -f "$LCM_DB" ]; then
    LCM_AGE=$(( $(date +%s) - $(stat -c %Y "$LCM_DB" 2>/dev/null || echo 0) ))
    if [ "$LCM_AGE" -lt 1800 ]; then
        send_recovery "LCM"
    else
        if [ "$GW_OK" = "1" ]; then
            # Only alert if gateway has been up long enough for LCM to have fired
            if [ "$GW_UPTIME" -lt "$LCM_GRACE_SECONDS" ]; then
                # Still in grace period — skip alert
                : # LCM stale but within grace period, suppress false positive
            else
                send_alert "LCM" "Stale" "LCM db not written to in ${LCM_AGE}s (~$((LCM_AGE/60)) min).
Gateway up for $((GW_UPTIME/60)) min (grace period: $((LCM_GRACE_SECONDS/60)) min).
LCM should have fired by now — check if summarization is stuck."
            fi
        fi
    fi
else
    if [ "$GW_OK" = "1" ]; then
        send_alert "LCM" "Missing" "LCM database file not found at ${LCM_DB}."
    fi
fi

# ─── 4. Provider Health (OpenRouter) ───
PROVIDER_START=$(date +%s%N)
PROVIDER_RESP=$(curl -s -w "\n%{http_code}\n%{time_total}" --connect-timeout 5 --max-time 15 "https://openrouter.ai/api/v1/models?per_page=1" 2>&1)
PROVIDER_HTTP=$(echo "$PROVIDER_RESP" | tail -2 | head -1)
PROVIDER_TIME=$(echo "$PROVIDER_RESP" | tail -1)

if [ "$PROVIDER_HTTP" = "200" ]; then
    send_recovery "OpenRouter"
    PROVIDER_OK=1
else
    PROVIDER_OK=0
    PROVIDER_ERROR=$(echo "$PROVIDER_RESP" | head -1 | head -c 200)
    send_alert "OpenRouter" "⚠️ Provider Issue" "HTTP ${PROVIDER_HTTP} in ${PROVIDER_TIME}s
Error: ${PROVIDER_ERROR}
Active model: ${ACTIVE_MODEL}
LLM requests may fail or timeout."
fi

# ─── 5. Lane Error Monitor (recent provider failures from logs) ───
LANE_LOG="/root/.openclaw/logs/gateway.log"
LANE_STATE="${STATE_DIR}/last-lane-check"
LAST_LANE_CHECK=$(cat "$LANE_STATE" 2>/dev/null || echo 0)
NOW=$(date +%s)

if [ -f "$LANE_LOG" ] && [ "$GW_OK" = "1" ]; then
    # Look for lane task errors in last 5 minutes
    RECENT_ERRORS=$(find "$LANE_LOG" -mmin -5 -exec grep -c "lane task error" {} \; 2>/dev/null || echo 0)
    if [ "$RECENT_ERRORS" -gt 0 ] 2>/dev/null; then
        # Extract the last error with reason
        LAST_ERROR=$(grep "lane task error" "$LANE_LOG" 2>/dev/null | tail -1)
        ERROR_REASON=$(echo "$LAST_ERROR" | grep -o '"error":"[^"]*"' | head -1 | sed 's/"error":"//;s/"//')
        ERROR_LANE=$(echo "$LAST_ERROR" | grep -o 'lane=[^ ]*' | head -1)
        ERROR_DURATION=$(echo "$LAST_ERROR" | grep -o 'durationMs=[0-9]*' | head -1 | sed 's/durationMs=//')

        # Only alert if we haven't already alerted for this specific error pattern
        ERROR_SIG=$(echo "$ERROR_REASON" | md5sum | cut -c1-8)
        ERROR_FLAG="${STATE_DIR}/lane-error-${ERROR_SIG}.alerted"
        if [ ! -f "$ERROR_FLAG" ]; then
            send_alert "Lane Errors" "⚠️ ${RECENT_ERRORS} error(s) in 5min" "Last: ${ERROR_REASON}
${ERROR_LANE} after ${ERROR_DURATION}ms
Active model: ${ACTIVE_MODEL}
Provider may be struggling — LLM requests timing out."
            touch "$ERROR_FLAG"
            # Auto-clear lane error flags after 30 min
            find "$STATE_DIR" -name "lane-error-*.alerted" -mmin +30 -delete 2>/dev/null
        fi
    else
        # No recent errors — clear old lane error flags
        find "$STATE_DIR" -name "lane-error-*.alerted" -mmin +5 -delete 2>/dev/null
    fi
fi
echo "$NOW" > "$LANE_STATE"

# ─── 6. Model & Request Status ───
# Read active model from config
ACTIVE_MODEL=$(jq -r '.agents.defaults.model // "unknown"' "$CONFIG_FILE" 2>/dev/null | sed 's|.*/||')
FALLBACK_MODEL=$(jq -r '.agents.defaults.fallbackModels[0] // "none"' "$CONFIG_FILE" 2>/dev/null | sed 's|.*/||')
LCM_SUMMARY_MODEL=$(jq -r '.plugins.entries["lossless-claw"].config.summaryModel // "unknown"' "$CONFIG_FILE" 2>/dev/null | sed 's|.*/||;s|:.*||')

# Track last successful request by looking at gateway log activity
# (If lane task completed without error in last 10 min, requests are flowing)
LAST_LANE_ERROR_TIME=""
if [ -f "$LANE_LOG" ]; then
    LAST_LANE_SUCCESS=$(grep "lane task completed\|agent run complete\|embedded run complete" "$LANE_LOG" 2>/dev/null | tail -1 | grep -o 'at=[^ ]*' | head -1 || echo "")
    LAST_LANE_ERROR_TIME=$(grep "lane task error" "$LANE_LOG" 2>/dev/null | tail -1 | grep -o 'at=[^ ]*' | head -1 || echo "")
fi

# Build model status line (included in alerts for context)
MODEL_STATUS="Model: ${ACTIVE_MODEL} (fallback: ${FALLBACK_MODEL}, LCM: ${LCM_SUMMARY_MODEL})"

# ─── 7. LCM Summary Quality Preview + Classification ───
LAST_SEEN_FILE="${STATE_DIR}/last-summary-seen"
if [ -f "$LCM_DB" ] && [ "$GW_OK" = "1" ]; then
    LATEST_ID=$(sqlite3 "$LCM_DB" "SELECT summary_id FROM summaries ORDER BY created_at DESC LIMIT 1" 2>/dev/null)
    LAST_SEEN=$(cat "$LAST_SEEN_FILE" 2>/dev/null || echo "")
    if [ -n "$LATEST_ID" ] && [ "$LATEST_ID" != "$LAST_SEEN" ]; then
        PREVIEW=$(sqlite3 "$LCM_DB" "SELECT substr(replace(substr(content, 1, 400), char(10), ' '), 1, 400) FROM summaries WHERE summary_id = '$LATEST_ID'" 2>/dev/null)
        TOKENS=$(sqlite3 "$LCM_DB" "SELECT token_count FROM summaries WHERE summary_id = '$LATEST_ID'" 2>/dev/null)
        CREATED=$(sqlite3 "$LCM_DB" "SELECT created_at FROM summaries WHERE summary_id = '$LATEST_ID'" 2>/dev/null)

        # Classify quality
        QUALITY_LABEL="✅ Real"
        if echo "$PREVIEW" | grep -q "^We need to summarize\|^We need to produce"; then
            QUALITY_LABEL="❌ Prompt Echo"
        elif echo "$PREVIEW" | grep -q "LCM fallback summary"; then
            QUALITY_LABEL="⚠️ Fallback Truncation"
        elif echo "$PREVIEW" | grep -q "^<!DOCTYPE"; then
            QUALITY_LABEL="⚠️ Raw HTML Fallback"
        elif [ "$TOKENS" -lt 50 ] 2>/dev/null; then
            QUALITY_LABEL="⚠️ Too Short"
        fi

        # Get model name from config (logs unreliable for successful summaries)
        LCM_MODEL=$(jq -r '.plugins.entries["lossless-claw"].config.summaryModel // "unknown"' "$CONFIG_FILE" 2>/dev/null | sed 's|.*/||;s|:.*||')

        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            --data-urlencode "text=📝 LCM New Summary ${QUALITY_LABEL} (${TOKENS} tok, ${CREATED})
ID: ${LATEST_ID}
Model: ${LCM_MODEL}

${PREVIEW}..." >/dev/null 2>&1
        echo "$LATEST_ID" > "$LAST_SEEN_FILE"
    fi
fi

exit 0
