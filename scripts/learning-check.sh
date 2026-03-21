#!/usr/bin/env bash
# learning-check.sh — Surface relevant learnings before taking action
# Usage: learning-check.sh <category>
# Categories: deploy, config, debug, spawn, think, approval, knowledge

set -euo pipefail

DB="/root/.openclaw/workspace/data/freddy.db"
CATEGORY="${1:-}"
LIMIT="${2:-5}"

if [ -z "$CATEGORY" ]; then
  echo "Usage: learning-check.sh <category>"
  echo "Categories: deploy, config, debug, spawn, think, approval, knowledge"
  exit 1
fi

echo "=== 🔴 Active ($CATEGORY) ==="
sqlite3 -box "$DB" "
  SELECT code, title, repeat_count, last_triggered, prevention
  FROM learnings
  WHERE category='$CATEGORY' AND tier='active'
  ORDER BY repeat_count DESC, last_triggered DESC
  LIMIT $LIMIT;
" 2>/dev/null || echo "(none)"

echo ""
echo "=== 🟡 Watch ($CATEGORY) ==="
sqlite3 -box "$DB" "
  SELECT code, title, repeat_count, last_triggered
  FROM learnings
  WHERE category='$CATEGORY' AND tier='watch'
  ORDER BY last_triggered DESC
  LIMIT 3;
" 2>/dev/null || echo "(none)"

# Count total active
ACTIVE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM learnings WHERE tier='active';")
echo ""
echo "Total active learnings: $ACTIVE"
