#!/bin/bash
# memory-session-extract.sh — LLM-powered memory pipeline (layered: session → daily → MEMORY.md)
#
# Pipeline:
#   1. Read new session transcripts (tracked via .memory-processed-sessions)
#   2. LLM Distill  — raw transcripts → clean topic bullets
#   3. LLM Extract  — distilled bullets → structured items (JSON)
#   4. Append       — write to memory/daily/YYYY-MM-DD.md
#   5. Sync         — update processed-sessions list
#   6. Report       — log summary
#
# Requires: OPENROUTER_API_KEY, memory-llm.sh, jq
set -euo pipefail

W="/root/.openclaw/workspace"
source "$W/.env" 2>/dev/null || true
[ -z "${OPENROUTER_API_KEY:-}" ] && echo "⚠️ No API key" && exit 1

LOG="/tmp/memory-session-extract.log"
OBS="$W/scripts/obs-log.sh"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" >> "$LOG"; }

# ── Retry wrapper for LLM calls ──
MAX_RETRIES=3
RETRY_BACKOFF=10
retry_llm() {
  local sys_prompt="$1" model="$2" max_tokens="$3"
  local attempt=1 result=""
  while (( attempt <= MAX_RETRIES )); do
    if result=$(bash "$W/scripts/memory-llm.sh" "$sys_prompt" "$model" "$max_tokens" 2>/dev/null); then
      echo "$result"
      return 0
    fi
    bash "$OBS" "warn" "memory-session-extract" "LLM call failed, retrying" "attempt=$attempt" "model=$model"
    log "LLM retry $attempt/$MAX_RETRIES (model=$model)"
    sleep $(( RETRY_BACKOFF * attempt ))
    (( attempt++ ))
  done
  bash "$OBS" "error" "memory-session-extract" "LLM call failed after $MAX_RETRIES attempts" "model=$model"
  return 1
}

log "=== start ==="
bash "$OBS" "info" "memory-session-extract" "Pipeline started"

# ── Step 1: Read new session transcripts (processed-sessions tracking) ──
SESSIONS_DIR="/root/.openclaw/agents/main/sessions"
PROCESSED_FILE="$W/data/.memory-processed-sessions"
DAILY_DIR="$W/memory/daily"

mkdir -p "$DAILY_DIR" "$(dirname "$PROCESSED_FILE")"

# Load processed list (empty file is fine)
touch "$PROCESSED_FILE"

# Find all .jsonl files, skip already-processed ones
NEW_FILES=()
while IFS= read -r f; do
  [ -f "$f" ] || continue
  basename_f=$(basename "$f")
  if ! grep -qxF "$basename_f" "$PROCESSED_FILE" 2>/dev/null; then
    NEW_FILES+=("$f")
  fi
done < <(find "$SESSIONS_DIR" -name '*.jsonl' -type f 2>/dev/null | sort)

