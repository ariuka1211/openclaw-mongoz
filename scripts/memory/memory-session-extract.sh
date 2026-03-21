#!/bin/bash
# memory-session-extract.sh — LLM-powered memory pipeline (multi-agent)
#
# Pipeline per agent:
#   1. Read new session transcripts (tracked via .memory-processed-sessions)
#   2. LLM Distill  — raw transcripts → clean topic bullets
#   3. LLM Extract  — distilled bullets → structured items (JSON)
#   4. Append       — write to agent's MEMORY.md
#   5. Sync         — update processed-sessions list
#   6. Cleanup      — delete processed files, remove .reset.* and .deleted.*
#   7. Report       — log summary
#
# Requires: KILOCODE_API_KEY, memory-llm.sh, jq
set -euo pipefail

W="/root/.openclaw/workspace"
source "$W/.env" 2>/dev/null || true
source "$W/scripts/memory/.env" 2>/dev/null || true
[ -z "${KILOCODE_API_KEY:-}" ] && echo "⚠️ No API key" && exit 1

LOG="/tmp/memory-session-extract.log"
OBS="$W/scripts/learning/obs-log.sh"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" >> "$LOG"; }

# ── Retry wrapper for LLM calls ──
MAX_RETRIES=3
RETRY_BACKOFF=10
retry_llm() {
  local sys_prompt="$1" model="$2" max_tokens="$3"
  local attempt=1 result=""
  while (( attempt <= MAX_RETRIES )); do
    if result=$(bash "$W/scripts/memory/memory-llm.sh" "$sys_prompt" "$model" "$max_tokens" 2>/dev/null); then
      echo "$result"
      return 0
    fi
    bash "$OBS" "warn" "memory-session-extract" "LLM call failed, retrying" "attempt=$attempt" "model=$model"
    log "LLM retry $attempt/$MAX_RETRIES (model=$model)"
    sleep $(( RETRY_BACKOFF * attempt ))
    (( attempt++ )) || true
  done
  bash "$OBS" "error" "memory-session-extract" "LLM call failed after $MAX_RETRIES attempts" "model=$model"
  return 1
}

log "=== start ==="
bash "$OBS" "info" "memory-session-extract" "Pipeline started"

# ── Agent definitions ──
# Format: agent_name:sessions_dir:memory_file
declare -a AGENTS=(
  "main:/root/.openclaw/agents/main/sessions:$W/MEMORY.md"
)

PROCESSED_FILE="$W/data/.memory-processed-sessions"
DAILY_DIR="$W/memory"
DELETION_CMD="trash"  # prefer recoverable deletion
command -v trash &>/dev/null || DELETION_CMD="rm -f"

mkdir -p "$DAILY_DIR" "$(dirname "$PROCESSED_FILE")"
touch "$PROCESSED_FILE"

# ── Migration: convert old flat basenames to agent/basename format ──
# Old entries were just basenames (all from main). Migrate them.
if grep -q '^[^/]*\.jsonl$' "$PROCESSED_FILE" 2>/dev/null; then
  log "Migrating old flat processed entries to main/ prefix"
  sed -i 's|^\([^/]*\.jsonl\)$|main/\1|' "$PROCESSED_FILE"
  # Deduplicate in case of re-runs
  sort -u "$PROCESSED_FILE" -o "$PROCESSED_FILE"
fi

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

TOTAL_ITEMS=0
TOTAL_FILES=0
AGENTS_DONE=0

