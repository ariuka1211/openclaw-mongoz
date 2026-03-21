#!/bin/bash
# memory-nightly-cleanup.sh — Nightly cleanup of memory files
#
# Steps:
#   1. Daily → MEMORY.md distillation (promote daily buffer to permanent memory)
#   2. Clean MEMORY.md, LEARNINGS.md, TOOLS.md (dedup, trim, archive removed items)
#
# Uses memory-llm.sh for LLM-powered cleanup decisions.
# Requires: OPENROUTER_API_KEY, memory-llm.sh, jq
set -euo pipefail

W="/root/.openclaw/workspace"
source "$W/.env" 2>/dev/null || true
[ -z "${OPENROUTER_API_KEY:-}" ] && echo "⚠️ No API key" && exit 1

LOG="/tmp/memory-nightly-cleanup.log"
OBS="$W/scripts/obs-log.sh"
TODAY=$(date -u '+%Y-%m-%d')
DAILY_DIR="$W/memory/daily"
ARCHIVE_DIR="$W/memory/archived"
TARGET_LINES=200

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" >> "$LOG"; }

mkdir -p "$ARCHIVE_DIR" "$DAILY_DIR"

log "=== nightly cleanup start ==="
bash "$OBS" "info" "memory-nightly-cleanup" "Nightly cleanup started"

# ── Helper: LLM call with retry ──
MAX_RETRIES=3
call_llm() {
  local sys_prompt="$1" model="${2:-google/gemini-2.5-flash}" max_tokens="${3:-4096}"
  local attempt=1 result=""
  while (( attempt <= MAX_RETRIES )); do
    if result=$(bash "$W/scripts/memory-llm.sh" "$sys_prompt" "$model" "$max_tokens" 2>/dev/null); then
      echo "$result"
      return 0
    fi
    log "LLM retry $attempt/$MAX_RETRIES"
    sleep $(( 10 * attempt ))
    (( attempt++ ))
  done
  log "LLM failed after $MAX_RETRIES attempts"
  return 1
}

# ── Step 1: Daily → MEMORY.md distillation ──
DAILY_FILE="$DAILY_DIR/${TODAY}.md"

if [ -f "$DAILY_FILE" ]; then
  DAILY_CONTENT=$(cat "$DAILY_FILE")
  DAILY_LINES=$(wc -l < "$DAILY_FILE")

  if [ "$DAILY_LINES" -gt 3 ]; then
    log "Distilling daily file: $DAILY_FILE ($DAILY_LINES lines)"

    CURRENT_MEMORY=$(cat "$W/MEMORY.md" 2>/dev/null || echo "")

    DISTILL_PROMPT="You are a memory distiller. Review today's daily memory notes and extract ONLY the most important, lasting items worth keeping permanently.

Rules:
1. Skip transient details (specific timestamps, routine status updates, minor debug info)
2. Keep: decisions, lessons learned, preferences, important facts, emotional context, patterns
3. Skip items already well-covered in Current Memory
4. Be selective — quality over quantity. 5-15 items max.
5. Format each item as:
   - [tag] (container) content — source
   Tags: decision, lesson, pattern, rule, fact, open
   Containers: personal, trading, career, system, philosophy

## Current Memory (dedup reference):
$CURRENT_MEMORY

## Today's Daily Notes:
$DAILY_CONTENT

## Output:
List only the items worth permanently keeping. No commentary. If nothing is worth keeping, output 'NONE'."

    DISTILLED=$(echo "$DISTILL_PROMPT" | call_llm "Distill daily notes into permanent memory items. Be selective." "google/gemini-2.5-flash" 4096) || true

    if [ -n "${DISTILLED:-}" ] && [ "$DISTILLED" != "NONE" ]; then
      # Strip markdown fences if present
      DISTILLED=$(echo "$DISTILLED" | sed '/^```/,/^```/d')

      if [ -n "$DISTILLED" ]; then
        echo "" >> "$W/MEMORY.md"
        echo "## [active]" >> "$W/MEMORY.md"
        echo "$DISTILLED" >> "$W/MEMORY.md"

        PROMOTED=$(echo "$DISTILLED" | grep -c '^-' || echo 0)
        log "Promoted $PROMOTED items from daily → MEMORY.md"
        bash "$OBS" "info" "memory-nightly-cleanup" "Daily distillation complete" "promoted=$PROMOTED"
      else
        log "Daily distillation produced empty result after stripping fences"
      fi
    else
      log "Daily distillation: no items worth promoting"
    fi
  else
    log "Daily file too short ($DAILY_LINES lines), skipping distillation"
  fi
