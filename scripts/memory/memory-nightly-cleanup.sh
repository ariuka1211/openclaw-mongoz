#!/bin/bash
# memory-nightly-cleanup.sh — Nightly cleanup of memory files
#
# Steps:
#   1. Daily → MEMORY.md distillation (promote daily buffer to permanent memory)
#   2. Surgical cleanup of MEMORY.md (dedup, trim — no LLM rewrite)
#
# Requires: KILOCODE_API_KEY (for Step 1 distillation)
set -euo pipefail

W="/root/.openclaw/workspace"
source "$W/.env" 2>/dev/null || true
source "$W/scripts/memory/.env" 2>/dev/null || true
[ -z "${KILOCODE_API_KEY:-}" ] && echo "⚠️ No API key" && exit 1

LOG="/tmp/memory-nightly-cleanup.log"
OBS="$W/scripts/memory/obs-log.sh"
TODAY=$(TZ='America/Denver' date '+%Y-%m-%d')
DAILY_DIR="$W/memory"
ARCHIVE_DIR="$W/memory/archived"
TARGET_LINES=200

log() { echo "[$(TZ='America/Denver' date '+%Y-%m-%d %H:%M:%S %Z')] $*" >> "$LOG"; }

mkdir -p "$ARCHIVE_DIR" "$DAILY_DIR"

log "=== nightly cleanup start ==="
bash "$OBS" "info" "memory-nightly-cleanup" "Nightly cleanup started"

# ── Helper: LLM call with retry ──
MAX_RETRIES=3
call_llm() {
  local sys_prompt="$1" model="${2:-xiaomi/mimo-v2-pro}" max_tokens="${3:-4096}"
  local attempt=1 result=""
  while (( attempt <= MAX_RETRIES )); do
    if result=$(bash "$W/scripts/memory/memory-llm.sh" "$sys_prompt" "$model" "$max_tokens" 2>/dev/null); then
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

    DISTILLED=$(echo "$DISTILL_PROMPT" | call_llm "Distill daily notes into permanent memory items. Be selective." "xiaomi/mimo-v2-pro" 4096) || true

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

# ── Step 2: Surgical cleanup — MEMORY.md only ──
cleanup_file() {
  local FILE="$1" LABEL="$2" MAX_LINES="$3"
  local FILENAME=$(basename "$FILE")

  [ ! -f "$FILE" ] && return

  local LINES=$(wc -l < "$FILE")

  # Step A: Dedup — remove exact duplicate lines (preserve order, keep first occurrence)
  awk '!seen[tolower($0)]++' "$FILE" > "$FILE.tmp"
  mv "$FILE.tmp" "$FILE"

  # Step B: If over target, trim from the bottom (oldest auto-extracted items first)
  local NEW_LINES=$(wc -l < "$FILE")
  if [ "$NEW_LINES" -gt "$MAX_LINES" ]; then
    # Archive overflow before trimming
    tail -n $(( NEW_LINES - MAX_LINES )) "$FILE" >> "$ARCHIVE_DIR/${FILENAME%.md}-${TODAY}-trimmed.md"
    head -n "$MAX_LINES" "$FILE" > "$FILE.tmp"
    mv "$FILE.tmp" "$FILE"
    log "Trimmed $LABEL to $MAX_LINES lines (archived overflow)"
  fi

  local FINAL_LINES=$(wc -l < "$FILE")
  log "Cleaned $LABEL: $LINES → $FINAL_LINES lines"
}

cleanup_file "$W/MEMORY.md" "MEMORY.md" "$TARGET_LINES"

# ── Clean up old archives (keep 30 days) ──
find "$ARCHIVE_DIR" -name '*.diff' -mtime +30 -delete 2>/dev/null || true
find "$ARCHIVE_DIR" -name '*-removed.md' -mtime +30 -delete 2>/dev/null || true

# ── Clean up .bak files ──
rm -f "$W/MEMORY.md.bak"

log "=== nightly cleanup done ==="
touch /tmp/cron-state/memory-cleanup.last-run
bash "$OBS" "info" "memory-nightly-cleanup" "Nightly cleanup completed"
echo "Nightly cleanup: ✅ done"

# Cron: memory-nightly-cleanup (daily 05:00 UTC)