if [ ${#NEW_FILES[@]} -eq 0 ]; then
  echo "Memory daily: 0 new transcripts to process"
  bash "$OBS" "info" "memory-session-extract" "No new transcripts to process"
  exit 0
fi

# Build transcript text from new files
TRANSCRIPTS=""
for f in "${NEW_FILES[@]}"; do
  MESSAGES=$(jq -r '
    select(.type == "message") |
    select(.message.role == "user" or .message.role == "assistant") |
    . as $root | .message as $msg | $msg.content |
    if type == "array" then map(.text // "") | join(" ")
    elif type == "string" then .
    else empty end |
    select(length > 0) |
    "[" + ($root.timestamp // "unknown") + "] " + $msg.role + ": " + .[0:2000]
  ' "$f" 2>/dev/null || true)
  [ -n "$MESSAGES" ] && TRANSCRIPTS+=$'\n'"--- $(basename "$f") ---"$'\n'"$MESSAGES"$'\n'
done

TRANSCRIPTS="${TRANSCRIPTS#$'\n'}"

if [ -z "$TRANSCRIPTS" ]; then
  echo "Memory daily: ${#NEW_FILES[@]} files found but no extractable messages"
  # Still mark as processed since we tried
  for f in "${NEW_FILES[@]}"; do echo "$(basename "$f")" >> "$PROCESSED_FILE"; done
  exit 0
fi

log "Found ${#NEW_FILES[@]} new transcript files"

PROMPTS="$W/prompts"
load_prompt() {
  python3 -c "
import sys, os
tmpl = open('$PROMPTS/$1').read()
for key, val in os.environ.items():
    if key.startswith('PROMPT_'):
        tmpl = tmpl.replace('{{' + key[7:] + '}}', val)
print(tmpl, end='')
"
}

# ── Step 2: LLM Distill — raw transcripts → clean topic bullets ──
DISTILL_PROMPT=$(PROMPT_SUMMARIES="$TRANSCRIPTS" load_prompt "distill.txt")

DISTILLED=$(echo "$DISTILL_PROMPT" | retry_llm "Distill conversation transcripts into clean topic bullets." "google/gemini-2.5-flash" 4096) || DISTILLED="$TRANSCRIPTS"

DBULLETS=$(echo "$DISTILLED" | grep -c '^[*-]' || echo "?")
log "Distilled ${#NEW_FILES[@]} transcripts → $DBULLETS topic bullets"

# ── Step 3: LLM Extract — distilled bullets → structured items (JSON) ──
CURRENT=$(cat "$W/MEMORY.md" 2>/dev/null || echo "")

EXTRACT_PROMPT=$(PROMPT_CURRENT="$CURRENT" PROMPT_DISTILLED="$DISTILLED" load_prompt "extract.txt")

RESP=$(echo "$EXTRACT_PROMPT" | retry_llm "Extract memory items as JSON." "google/gemini-2.5-flash" 8192) || true

if ! echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'items' in d" 2>/dev/null; then
  log "Bad LLM response: $(echo "$RESP" | head -c 300)"
  bash "$OBS" "error" "memory-session-extract" "LLM returned malformed JSON" "response_len=$(echo "$RESP" | wc -c)"
  echo "⚠️ LLM extraction failed"
  exit 1
fi

ICOUNT=$(echo "$RESP" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('items',[])))")
[ "$ICOUNT" -eq 0 ] && echo "Memory daily: 0 new items" && for f in "${NEW_FILES[@]}"; do echo "$(basename "$f")" >> "$PROCESSED_FILE"; done && exit 0
log "Extracted $ICOUNT items"

# ── Step 4: Append to memory/daily/YYYY-MM-DD.md ──
TODAY=$(date -u '+%Y-%m-%d')
DAILY_FILE="$DAILY_DIR/${TODAY}.md"

# Create header if file doesn't exist
if [ ! -f "$DAILY_FILE" ]; then
  echo "# Daily Memory — ${TODAY}" > "$DAILY_FILE"
  echo "" >> "$DAILY_FILE"
fi

# Convert JSON items to bullet points and append
echo "$RESP" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for item in data.get('items', []):
    tag = item.get('tag', 'fact')
    content = item.get('content', '')
    container = item.get('container', '')
    source = item.get('source', '')
    line = f'- [{tag}]'
    if container:
        line += f' ({container})'
    line += f' {content}'
    if source:
        line += f' — {source}'
    print(line)
" >> "$DAILY_FILE"

echo "" >> "$DAILY_FILE"

log "Appended $ICOUNT items to $DAILY_FILE"

# ── Step 5: Update processed-sessions list ──
for f in "${NEW_FILES[@]}"; do
  echo "$(basename "$f")" >> "$PROCESSED_FILE"
done
log "Updated processed-sessions: +${#NEW_FILES[@]} files"

# ── Step 6: Report ──
REPORT=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('summary','done'))" 2>/dev/null || echo "done")
log "=== $REPORT ==="
bash "$OBS" "info" "memory-session-extract" "Pipeline completed" "result=$REPORT" "items=$ICOUNT" "files=${#NEW_FILES[@]}"
echo "Memory daily: ✅ $ICOUNT items → $DAILY_FILE ($REPORT)"

# Cron: memory-session-extract (every 2hrs)