else
  log "No daily file for today ($DAILY_FILE), skipping distillation"
fi

# ── Step 2: CLEANUP — MEMORY.md, LEARNINGS.md, TOOLS.md ──
cleanup_file() {
  local FILE="$1" LABEL="$2" MAX_LINES="$3"
  local FILENAME=$(basename "$FILE")

  [ ! -f "$FILE" ] && log "SKIP $LABEL: not found" && return

  local LINES=$(wc -l < "$FILE")
  log "Processing $LABEL: $LINES lines"

  if [ "$LINES" -le "$MAX_LINES" ]; then
    log "$LABEL is under $MAX_LINES lines, minimal cleanup only"
  fi

  # Backup before cleanup
  cp "$FILE" "$FILE.bak"

  local CONTENT=$(cat "$FILE")

  local PROMPT="You are a memory file cleaner. Clean up this $LABEL file.

Rules:
1. REMOVE: duplicate entries, obsolete config notes, transient debug info
2. KEEP: active items, recent decisions, important rules, emotional weight
3. PRESERVE: all metadata tags ([date], [status], container:*, (source))
4. If file exceeds $MAX_LINES lines, prioritize cutting the least important items
5. Output ONLY the cleaned file content — no commentary, no markdown fences

## Current $LABEL ($LINES lines):
$CONTENT"

  local CLEANED
  if CLEANED=$(echo "$PROMPT" | call_llm "Clean up $LABEL. Output only the cleaned file content." "google/gemini-2.5-flash" 16384); then
    # Strip markdown fences if present
    CLEANED=$(echo "$CLEANED" | sed '/^```/,/^```/d')
    [ -z "$CLEANED" ] && log "WARN: LLM returned empty for $LABEL, keeping original" && return

    # Archive the diff (items removed)
    diff "$FILE.bak" <(echo "$CLEANED") > "$ARCHIVE_DIR/${FILENAME%.md}-${TODAY}.diff" 2>/dev/null || true

    # Archive removed lines
    comm -23 <(sort "$FILE.bak") <(sort <(echo "$CLEANED")) \
      > "$ARCHIVE_DIR/${FILENAME%.md}-${TODAY}-removed.md" 2>/dev/null || true

    echo "$CLEANED" > "$FILE"

    local NEW_LINES=$(wc -l < "$FILE")
    local REMOVED=$(( LINES - NEW_LINES ))
    log "Cleaned $LABEL: $LINES → $NEW_LINES lines (removed ~$REMOVED)"

    # If still over target, truncate from the bottom (preamble/header preserved)
    if [ "$NEW_LINES" -gt "$MAX_LINES" ]; then
      head -n "$MAX_LINES" "$FILE" > "$FILE.tmp"
      mv "$FILE.tmp" "$FILE"
      log "Truncated $LABEL to $MAX_LINES lines"
    fi
  else
    log "WARN: LLM cleanup failed for $LABEL, keeping original"
  fi
}

cleanup_file "$W/MEMORY.md" "MEMORY.md" "$TARGET_LINES"
cleanup_file "$W/LEARNINGS.md" "LEARNINGS.md" 100
cleanup_file "$W/TOOLS.md" "TOOLS.md" 150

# ── Clean up old archives (keep 30 days) ──
find "$ARCHIVE_DIR" -name '*.diff' -mtime +30 -delete 2>/dev/null || true
find "$ARCHIVE_DIR" -name '*-removed.md' -mtime +30 -delete 2>/dev/null || true

# ── Clean up .bak files ──
rm -f "$W/MEMORY.md.bak" "$W/LEARNINGS.md.bak" "$W/TOOLS.md.bak"

log "=== nightly cleanup done ==="
bash "$OBS" "info" "memory-nightly-cleanup" "Nightly cleanup completed"
echo "Nightly cleanup: ✅ done"

# Cron: memory-nightly-cleanup (daily 05:00 UTC)
