#!/bin/bash
# OpenClaw Real-Time Watchdog Daemon
# Replaces cron-based watchdog.sh + queue-watchdog.sh
# Run: ./watchdog-daemon.sh start|stop|status
#
# Features:
# - Real-time log tailing (instant lane error detection)
# - Gateway health check every 5s (not 10min)
# - OpenRouter provider check every 60s
# - 60s cooldown on duplicate alerts, NO cooldown for gateway-down
# - Single Telegram bot for all alerts

BOT_TOKEN="8677962428:AAEHnCJsk3g0YXfpXfvwU06-IMx8IbYIbi8"
CHAT_ID="1736401643"
CONFIG_FILE="/root/.openclaw/openclaw.json"
LCM_DB="/root/.openclaw/lcm.db"
STATE_DIR="/tmp/openclaw-watchdog"
PID_FILE="${STATE_DIR}/daemon.pid"
LOG_PATTERN="/tmp/openclaw/openclaw-*.log"
mkdir -p "$STATE_DIR"

# ─── Session Monitoring State ───
declare -A recent_runs
declare -A run_timestamps

# ─── Alert System ───
declare -A LAST_ALERT_TIME

send_alert() {
    local key="$1"
    local message="$2"
    local cooldown="${3:-60}"  # default 60s cooldown
    local no_cooldown="${4:-0}"
    
    local now=$(date +%s)
    local last=${LAST_ALERT_TIME[$key]:-0}
    local elapsed=$((now - last))
    
    # Skip if cooldown active (unless no_cooldown flag)
    if [ "$no_cooldown" = "0" ] && [ "$elapsed" -lt "$cooldown" ]; then
        return 0
    fi
    
    LAST_ALERT_TIME[$key]=$now
    
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        --data-urlencode "text=${message}" \
        -d "disable_notification=false" >/dev/null 2>&1
}

send_recovery() {
    local component="$1"
    local flag_file="${STATE_DIR}/${component}.down"
    
    if [ -f "$flag_file" ]; then
        rm -f "$flag_file"
        send_alert "recovery-${component}" "✅ WATCHDOG — ${component} recovered" 0
    fi
}

# ─── Get Active Model ───
get_active_model() {
    jq -r '.agents.defaults.model.primary // "unknown"' "$CONFIG_FILE" 2>/dev/null | sed 's|.*/||'
}

# ─── Gateway Health Check (every 5s) ───
check_gateway() {
    local gw_http
    gw_http=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time 5 \
        "http://127.0.0.1:18789/health" 2>/dev/null)
    
    if [ "$gw_http" = "200" ]; then
        send_recovery "Gateway"
        return 0
    else
        # NO COOLDOWN for gateway down — always alert
        local svc_status
        svc_status=$(systemctl is-active openclaw-gateway 2>/dev/null || echo "unknown")
        send_alert "gateway-down" \
            "🚨 WATCHDOG — Gateway Down
HTTP: ${gw_http}
Service: ${svc_status}
Systemd auto-restart should kick in." \
            300 1  # 5min cooldown, but override to always send
        
        touch "${STATE_DIR}/Gateway.down"
        return 1
    fi
}

# ─── OpenRouter Provider Check (every 60s) ───
check_provider() {
    local provider_http provider_time provider_error
    local response
    response=$(curl -s -w "\n%{http_code}\n%{time_total}" --connect-timeout 5 --max-time 15 \
        "https://openrouter.ai/api/v1/models?per_page=1" 2>&1)
    
    provider_http=$(echo "$response" | tail -2 | head -1)
    provider_time=$(echo "$response" | tail -1)
    provider_error=$(echo "$response" | head -1 | head -c 80)
    
    local active_model
    active_model=$(get_active_model)
    
    if [ "$provider_http" = "200" ]; then
        send_recovery "OpenRouter"
        return 0
    else
        send_alert "openrouter-down" \
            "⚠️ WATCHDOG — OpenRouter Issue
HTTP ${provider_http} in ${provider_time}s
Error: ${provider_error}
Active model: ${active_model}
LLM requests may fail."
        
        touch "${STATE_DIR}/OpenRouter.down"
        return 1
    fi
}

