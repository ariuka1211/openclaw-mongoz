#!/bin/bash
# memory-daily.sh — LLM-powered memory pipeline
#
# Pipeline order (DO NOT REORDER — dependencies between steps):
#   1. Pull new LCM summaries (from lcm.db)
#   2. LLM Distill  — raw summaries → clean topic bullets (noise removal)
#   3. LLM Extract  — distilled bullets → structured items + contradictions (JSON)
#   4. Version      — supersede contradictions, TTL decay, archive old items
#   5. Append       — new items into MEMORY.md with metadata & container tags
#   6. LLM Cleanup  — deduplicate/trim each MEMORY.md section
#   7. Profile      — regenerate condensed user profile (memory/profile.md)
#   8. Promote      — ⭐ mark recurring themes in MEMORY.md
#   9. Sync         — update pull tracker so next run picks up where we left off
#  10. Report       — log summary + clean up temp files
#
# Requires: OPENROUTER_API_KEY, memory-llm.sh, sqlite3, jq
set -euo pipefail

W="/root/.openclaw/workspace"
source "$W/.env" 2>/dev/null || true
[ -z "${OPENROUTER_API_KEY:-}" ] && echo "⚠️ No API key" && exit 1

LOG="/tmp/memory-daily.log"
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
    bash "$OBS" "warn" "memory-daily" "LLM call failed, retrying" "attempt=$attempt" "model=$model"
    log "LLM retry $attempt/$MAX_RETRIES (model=$model)"
    sleep $(( RETRY_BACKOFF * attempt ))
    (( attempt++ ))
  done
  bash "$OBS" "error" "memory-daily" "LLM call failed after $MAX_RETRIES attempts" "model=$model"
  return 1
}

log "=== start ==="
rm -f /tmp/memory-llm-response.json
bash "$OBS" "info" "memory-daily" "Pipeline started"

# ── Pre-step: Archive old daily notes (housekeeping, not part of LCM pipeline) ──
mkdir -p "$W/data/daily/archive" 2>/dev/null
find "$W/data/daily" -maxdepth 1 -name "*.md" -mtime +7 -exec mv {} "$W/data/daily/archive/" \; 2>/dev/null || true

# ── Step 1: Pull new LCM summaries ──
SINCE=$(cat "$W/data/.last_memory_pull" 2>/dev/null || echo "2000-01-01 00:00:00")
SUMMARIES=$(sqlite3 /root/.openclaw/lcm.db "
  SELECT '--- Summary ' || summary_id || ' (' || substr(created_at, 1, 16) || ') ---' || char(10) || content
  FROM summaries WHERE created_at > '$SINCE' ORDER BY created_at ASC;
" 2>/dev/null || true)
[ -z "$SUMMARIES" ] && echo "Memory daily: 0 summaries" && bash "$OBS" "info" "memory-daily" "No new summaries to process" && exit 0
SCOUNT=$(echo "$SUMMARIES" | grep -c '^--- Summary' || echo 0)
log "Found $SCOUNT summaries"

PROMPTS="$W/prompts"
# Use Python for safe template substitution (handles special chars, newlines)
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

# ── Step 2: LLM Distill — raw summaries → clean topic bullets ──
DISTILL_PROMPT=$(PROMPT_SUMMARIES="$SUMMARIES" load_prompt "distill.txt")

DISTILLED=$(echo "$DISTILL_PROMPT" | retry_llm "Distill conversation summaries into clean topic bullets." "google/gemini-2.5-flash" 4096) || DISTILLED="$SUMMARIES"

DBULLETS=$(echo "$DISTILLED" | grep -c '^[*-]' || echo "?")
log "Distilled $SCOUNT summaries → $DBULLETS topic bullets"

# ── Step 3: LLM Extract — distilled bullets → structured items + contradictions (JSON) ──
CURRENT=$(grep -A 999 '^## ' "$W/MEMORY.md" 2>/dev/null || echo "")

EXTRACT_PROMPT=$(PROMPT_CURRENT="$CURRENT" PROMPT_DISTILLED="$DISTILLED" load_prompt "extract.txt")

RESP=$(echo "$EXTRACT_PROMPT" | retry_llm "Extract memory items as JSON." "google/gemini-2.5-flash" 8192) || true

if ! echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'items' in d" 2>/dev/null; then
  log "Bad LLM response: $(echo "$RESP" | head -c 300)"
  bash "$OBS" "error" "memory-daily" "LLM returned malformed JSON" "response_len=$(echo "$RESP" | wc -c)"
  echo "⚠️ LLM extraction failed"
  exit 1
fi

ICOUNT=$(echo "$RESP" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('items',[])))")
UCOUNT=$(echo "$RESP" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('updates',[])))" 2>/dev/null || echo 0)
[ "$ICOUNT" -eq 0 ] && [ "$UCOUNT" -eq 0 ] && echo "Memory daily: 0 new items, 0 updates" && sqlite3 /root/.openclaw/lcm.db "SELECT max(created_at) FROM summaries;" > "$W/data/.last_memory_pull" && exit 0
log "Extracted $ICOUNT items, $UCOUNT updates"

# ── Step 4: Version — supersede contradictions, TTL decay, archive old items ──
cp "$W/MEMORY.md" "$W/MEMORY.md.bak"
export MEMORY_FILE="$W/MEMORY.md" LLM_RESPONSE="$RESP"
VERSION=$(python3 "$W/scripts/mem-version.py" 2>&1) || true
log "$VERSION"
# After versioning: enriched LLM_RESPONSE is at /tmp/memory-llm-response.json
# (mem-version.py writes the enriched response there for step 5 to consume)

# ── Step 5: Append — new items into MEMORY.md with metadata & container tags ──
APPEND=$(python3 "$W/scripts/mem-append.py")
log "$APPEND"

# ── Step 6: LLM Cleanup — deduplicate/trim each MEMORY.md section ──
export WORKSPACE="$W"
CLEANUP=$(python3 "$W/scripts/mem-cleanup.py")
log "$CLEANUP"

# ── Step 7: Profile — regenerate condensed user profile (memory/profile.md) ──
PROFILE=$(bash "$W/scripts/mem-profile.sh" 2>&1) || true
log "$PROFILE"

# ── Step 8: Promote — ⭐ mark recurring themes in MEMORY.md ──
export MEMORY_FILE="$W/MEMORY.md"
PROMOTE=$(python3 "$W/scripts/mem-promote.py")
log "$PROMOTE"

# ── Step 9: Sync — update pull tracker so next run picks up where we left off ──
sqlite3 /root/.openclaw/lcm.db "SELECT max(created_at) FROM summaries;" 2>/dev/null > "$W/data/.last_memory_pull" || date -u '+%Y-%m-%d %H:%M:%S' > "$W/data/.last_memory_pull"

# ── Step 10: Report — log summary + clean up temp files ──
REPORT=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('summary','done'))" 2>/dev/null || echo "done")
rm -f /tmp/memory-llm-response.json
log "=== $REPORT ==="
bash "$OBS" "info" "memory-daily" "Pipeline completed" "result=$REPORT" "items=$ICOUNT" "updates=$UCOUNT"
echo "Memory daily: ✅ $REPORT"
