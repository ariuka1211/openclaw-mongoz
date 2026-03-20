#!/usr/bin/env bash
# learning-log.sh — Log a new learning/mistake to DB + LEARNINGS.md
# Usage: learning-log.sh <category> <title> [root_cause] [prevention] [pattern]
# Example: learning-log.sh deploy "Restart without verification" "Assumed restart = working" "curl localhost:PORT after restart" "deploy"

set -euo pipefail

DB="/root/.openclaw/workspace/data/freddy.db"
CATEGORY="${1:-}"
TITLE="${2:-}"
ROOT_CAUSE="${3:-}"
PREVENTION="${4:-}"
PATTERN="${5:-$CATEGORY}"
TODAY=$(date -u +%Y-%m-%d)

if [ -z "$CATEGORY" ] || [ -z "$TITLE" ]; then
  echo "Usage: learning-log.sh <category> <title> [root_cause] [prevention] [pattern]"
  echo "Categories: deploy, config, debug, spawn, think, approval, knowledge"
  exit 1
fi

# Generate next code
LAST_NUM=$(sqlite3 "$DB" "SELECT MAX(CAST(SUBSTR(code, 2) AS INTEGER)) FROM learnings WHERE code LIKE 'L%';" 2>/dev/null)
LAST_NUM=${LAST_NUM:-0}
NEXT_NUM=$((LAST_NUM + 1))
CODE=$(printf "L%03d" $NEXT_NUM)

# Check for similar existing entry (fuzzy match on title)
SIMILAR=$(sqlite3 "$DB" "
  SELECT code, repeat_count FROM learnings 
  WHERE category='$CATEGORY' AND tier != 'retired'
    AND (title LIKE '%$(echo "$TITLE" | cut -d' ' -f1-2)%' 
         OR LOWER(title) LIKE LOWER('%${TITLE:0:20}%'))
  LIMIT 1;
" 2>/dev/null)

if [ -n "$SIMILAR" ]; then
  IFS='|' read -r existing_code existing_count <<< "$SIMILAR"
  NEW_COUNT=$((existing_count + 1))
  
  # Update existing: increment repeat, reset tier to active, update last_triggered
  sqlite3 "$DB" "
    UPDATE learnings 
    SET repeat_count=$NEW_COUNT, last_triggered='$TODAY', tier='active', updated_at=datetime('now')
    WHERE code='$existing_code';
  "
  echo "⚠️ Similar to $existing_code — incremented repeats to $NEW_COUNT, reset to 🔴 active"
  CODE=$existing_code
else
  # Insert new learning
  sqlite3 "$DB" "
    INSERT INTO learnings (code, category, title, root_cause, prevention, pattern, repeat_count, first_seen, last_triggered, tier)
    VALUES ('$CODE', '$CATEGORY', '$TITLE', '$ROOT_CAUSE', '$PREVENTION', '$PATTERN', 1, '$TODAY', '$TODAY', 'active');
  "
  echo "✅ Logged $CODE: $TITLE (category: $CATEGORY)"
fi

# Sync LEARNINGS.md (run graduation script which also syncs)
bash /root/.openclaw/workspace/scripts/learning-graduate.sh 2>/dev/null || true