# ── Process each agent ──
for agent_def in "${AGENTS[@]}"; do
  IFS=':' read -r AGENT_NAME SESSIONS_DIR MEMORY_FILE <<< "$agent_def"

  # Skip agents with no sessions directory or no files
  if [ ! -d "$SESSIONS_DIR" ]; then
    log "[$AGENT_NAME] sessions dir not found, skipping"
    continue
  fi

  # ── Cleanup: remove .reset.* and .deleted.* files ──
  CLEANED=0
  while IFS= read -r stale_file; do
    [ -f "$stale_file" ] || continue
    $DELETION_CMD "$stale_file" 2>/dev/null && (( CLEANED++ )) || true
  done < <(find "$SESSIONS_DIR" -type f \( -name '*.reset.*' -o -name '*.deleted.*' \) 2>/dev/null)
  [ "$CLEANED" -gt 0 ] && log "[$AGENT_NAME] Cleaned up $CLEANED stale files (.reset/.deleted)"

  # ── Find new .jsonl files (skip .lock'd and already-processed) ──
  NEW_FILES=()
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    basename_f=$(basename "$f")
    # Skip if .lock file exists (active session)
    [ -f "${f}.lock" ] && continue
    # Skip non-.jsonl (e.g. sessions.json)
    [[ "$basename_f" == *.jsonl ]] || continue
    # Check processed list with agent/ prefix
    if ! grep -qxF "${AGENT_NAME}/${basename_f}" "$PROCESSED_FILE" 2>/dev/null; then
      NEW_FILES+=("$f")
    fi
  done < <(find "$SESSIONS_DIR" -name '*.jsonl' -type f 2>/dev/null | sort)

  if [ ${#NEW_FILES[@]} -eq 0 ]; then
    log "[$AGENT_NAME] No new transcripts to process"
    continue
  fi

  # ── Skip agents with no MEMORY.md target ──
  if [ -z "$MEMORY_FILE" ] || [ ! -f "$MEMORY_FILE" ]; then
    log "[$AGENT_NAME] No MEMORY.md found, marking files as processed but skipping extraction"
    for f in "${NEW_FILES[@]}"; do
      echo "${AGENT_NAME}/$(basename "$f")" >> "$PROCESSED_FILE"
      $DELETION_CMD "$f" 2>/dev/null || true
    done
    continue
  fi

  log "[$AGENT_NAME] Processing ${#NEW_FILES[@]} new transcript files"

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
    log "[$AGENT_NAME] ${#NEW_FILES[@]} files found but no extractable messages"
    for f in "${NEW_FILES[@]}"; do
      echo "${AGENT_NAME}/$(basename "$f")" >> "$PROCESSED_FILE"
      $DELETION_CMD "$f" 2>/dev/null || true
    done
    continue
  fi

  # ── Step 2: LLM Distill ──
  DISTILL_PROMPT=$(PROMPT_SUMMARIES="$TRANSCRIPTS" load_prompt "distill.txt")

  DISTILLED=$(echo "$DISTILL_PROMPT" | retry_llm "Distill conversation transcripts into clean topic bullets." "xiaomi/mimo-v2-pro" 4096) || DISTILLED="$TRANSCRIPTS"

  DBULLETS=$(echo "$DISTILLED" | grep -c '^[*-]' || echo "?")
  log "[$AGENT_NAME] Distilled ${#NEW_FILES[@]} transcripts → $DBULLETS topic bullets"

  # ── Step 3: LLM Extract ──
  CURRENT=$(cat "$MEMORY_FILE" 2>/dev/null || echo "")

  EXTRACT_PROMPT=$(PROMPT_CURRENT="$CURRENT" PROMPT_DISTILLED="$DISTILLED" load_prompt "extract.txt")

  RESP=$(echo "$EXTRACT_PROMPT" | retry_llm "Extract memory items as JSON. Respond only with valid JSON, no explanations." "xiaomi/mimo-v2-pro" 8192) || true

  if ! echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'items' in d" 2>/dev/null; then
    log "[$AGENT_NAME] Bad LLM response: $(echo "$RESP" | head -c 300)"
    bash "$OBS" "error" "memory-session-extract" "LLM returned malformed JSON" "agent=$AGENT_NAME" "response_len=$(echo "$RESP" | wc -c)"
    # Still mark as processed — don't re-process bad sessions
    for f in "${NEW_FILES[@]}"; do
      echo "${AGENT_NAME}/$(basename "$f")" >> "$PROCESSED_FILE"
      $DELETION_CMD "$f" 2>/dev/null || true
    done
    continue
  fi

  ICOUNT=$(echo "$RESP" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('items',[])))")
  if [ "$ICOUNT" -eq 0 ]; then
    log "[$AGENT_NAME] 0 new items extracted"
    for f in "${NEW_FILES[@]}"; do
      echo "${AGENT_NAME}/$(basename "$f")" >> "$PROCESSED_FILE"
      $DELETION_CMD "$f" 2>/dev/null || true
    done
    continue
  fi

  log "[$AGENT_NAME] Extracted $ICOUNT items"

  # ── Step 4: Append to agent's MEMORY.md ──
  # Append extracted items as a new section
  {
    echo ""
    echo "## Session Extract — $(date -u '+%Y-%m-%d') [auto]"
    echo ""
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
"
    echo ""
  } >> "$MEMORY_FILE"

  log "[$AGENT_NAME] Appended $ICOUNT items to $MEMORY_FILE"

  # ── Step 5: Update processed-sessions list + delete files ──
  for f in "${NEW_FILES[@]}"; do
    echo "${AGENT_NAME}/$(basename "$f")" >> "$PROCESSED_FILE"
    $DELETION_CMD "$f" 2>/dev/null || true
  done
  log "[$AGENT_NAME] Updated processed-sessions +${#NEW_FILES[@]} files, deleted originals"

  # ── Report ──
  REPORT=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('summary','done'))" 2>/dev/null || echo "done")
  log "[$AGENT_NAME] === $REPORT ==="
  echo "Memory [$AGENT_NAME]: ✅ $ICOUNT items → $MEMORY_FILE ($REPORT)"

  (( TOTAL_ITEMS += ICOUNT )) || true
  (( TOTAL_FILES += ${#NEW_FILES[@]} )) || true
  (( AGENTS_DONE++ )) || true
done

# ── Final report ──
if [ "$AGENTS_DONE" -eq 0 ]; then
  echo "Memory daily: 0 new transcripts across all agents"
  bash "$OBS" "info" "memory-session-extract" "No new transcripts to process"
else
  bash "$OBS" "info" "memory-session-extract" "Pipeline completed" "agents=$AGENTS_DONE" "items=$TOTAL_ITEMS" "files=$TOTAL_FILES"
  echo "Memory daily: ✅ $TOTAL_ITEMS items from $TOTAL_FILES files across $AGENTS_DONE agents"
fi

log "=== done: $AGENTS_DONE agents, $TOTAL_ITEMS items, $TOTAL_FILES files ==="