# ─── Log Line Processor ───
process_log_line() {
    local line="$1"
    
    # Skip empty lines
    [ -z "$line" ] && return
    
    local active_model
    active_model=$(get_active_model)
    
    # Lane task error
    if echo "$line" | grep -q "lane task error"; then
        local error_reason error_duration
        # Try both JSON format ("error":"...") and key=value format (error="...")
        error_reason=$(echo "$line" | grep -oP '"error":"[^"]*"' | head -1 | sed 's/"error":"//;s/"//')
        if [ -z "$error_reason" ]; then
            error_reason=$(echo "$line" | grep -oP 'error="[^"]*"' | head -1 | sed 's/error="//;s/"//')
        fi
        error_duration=$(echo "$line" | grep -oP 'durationMs=[0-9]*' | head -1 | sed 's/durationMs=//')
        
        send_alert "lane-error" \
            "🚨 WATCHDOG — Lane Task Error
Error: ${error_reason}
Duration: ${error_duration}ms
Model: ${active_model}
LLM request failed."
        return
    fi
    
    # Lane wait exceeded
    if echo "$line" | grep -q "lane wait exceeded"; then
        local waited_ms queue_ahead
        waited_ms=$(echo "$line" | grep -oP 'waitedMs=\K\d+')
        queue_ahead=$(echo "$line" | grep -oP 'queueAhead=\K\d+')
        local waited_sec=$((waited_ms / 1000))
        
        send_alert "lane-stuck" \
            "⚠️ WATCHDOG — Lane Stuck
Waited: ${waited_sec}s (threshold exceeded)
Queue ahead: ${queue_ahead:-0}
Model: ${active_model}
Request may be hanging."
        return
    fi
    
    # Embedded run timeout
    if echo "$line" | grep -q "embedded run timeout"; then
        local timeout_runId
        timeout_runId=$(echo "$line" | grep -oP 'runId=\K[^, ]+' | head -1)
        if [ -n "$timeout_runId" ]; then
            recent_runs[$timeout_runId]=$(( ${recent_runs[$timeout_runId]:-0} + 1 ))
            if [ -z "${run_timestamps[$timeout_runId]:-}" ]; then
                run_timestamps[$timeout_runId]=$(date +%s)
            fi
        fi
        send_alert "embedded-timeout" \
            "🔥 WATCHDOG — Embedded Run Timeout
Sub-agent may be blocking the main lane.
RunId: ${timeout_runId:0:8}...
Model: ${active_model}"
        return
    fi

    # Embedded run agent end — provider errors (400, 500, etc.)
    if echo "$line" | grep -q "embedded run agent end" && echo "$line" | grep -q "isError=true"; then
        local error_detail failed_model failed_provider runId
        error_detail=$(echo "$line" | grep -oP 'error=\K[^,]+' | head -1 | head -c 80)
        failed_model=$(echo "$line" | grep -oP 'model=\K[^,]+' | head -1 | sed 's|.*/||')
        failed_provider=$(echo "$line" | grep -oP 'provider=\K[^, ]+' | head -1)
        runId=$(echo "$line" | grep -oP 'runId=\K[^, ]+' | head -1)

        # Track for loop detection
        if [ -n "$runId" ]; then
            recent_runs[$runId]=$(( ${recent_runs[$runId]:-0} + 1 ))
            if [ -z "${run_timestamps[$runId]:-}" ]; then
                run_timestamps[$runId]=$(date +%s)
            fi
        fi

        # 400 errors — bad request, model rejection
        if echo "$error_detail" | grep -q "^400"; then
            send_alert "provider-400" \
                "🚨 WATCHDOG — Provider 400 (Bad Request)
Error: ${error_detail}
Model: ${failed_model:-unknown}
Provider: ${failed_provider:-unknown}
Request rejected — possible token limit or invalid payload." 120
            return
        fi

        # Generic provider errors
        send_alert "provider-error" \
            "⚠️ WATCHDOG — Provider Error
Error: ${error_detail}
Model: ${failed_model:-unknown}
Provider: ${failed_provider:-unknown}" 120
        return
    fi
    
    # NOTE: Successful completions (isError=false) handled by check_new_subagents
    # via runs.json + stored state — not here. Log tailer only handles errors.

    # LCM empty normalized summary
    if echo "$line" | grep -q "empty normalized summary"; then
        local failed_model
        failed_model=$(echo "$line" | grep -oP 'model=\K[^;, ]+' | head -1 | sed 's|.*/||;s|:.*||')
        
        send_alert "lcm-empty-summary" \
            "🔄 LCM — Empty Summary
Model: \`${failed_model:-unknown}\` returning thinking-only blocks.
Summary model may need changing." 300
        return
    fi
    
    # All extraction attempts exhausted
    if echo "$line" | grep -q "all extraction attempts exhausted"; then
        local failed_model rate_limited
        failed_model=$(echo "$line" | grep -oP 'model=\K[^;, ]+' | head -1 | sed 's|.*/||;s|:.*||')
        rate_limited=$(echo "$line" | grep -c "429\|rate.limit")
        
        if [ "$rate_limited" -gt 0 ]; then
            send_alert "lcm-exhausted" \
                "🔥 LCM — Rate Limited
Model: \`${failed_model:-unknown}\` — 429 errors.
All extraction attempts exhausted.
Free tier limits hit." 300
        else
            send_alert "lcm-exhausted" \
                "🔥 LCM — Extraction Failed
Model: \`${failed_model:-unknown}\` — all attempts exhausted." 300
        fi
        return
    fi
}

# ─── LCM Summary Success Alert ───
LAST_SUMMARY_FILE="${STATE_DIR}/last-summary-seen"

check_new_summaries() {
    [ ! -f "$LCM_DB" ] && return
    
    local latest_id last_seen
    latest_id=$(sqlite3 "$LCM_DB" "SELECT summary_id FROM summaries ORDER BY created_at DESC LIMIT 1" 2>/dev/null)
    last_seen=$(cat "$LAST_SUMMARY_FILE" 2>/dev/null || echo "")
    
    [ -z "$latest_id" ] && return
    [ "$latest_id" = "$last_seen" ] && return
    
    # New summary found — get details
    local kind token_count created_at preview lcm_model
    kind=$(sqlite3 "$LCM_DB" "SELECT kind FROM summaries WHERE summary_id='$latest_id'" 2>/dev/null)
    token_count=$(sqlite3 "$LCM_DB" "SELECT token_count FROM summaries WHERE summary_id='$latest_id'" 2>/dev/null)
    created_at=$(sqlite3 "$LCM_DB" "SELECT created_at FROM summaries WHERE summary_id='$latest_id'" 2>/dev/null)
    preview=$(sqlite3 "$LCM_DB" "SELECT substr(replace(substr(content, 1, 300), char(10), ' '), 1, 300) FROM summaries WHERE summary_id='$latest_id'" 2>/dev/null)
    lcm_model=$(jq -r '.plugins.entries["lossless-claw"].config.summaryModel // "unknown"' "$CONFIG_FILE" 2>/dev/null | sed 's|.*/||;s|:.*||')
    
    # Skip tiny summaries — these are usually the model choking on binary/base64 dumps
    if [ "$token_count" -lt 50 ] 2>/dev/null; then
        echo "$latest_id" > "$LAST_SUMMARY_FILE"
        return
    fi

    # Classify quality
    local icon="✅"
    if echo "$preview" | grep -qi "^we need to summarize\|^we need to produce"; then
        icon="❌"
    elif echo "$preview" | grep -q "LCM fallback summary"; then
        icon="⚠️"
    elif [ "$token_count" -lt 50 ] 2>/dev/null; then
        icon="⚠️"
    fi

    # Trim preview to ~150 chars
    preview=$(echo "$preview" | cut -c1-150)

    send_alert "lcm-success" \
        "📝 LCM Summary ${icon}
${kind} • ${token_count} tok

${preview}" \
        60  # 60s cooldown between summary alerts
    
    echo "$latest_id" > "$LAST_SUMMARY_FILE"
}

# ─── Sub-Agent Spawn/Completion Monitoring ───
RUNS_STATE_FILE="${STATE_DIR}/last-runs-state.json"
# Initialize runs state file on first load
[ ! -f "$RUNS_STATE_FILE" ] && echo '{}' > "$RUNS_STATE_FILE"

check_new_subagents() {
    local runs_file="/root/.openclaw/subagents/runs.json"
    [ ! -f "$runs_file" ] && return

    # Parse current active runs (exclude empty version-only object)
    local current_runs
    current_runs=$(jq -r '.runs // {} | to_entries[] | "\(.key)|\(.value.childSessionKey // "unknown")|\(.value.model // "unknown")|\(.value.task // "unknown")|\(.value.startedAt // "unknown")|\(.value.requesterSessionKey // "unknown")"' "$runs_file" 2>/dev/null)

    # Parse last known runs
    local last_runs
    last_runs=$(jq -r 'to_entries[] | "\(.key)"' "$RUNS_STATE_FILE" 2>/dev/null)

    # Build sets for comparison
    declare -A current_set last_set current_details
    declare -a completed_fast=()

    if [ -n "$current_runs" ]; then
        while IFS='|' read -r run_id child_key model task started_at requester_key; do
            # Extract agent name from childSessionKey (agent:<name>:subagent:<uuid>)
            local agent="$(echo "$child_key" | cut -d: -f2)"
            # Extract parent agent from requesterSessionKey (agent:<name>:telegram:...)
            local parent="$(echo "$requester_key" | cut -d: -f2)"
            current_set["$run_id"]=1
            current_details["$run_id"]="${agent}|${model}|${task}|${started_at}|${parent}"
        done <<< "$current_runs"
    fi

    if [ -n "$last_runs" ]; then
        while IFS= read -r run_id; do
            [ -n "$run_id" ] && last_set["$run_id"]=1
        done <<< "$last_runs"
    fi

    # Detect NEW runs (in current but not in last)
    for run_id in "${!current_set[@]}"; do
        if [ -z "${last_set[$run_id]:-}" ]; then
            # New spawn detected!
            IFS='|' read -r agent model task started_at parent <<< "${current_details[$run_id]}"

            local alert_time
            alert_time=$(TZ='America/Denver' date '+%Y-%m-%d %H:%M:%S MT')

            # Truncate task preview
            local task_preview="${task:0:200}"
            [ ${#task} -gt 200 ] && task_preview="${task_preview}..."

            send_alert "subagent-spawn-${run_id}" \
                "🚀 Sub-Agent Spawned
RunId: \`${run_id:0:8}\`...
Agent: ${parent}
Model: ${model}
Time: ${alert_time}

Task: ${task_preview}" \
                0  # no cooldown — every spawn is unique

            # Check if already completed (fast sub-agent: spawned + ended between polls)
            local is_ended
            is_ended=$(jq -r --arg id "$run_id" '.runs[$id].endedAt // empty' "$runs_file" 2>/dev/null)
            if [ -n "$is_ended" ]; then
                local complete_time
                complete_time=$(TZ='America/Denver' date '+%Y-%m-%d %H:%M:%S MT')

                send_alert "subagent-done-${run_id}" \
"✅ Sub-Agent Completed
RunId: \`${run_id:0:8}\`...
Agent: ${parent}
Model: ${model}
Time: ${complete_time}

Task: ${task_preview}" \
                    0

                # Mark as completed-fast so we skip it during state persistence
                # (prevents duplicate completion alert when run disappears from runs.json)
                completed_fast+=("$run_id")
            fi
        fi
    done

    # Detect COMPLETED runs — check known runs in state for endedAt
    for run_id in "${!last_set[@]}"; do
        # Skip if we already handled this as a fast-completion in this call
        local skip=0
        for fc in "${completed_fast[@]:-}"; do
            [ "$fc" = "$run_id" ] && skip=1 && break
        done
        [ "$skip" -eq 1 ] && continue

        # Skip if already marked completed in state file (from previous call)
        local already_done
        already_done=$(jq -r --arg id "$run_id" '.[$id].completed // false' "$RUNS_STATE_FILE" 2>/dev/null)
        [ "$already_done" = "true" ] && continue

        # Check if run now has endedAt in runs.json
        local ended_at
        ended_at=$(jq -r --arg id "$run_id" '.runs[$id].endedAt // empty' "$runs_file" 2>/dev/null)
        if [ -n "$ended_at" ]; then
            # Run completed — look up stored details from state file
            local stored_agent stored_model stored_task
            stored_agent=$(jq -r --arg id "$run_id" '.[$id].agent // "unknown"' "$RUNS_STATE_FILE" 2>/dev/null)
            stored_model=$(jq -r --arg id "$run_id" '.[$id].model // "unknown"' "$RUNS_STATE_FILE" 2>/dev/null)
            stored_task=$(jq -r --arg id "$run_id" '.[$id].task // "unknown"' "$RUNS_STATE_FILE" 2>/dev/null)

            local complete_time
            complete_time=$(TZ='America/Denver' date '+%Y-%m-%d %H:%M:%S MT')

            # Truncate task preview
            local task_preview="${stored_task:0:200}"
            [ ${#stored_task} -gt 200 ] && task_preview="${task_preview}..."

            send_alert "subagent-done-${run_id}" \
                "✅ Sub-Agent Completed
RunId: \`${run_id:0:8}\`...
Agent: ${stored_agent}
Model: ${stored_model}
Time: ${complete_time}

Task: ${task_preview}" \
                0

            # Mark as completed so we exclude it from state persistence
            completed_fast+=("$run_id")
        fi
    done

    # Persist current state for next comparison (full details for completion alerts)
    # NOTE: completed_fast runs ARE written to state to prevent re-detection on
    # subsequent inotifywait events (OpenClaw keeps completed runs in runs.json
    # until archival, so without persistence they'd be re-detected as "new").
    # The completed_fast mechanism only prevents the "Detect COMPLETED" loop from
    # sending a duplicate completion alert — it does NOT skip state persistence.
    if [ -n "$current_runs" ]; then
        local json_state="{"
        local first=1
        while IFS='|' read -r run_id child_key model task started_at requester_key; do

            local parent="$(echo "$requester_key" | cut -d: -f2)"
            # Escape task for JSON (handle quotes and special chars)
            local escaped_task
            escaped_task=$(printf '%s' "$task" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | head -c 300)
            
            # Check if this run was already completed (from previous state or current fast-completion)
            local done_flag="false"
            # Check previous state first — don't downgrade completed→not-completed
            local prev_done
            prev_done=$(jq -r --arg id "$run_id" '.[$id].completed // false' "$RUNS_STATE_FILE" 2>/dev/null)
            if [ "$prev_done" = "true" ]; then
                done_flag="true"
            else
                # Check if this run just completed fast in this call
                for fc in "${completed_fast[@]:-}"; do
                    [ "$fc" = "$run_id" ] && done_flag="true" && break
                done
            fi
            
            [ "$first" -eq 0 ] && json_state+=","
            json_state+="\"${run_id}\":{\"agent\":\"${parent}\",\"model\":\"${model}\",\"task\":\"${escaped_task}\",\"startedAt\":\"${started_at}\",\"completed\":${done_flag}}"
            first=0
        done <<< "$current_runs"
        json_state+="}"
        echo "$json_state" > "$RUNS_STATE_FILE"
    else
        echo '{}' > "$RUNS_STATE_FILE"
    fi

    # Cleanup associative arrays
    unset current_set last_set current_details completed_fast
}

# ─── Session Monitoring ───

# Monitor active embedded runs for hangs/loops
monitor_sessions() {
    local current_time=$(date +%s)
    local loop_window=$((2 * 60))      # 2 minutes for loop detection
    local max_consecutive_errors=3
    
    # Clean old entries (older than loop_window)
    local cutoff=$((current_time - loop_window))
    for runId in "${!run_timestamps[@]}"; do
        if [[ ${run_timestamps[$runId]} -lt $cutoff ]]; then
            unset "recent_runs[$runId]"
            unset "run_timestamps[$runId]"
        fi
    done
    
    # Check for loops: same runId failing repeatedly in short time
    for runId in "${!recent_runs[@]}"; do
        local count=${recent_runs[$runId]}
        if [[ $count -ge $max_consecutive_errors ]]; then
            local first_seen=${run_timestamps[$runId]:-0}
            local age=$((current_time - first_seen))
            if [[ $age -le $loop_window ]]; then
                send_alert "subagent-loop" \
                    "🔄 Subagent Loop Detected
RunId: \`${runId:0:8}\`...
Failures: $count in $age seconds
Possible infinite retry loop." 120
            fi
        fi
    done
}

# ─── Background Processes ───

# Runs.json watcher — event-driven subagent detection (with 10s safety poll)
start_runs_watcher() {
    local runs_dir="/root/.openclaw/subagents"
    local runs_file="runs.json"
    
    [ ! -d "$runs_dir" ] && return 1
    
    (
        while true; do
            # Wait for modify events on runs.json, timeout after 10s as safety net
            if inotifywait -q -t 10 -e modify,close_write "${runs_dir}/${runs_file}" 2>/dev/null; then
                # File changed — small delay to let writes settle
                sleep 0.5
                check_new_subagents
            else
                # Timeout (no event in 10s) — poll anyway as safety net
                # Catches completions that happened during check_new_subagents execution
                check_new_subagents
            fi
        done
    ) &
    echo $! > "${STATE_DIR}/runs-watcher.pid"
    echo "[$(date -u +"%H:%M:%S")] Runs watcher started (PID $!)"
}

# Log tailer — detects issues in real-time
start_log_tailer() {
    local log_files
    # Tail last 2 files by mtime (handles day boundary overlap)
    log_files=$(ls -t /tmp/openclaw/openclaw-*.log 2>/dev/null | head -2)

    if [ -z "$log_files" ]; then
        echo "[$(date -u +"%H:%M:%S")] No log file found, will retry..."
        return 1
    fi

    echo "[$(date -u +"%H:%M:%S")] Tailing: $(echo "$log_files" | tr '\n' ' ')"
    
    # tail -F follows files by name even if rotated
    tail -F $log_files 2>/dev/null | while IFS= read -r line; do
        # Only process lines with relevant keywords (fast filter)
        case "$line" in
            *"lane task error"*|*"lane wait exceeded"*|*"embedded run timeout"*|\
            *"embedded run agent end"*|*"empty normalized summary"*|*"all extraction attempts exhausted"*|\
            *"Provider returned error"*|*"model fallback decision"*)
                process_log_line "$line"
                ;;
        esac
    done &
    echo $! > "${STATE_DIR}/tailer.pid"
}

# Health check loop (gateway + provider, runs every 5s)
start_health_checker() {
    local provider_counter=0
    
    while true; do
        check_gateway
        
        # Provider check every 60s (every 12th cycle)
        provider_counter=$((provider_counter + 1))
        if [ "$provider_counter" -ge 12 ]; then
            check_provider
            provider_counter=0
        fi
        
        sleep 5
    done &
    echo $! > "${STATE_DIR}/health-checker.pid"
}

# ─── Daemon Control ───
start() {
    if [ -f "$PID_FILE" ]; then
        local existing_pid
        existing_pid=$(cat "$PID_FILE")
        if kill -0 "$existing_pid" 2>/dev/null; then
            echo "Watchdog already running (PID $existing_pid)"
            return 1
        fi
        rm -f "$PID_FILE"
    fi

    # Clean up any orphaned processes from previous runs
    # Use $$ to exclude current process, and look for detached daemon children
    local my_pid=$$
    local orphans
    # Find orphaned watchdog bash processes (children of init, not our direct children)
    orphans=$(ps -eo pid,ppid,cmd | grep -E "watchdog-daemon\.sh" | grep -v grep | grep -v " $$ " | awk '$2 == 1 || $2 == 2 {print $1}')
    # Also find tail processes watching our log files
    local tail_orphans
    tail_orphans=$(ps aux | grep "tail -F /tmp/openclaw/openclaw-" | grep -v grep | awk '{print $2}')
    orphans="$orphans $tail_orphans"
    # Trim
    orphans=$(echo $orphans | xargs)
    if [ -n "$orphans" ]; then
        echo "Cleaning up orphaned processes: $orphans"
        kill $orphans 2>/dev/null
        sleep 1
        # Force kill stragglers
        local remaining
        remaining=$(echo $orphans | xargs -n1 | while read p; do kill -0 $p 2>/dev/null && echo $p; done)
        [ -n "$remaining" ] && kill -9 $remaining 2>/dev/null
    fi
    
    echo "Starting OpenClaw Watchdog Daemon..."
    
    # Main daemon loop — uses setsid to fully detach from parent
    (
        # Trap cleanup
        cleanup() {
            [ -f "${STATE_DIR}/tailer.pid" ] && kill $(cat "${STATE_DIR}/tailer.pid") 2>/dev/null
            [ -f "${STATE_DIR}/health-checker.pid" ] && kill $(cat "${STATE_DIR}/health-checker.pid") 2>/dev/null
            [ -f "${STATE_DIR}/runs-watcher.pid" ] && kill $(cat "${STATE_DIR}/runs-watcher.pid") 2>/dev/null
            rm -f "$PID_FILE" "${STATE_DIR}/tailer.pid" "${STATE_DIR}/health-checker.pid" "${STATE_DIR}/runs-watcher.pid"
            exit 0
        }
        trap cleanup EXIT INT TERM

        # Start sub-processes
        start_log_tailer
        start_health_checker
        start_runs_watcher

        # Monitor loop — restart tailer if log file changes
        while true; do
            sleep 30

            # Check if tailer is alive
            if [ -f "${STATE_DIR}/tailer.pid" ]; then
                local tailer_pid
                tailer_pid=$(cat "${STATE_DIR}/tailer.pid")
                if ! kill -0 "$tailer_pid" 2>/dev/null; then
                    echo "[$(date -u +"%H:%M:%S")] Tailer died, restarting..."
                    start_log_tailer
                fi
            fi

            # Check if health checker is alive
            if [ -f "${STATE_DIR}/health-checker.pid" ]; then
                local checker_pid
                checker_pid=$(cat "${STATE_DIR}/health-checker.pid")
                if ! kill -0 "$checker_pid" 2>/dev/null; then
                    echo "[$(date -u +"%H:%M:%S")] Health checker died, restarting..."
                    start_health_checker
                fi
            fi

            # Check for subagent loops
            monitor_sessions

            # Check for new LCM summaries
            check_new_summaries
        done
    ) &

    local daemon_pid=$!
    echo $daemon_pid > "$PID_FILE"
    echo "Watchdog daemon started (PID $daemon_pid)"
}

stop() {
    local stopped=0

    # Kill tail processes (these are children of the tailer pipeline)
    local tail_pids
    tail_pids=$(ps aux | grep "tail -F /tmp/openclaw/openclaw-" | grep -v grep | awk '{print $2}')
    if [ -n "$tail_pids" ]; then
        echo "Stopping tailer(s): $tail_pids"
        kill $tail_pids 2>/dev/null
        stopped=1
    fi

    # Kill health checker and session monitor (background loops)
    local checker_pids
    checker_pids=$(ps aux | grep -E "watchdog-daemon\.sh.*(start|health)" | grep -v grep | awk '{print $2}')
    if [ -n "$checker_pids" ]; then
        echo "Stopping checker/monitor: $checker_pids"
        kill $checker_pids 2>/dev/null
        stopped=1
    fi

    # Kill inotifywait runs watcher
    if [ -f "${STATE_DIR}/runs-watcher.pid" ]; then
        local watcher_pid
        watcher_pid=$(cat "${STATE_DIR}/runs-watcher.pid")
        if kill -0 "$watcher_pid" 2>/dev/null; then
            echo "Stopping runs watcher (PID $watcher_pid)..."
            kill "$watcher_pid" 2>/dev/null
        fi
        # Also kill any inotifywait children
        local inotify_pids
        inotify_pids=$(pgrep -f "inotifywait.*runs.json" 2>/dev/null)
        [ -n "$inotify_pids" ] && kill $inotify_pids 2>/dev/null
    fi

    # Kill the main daemon process
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping watchdog daemon (PID $pid)..."
            kill "$pid" 2>/dev/null
            stopped=1
        fi
        rm -f "$PID_FILE"
    fi

    # Clean up state files
    rm -f "${STATE_DIR}/tailer.pid" "${STATE_DIR}/health-checker.pid" "${STATE_DIR}/runs-watcher.pid" 2>/dev/null

    if [ "$stopped" -eq 0 ]; then
        echo "Watchdog not running."
    else
        sleep 1
        # Force kill anything still alive
        local remaining
        remaining=$(ps aux | grep -E "watchdog-daemon\.sh|tail -F /tmp/openclaw/openclaw-" | grep -v grep | awk '{print $2}')
        if [ -n "$remaining" ]; then
            echo "Force killing remaining: $remaining"
            kill -9 $remaining 2>/dev/null
        fi
        echo "Stopped."
    fi
}

status() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "✅ Watchdog running (PID $pid)"
            echo "   Log tailer: $(cat "${STATE_DIR}/tailer.pid" 2>/dev/null || echo "N/A")"
            echo "   Health checker: $(cat "${STATE_DIR}/health-checker.pid" 2>/dev/null || echo "N/A")"
            echo "   State dir: $STATE_DIR"
        else
            echo "❌ PID file exists but process dead. Run: $0 start"
        fi
    else
        echo "❌ Watchdog not running. Run: $0 start"
    fi
}

# ─── Entry Point ───
case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    restart)
        # Use nohup to avoid hanging when called from exec/pipes
        nohup bash -c "$0 stop; sleep 1; $0 start" > /tmp/watchdog-restart.log 2>&1 &
        disown $! 2>/dev/null
        echo "Restarting in background (check: $0 status)"
        ;;
    status) status ;;
    *)      echo "Usage: $0 start|stop|restart|status"; exit 1 ;;
esac
