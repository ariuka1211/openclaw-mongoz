#!/usr/bin/env bash
# Session size watchdog — alerts agent-specific Telegram groups when sessions grow large.
# First alert at 500KB, then every +200KB (700, 900, 1100...)
# Designed to run via OpenClaw cron (isolated agentTurn).

set -euo pipefail

FIRST_ALERT_KB=500
STEP_KB=200

# Agent → (bot_token, telegram_group_id)
declare -A BOT_TOKENS=(
  [main]="8677962428:AAEHnCJsk3g0YXfpXfvwU06-IMx8IbYIbi8"
  [blitz]="8637788378:AAGqx03IsxUzvO58jJ_fRcYRguirYfvZd0U"
  [system]="8750427370:AAEv0koMAN_nGZN5XkxGi08KjDEYbX0vpH4"
  [coder]="8364352218:AAFxTuefGgOpKR6i7gzmSCFtktUlpMLS76s"
)

declare -A GROUP_IDS=(
  [main]="-1003843793948"
  [blitz]="-1003871432379"
  [system]="-1003880609464"
  [coder]="-1003263991986"
)

declare -A AGENT_NAMES=(
  [main]="Maaraa"
  [blitz]="Blitz"
  [system]="Techno4k"
  [coder]="Mzinho"
)

SESSIONS_DIR="/root/.openclaw/agents"
STATE_FILE="/root/.openclaw/workspace/memory/session-watchdog-state.json"
alerts=""

# Load previous state (last alerted threshold per agent)
declare -A PREV_THRESHOLD
if [ -f "$STATE_FILE" ]; then
  while IFS='=' read -r k v; do
    PREV_THRESHOLD[$k]="$v"
  done < <(python3 -c "
import json
with open('$STATE_FILE') as f:
    d = json.load(f)
for k,v in d.items():
    print(f'{k}={v}')
" 2>/dev/null)
fi

declare -A CURR_STATE

for agent in main blitz system coder; do
  sessions_file="$SESSIONS_DIR/$agent/sessions/sessions.json"
  [ -f "$sessions_file" ] || continue

  size_bytes=$(wc -c < "$sessions_file")
  size_kb=$((size_bytes / 1024))
  name="${AGENT_NAMES[$agent]}"
  token="${BOT_TOKENS[$agent]}"
  gid="${GROUP_IDS[$agent]}"
  prev_threshold="${PREV_THRESHOLD[$agent]:-0}"

  # Under first threshold — skip
  [ "$size_kb" -lt "$FIRST_ALERT_KB" ] && continue

  # Calculate which step we're at: 500, 700, 900, 1100...
  current_step=$(( ((size_kb - FIRST_ALERT_KB) / STEP_KB) * STEP_KB + FIRST_ALERT_KB ))

  # Only alert if we've crossed into a new step
  [ "$current_step" -le "$prev_threshold" ] && continue

  CURR_STATE[$agent]="$current_step"

  msg="⚠️ *Session Size Alert*%0A%0AAgent: *${name}* (${agent})%0ASession store: *${size_kb}KB*%0A%0ANext alert at: $((current_step + STEP_KB))KB%0A%0ARun \`/reset\` in this chat or ask Maaraa to clean up."

  curl -s "https://api.telegram.org/bot${token}/sendMessage" \
    -d "chat_id=${gid}" \
    -d "text=${msg}" \
    -d "parse_mode=Markdown" \
    --connect-timeout 10 \
    --max-time 15 > /dev/null 2>&1

  alerts="${alerts}${name}: ${size_kb}KB (threshold: ${current_step}KB)\n"
done

# Merge current state with previous (keep old entries for agents not alerting now)
python3 -c "
import json, sys
prev = {}
try:
    with open('$STATE_FILE') as f:
        prev = json.load(f)
except: pass
updates = {$(for a in "${!CURR_STATE[@]}"; do echo "\"$a\":\"${CURR_STATE[$a]}\","; done)}
prev.update(updates)
with open('$STATE_FILE', 'w') as f:
    json.dump(prev, f)
"

if [ -n "$alerts" ]; then
  echo -e "Session alerts sent:\n${alerts}"
else
  echo "All sessions within limits."
fi
