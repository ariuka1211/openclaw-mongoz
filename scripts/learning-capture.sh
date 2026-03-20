#!/usr/bin/env bash
# learning-capture.sh — Scan recent session for potential mistakes to log
# Pulls messages from LCM DB, looks for correction patterns
# Output is proposals for the agent to review and log via learning-log.sh

set -euo pipefail

LCM_DB="/root/.openclaw/lcm.db"
DB="/root/.openclaw/workspace/data/freddy.db"
HOURS="${1:-24}"
SINCE=$(date -u -d "${HOURS} hours ago" +"%Y-%m-%dT%H:%M:%S" 2>/dev/null || date -u -v-${HOURS}H +"%Y-%m-%dT%H:%M:%S")

echo "=== Potential Corrections (last ${HOURS}h) ==="
echo ""

# Look for correction patterns in user messages
echo "--- User Corrections ---"
sqlite3 "$LCM_DB" "
  SELECT substr(content, 1, 250) || ' [' || created_at || ']'
  FROM messages
  WHERE role = 'user' 
    AND created_at >= '$SINCE'
    AND (LOWER(content) LIKE '%no,%' 
         OR LOWER(content) LIKE '%that%'s wrong%'
         OR LOWER(content) LIKE '%don%'t do that%'
         OR LOWER(content) LIKE '%that broke%'
         OR LOWER(content) LIKE '%we already%'
         OR LOWER(content) LIKE '%why did you%'
         OR LOWER(content) LIKE '%that%'s not%'
         OR LOWER(content) LIKE '%fix this%'
         OR LOWER(content) LIKE '%undo%')
  ORDER BY created_at DESC;
" 2>/dev/null || echo "(none found)"

echo ""
echo "--- Error/Debug Patterns ---"
sqlite3 "$LCM_DB" "
  SELECT substr(content, 1, 250) || ' [' || created_at || ']'
  FROM messages
  WHERE role = 'assistant'
    AND created_at >= '$SINCE'
    AND (LOWER(content) LIKE '%error:%' 
         OR LOWER(content) LIKE '%failed%'
         OR LOWER(content) LIKE '%broken%'
         OR LOWER(content) LIKE '%mistake%'
         OR LOWER(content) LIKE '%sorry%'
         OR LOWER(content) LIKE '%apolog%')
  ORDER BY created_at DESC
  LIMIT 10;
" 2>/dev/null || echo "(none found)"

echo ""
echo "=== Existing Active Learnings (check for matches) ==="
sqlite3 -box "$DB" "
  SELECT code, category, title, repeat_count 
  FROM learnings 
  WHERE tier='active'
  ORDER BY category, repeat_count DESC;
" 2>/dev/null

echo ""
echo "To log a new learning: learning-log.sh <category> <title> [root_cause] [prevention]"
