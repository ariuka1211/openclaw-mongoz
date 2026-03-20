#!/bin/bash
# memory-search.sh — Unified memory search across all sources
# Usage: memory-search.sh <query> [max_results] [-c container]
# Sources: MEMORY.md, LCM (FTS), daily files, LEARNINGS.md
set -uo pipefail

WORKSPACE="/root/.openclaw/workspace"
MEMORY="$WORKSPACE/MEMORY.md"
LCM_DB="/root/.openclaw/lcm.db"
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

# ── LCM (full-text search) ──
echo ""
echo "━━━ Conversations (LCM) ━━━"
LCM_QUERY="$QUERY${CONTAINER:+ $CONTAINER}"

lcm_results=$(sqlite3 "$LCM_DB" "
  SELECT snippet(summaries_fts, 1, '▸', '◂', '…', 40)
  FROM summaries_fts
  WHERE summaries_fts MATCH '$LCM_QUERY'
  ORDER BY rank
  LIMIT $MAX;
" 2>/dev/null || true)

if [ -n "$lcm_results" ]; then
  echo "$lcm_results" | while IFS= read -r line; do echo "  ▸ $line"; done
else
  echo "  (no matches)"
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
