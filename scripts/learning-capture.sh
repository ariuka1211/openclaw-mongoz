#!/usr/bin/env bash
# learning-capture.sh — Scan recent session for potential mistakes to log
# Looks for correction patterns and surfaces proposals for the agent to review
# Output is proposals for the agent to review and log via learning-log.sh

set -euo pipefail

DB="/root/.openclaw/workspace/data/freddy.db"
HOURS="${1:-24}"

echo "=== Learning Capture (last ${HOURS}h) ==="
echo ""
echo "NOTE: LCM has been disabled. Session history is no longer available for scanning."
echo "Manually review recent conversations for mistakes and log via learning-log.sh."
echo ""

echo "=== Existing Active Learnings ==="
sqlite3 -box "$DB" "
  SELECT code, category, title, repeat_count 
  FROM learnings 
  WHERE tier='active'
  ORDER BY category, repeat_count DESC;
" 2>/dev/null || echo "(no learnings database found)"

echo ""
echo "To log a new learning: learning-log.sh <category> <title> [root_cause] [prevention]"
