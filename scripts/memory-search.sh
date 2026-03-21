#!/bin/bash
# memory-search.sh — Unified memory search across all sources
# Usage: memory-search.sh <query> [max_results] [-c container]
# Sources: MEMORY.md, session transcripts, daily files, LEARNINGS.md
set -uo pipefail

WORKSPACE="/root/.openclaw/workspace"
MEMORY="$WORKSPACE/MEMORY.md"
SESSIONS_DIR="/root/.openclaw/agents/main/sessions"
MEMORY_DIR="$WORKSPACE/memory"
LEARNINGS="$WORKSPACE/LEARNINGS.md"
MAX="${2:-15}"
QUERY="${1:-}"
CONTAINER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--container) CONTAINER="$2"; shift 2 ;;
    *) if [ -z "$QUERY" ]; then QUERY="$1"; fi; shift ;;
  esac
done

[ -z "$QUERY" ] && { echo "Usage: memory-search.sh <query> [max_results] [-c container]"; exit 1; }

if [ -n "$CONTAINER" ]; then
  echo "🔍 Searching: '$QUERY' in container '$CONTAINER'"
else
  echo "🔍 Searching: '$QUERY'"
fi
echo ""

# ── MEMORY.md ──
echo "━━━ MEMORY.md ━━━"
if [ -f "$MEMORY" ]; then
  if [ -n "$CONTAINER" ]; then
    results=$(grep -in "$QUERY" "$MEMORY" 2>/dev/null | grep -i "container:$CONTAINER" | head -"$MAX" || true)
  else
    results=$(grep -in "$QUERY" "$MEMORY" 2>/dev/null | head -"$MAX" || true)
  fi
  if [ -n "$results" ]; then
    echo "$results" | while IFS= read -r line; do echo "  $line"; done
  else
    echo "  (no matches)"
  fi
else
  echo "  (file not found)"
fi

# ── Session Transcripts (last 7 days) ──
echo ""
echo "━━━ Conversations (transcripts) ━━━"
if [ -d "$SESSIONS_DIR" ]; then
  SEVEN_DAYS_AGO=$(date -d '7 days ago' '+%Y-%m-%d' 2>/dev/null || date -v-7d '+%Y-%m-%d' 2>/dev/null || echo "")
  CONVO_COUNT=0

  while IFS= read -r f; do
    [ -f "$f" ] || continue
    matches=$(jq -r '
      select(.type == "message") |
      select(.message.role == "user" or .message.role == "assistant") |
      . as $root | .message as $msg | $msg.content |
      if type == "array" then map(.text // "") | join(" ")
      elif type == "string" then .
      else empty end |
      select(length > 0) |
      "[" + ($root.timestamp // "unknown") + "] " + $msg.role + ": " + .
    ' "$f" 2>/dev/null \
      | grep -i "$QUERY" | head -3 || true)
    if [ -n "$matches" ]; then
      bname=$(basename "$f" .jsonl)
      echo "  📄 $bname:"
      echo "$matches" | while IFS= read -r line; do
        # Truncate long lines
        truncated="${line:0:200}"
        echo "    ▸ $truncated"
      done
      CONVO_COUNT=$((CONVO_COUNT + 1))
      [ $CONVO_COUNT -ge $MAX ] && break
    fi
  done < <(find "$SESSIONS_DIR" -name '*.jsonl' -mtime -7 -printf '%T@ %p\n' 2>/dev/null | sort -rn | awk '{print $2}')

  [ $CONVO_COUNT -eq 0 ] && echo "  (no matches)"
else
  echo "  (no sessions directory)"
fi

# ── Daily memory files ──
echo ""
echo "━━━ Daily Notes ━━━"
if [ -d "$MEMORY_DIR" ]; then
  found=0
  for f in $(ls -1t "$MEMORY_DIR"/20*.md 2>/dev/null | head -7); do
    matches=$(grep -i "$QUERY" "$f" 2>/dev/null | head -3 || true)
    if [ -n "$matches" ]; then
      bname=$(basename "$f" .md)
      echo "  📄 $bname:"
      echo "$matches" | while IFS= read -r line; do echo "    $line"; done
      found=$((found + 1))
    fi
  done
  [ $found -eq 0 ] && echo "  (no matches)"
else
  echo "  (no daily files)"
fi

# ── LEARNINGS.md ──
echo ""
echo "━━━ Learnings ━━━"
if [ -f "$LEARNINGS" ]; then
  grep -in "$QUERY" "$LEARNINGS" 2>/dev/null | head -5 | while IFS= read -r line; do
    echo "  ⚠ $line"
  done
  grep -ic "$QUERY" "$LEARNINGS" 2>/dev/null | grep -q '^0$' && echo "  (no matches)" || true
else
  echo "  (no learnings file)"
fi
