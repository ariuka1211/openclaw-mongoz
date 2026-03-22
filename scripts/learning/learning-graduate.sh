#!/usr/bin/env bash
# learning-graduate.sh — Auto-promote/reduce learning tiers based on time
# Active → Watch after 14 days no repeat
# Watch → Retired after 30 days no repeat
# Any repeat within tier period → stays at current tier
# Runs every 3 days via cron

set -euo pipefail

DB="/root/.openclaw/workspace/data/freddy.db"
LEARNINGS_MD="/root/.openclaw/workspace/LEARNINGS.md"
TODAY=$(TZ='America/Denver' date +%Y-%m-%d)
D14=$(TZ='America/Denver' date -d "14 days ago" +"%Y-%m-%d" 2>/dev/null || TZ='America/Denver' date -v-14d +"%Y-%m-%d")
D30=$(TZ='America/Denver' date -d "30 days ago" +"%Y-%m-%d" 2>/dev/null || TZ='America/Denver' date -v-30d +"%Y-%m-%d")

CHANGES=0

# Active → Watch (14+ days no trigger)
ACTIVE_TO_WATCH=$(sqlite3 "$DB" "
  SELECT code FROM learnings 
  WHERE tier='active' AND last_triggered <= '$D14';
")

for code in $ACTIVE_TO_WATCH; do
  sqlite3 "$DB" "
    UPDATE learnings SET tier='watch', updated_at=datetime('now') 
    WHERE code='$code';
  "
  echo "🔴→🟡 $code (14+ days clean)"
  CHANGES=$((CHANGES + 1))
done

# Watch → Retired (30+ days no trigger)
WATCH_TO_RETIRED=$(sqlite3 "$DB" "
  SELECT code FROM learnings 
  WHERE tier='watch' AND last_triggered <= '$D30';
")

for code in $WATCH_TO_RETIRED; do
  sqlite3 "$DB" "
    UPDATE learnings SET tier='retired', graduated_at='$TODAY', updated_at=datetime('now') 
    WHERE code='$code';
  "
  echo "🟡→🟢 $code (30+ days clean, graduated)"
  CHANGES=$((CHANGES + 1))
done

if [ "$CHANGES" -eq 0 ]; then
  echo "No tier changes needed."
else
  echo ""
  echo "Updated $CHANGES learnings. Syncing LEARNINGS.md..."
  
  # Regenerate LEARNINGS.md from DB
  cat > "$LEARNINGS_MD" <<'HEADER'
# LEARNINGS.md — Don't Repeat These 🔴

**After any mistake:** Log it here immediately. Increment repeat counter if it matches an existing pattern.

**Graduation tiers:** 🔴 Active → 🟡 Watch (14+ days no repeat) → 🟢 Retired (30+ days, archived)

**Before acting:** See Pre-Action Protocol in AGENTS.md for category mappings.

---

HEADER

  # Active tier
  ACTIVE_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM learnings WHERE tier='active';")
  if [ "$ACTIVE_COUNT" -gt 0 ]; then
    echo "## 🔴 Active" >> "$LEARNINGS_MD"
    echo "" >> "$LEARNINGS_MD"
    sqlite3 "$DB" "
      SELECT '### ' || code || ': ' || title || '
- **Instances:** ' || COALESCE(root_cause, 'N/A') || '
- **Pattern:** `' || COALESCE(pattern, category) || '`
- **Prevention:** ' || COALESCE(prevention, 'N/A') || '
- **Repeats:** ' || repeat_count || ' | **Last:** ' || last_triggered || '
'
      FROM learnings WHERE tier='active'
      ORDER BY category, repeat_count DESC;
    " >> "$LEARNINGS_MD" 2>/dev/null
  fi

  # Watch tier
  WATCH_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM learnings WHERE tier='watch';")
  if [ "$WATCH_COUNT" -gt 0 ]; then
    echo "## 🟡 Watch" >> "$LEARNINGS_MD"
    echo "" >> "$LEARNINGS_MD"
    sqlite3 "$DB" "
      SELECT '- **' || code || ':** ' || title || ' (last: ' || last_triggered || ', repeats: ' || repeat_count || ')'
      FROM learnings WHERE tier='watch'
      ORDER BY last_triggered DESC;
    " >> "$LEARNINGS_MD" 2>/dev/null
    echo "" >> "$LEARNINGS_MD"
  fi

  # Retired tier (collapsed)
  RETIRED_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM learnings WHERE tier='retired';")
  if [ "$RETIRED_COUNT" -gt 0 ]; then
    echo "## 🟢 Retired" >> "$LEARNINGS_MD"
    echo "" >> "$LEARNINGS_MD"
    sqlite3 "$DB" "
      SELECT '- **' || code || ':** ' || title || ' (graduated: ' || COALESCE(graduated_at, 'N/A') || ')'
      FROM learnings WHERE tier='retired'
      ORDER BY graduated_at DESC;
    " >> "$LEARNINGS_MD" 2>/dev/null
    echo "" >> "$LEARNINGS_MD"
  fi
  
  echo "LEARNINGS.md synced."
fi
