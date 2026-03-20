#!/usr/bin/env bash
# memory-lcm-cleanup.sh — Weekly LCM database cleanup
# Prunes orphaned data, rebuilds FTS, vacuums DB
# Run: weekly via system crontab

set -euo pipefail
LCM_DB="/root/.openclaw/lcm.db"
BACKUP_DIR="/root/.openclaw"
LOG_PREFIX="[LCM Cleanup]"

log() { echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') $*"; }

# Verify DB exists
[ ! -f "$LCM_DB" ] && { log "ERROR: lcm.db not found"; exit 1; }

# Create timestamped backup before cleanup
BACKUP_FILE="$BACKUP_DIR/lcm.db.bak.$(date +%Y%m%d)"
cp "$LCM_DB" "$BACKUP_FILE"
log "Backup created: $BACKUP_FILE"

# Initial counts
INITIAL_SUMMARIES=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM summaries;")
INITIAL_MESSAGES=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM messages;")
INITIAL_SIZE=$(du -h "$LCM_DB" | cut -f1)

log "Initial: $INITIAL_SUMMARIES summaries, $INITIAL_MESSAGES messages, $INITIAL_SIZE DB"

# 1. Delete orphaned context_items (referencing deleted conversations)
ORPHAN_CTX=$(sqlite3 "$LCM_DB" "
DELETE FROM context_items WHERE conversation_id NOT IN (SELECT conversation_id FROM conversations);
SELECT changes();
")
log "Deleted $ORPHAN_CTX orphaned context_items"

# 2. Delete orphaned summary_messages (no valid summary or message)
STALE_LINKS=$(sqlite3 "$LCM_DB" "
DELETE FROM summary_messages
WHERE summary_id NOT IN (SELECT summary_id FROM summaries)
   OR message_id NOT IN (SELECT message_id FROM messages);
SELECT changes();
")
log "Deleted $STALE_LINKS stale summary_links"

# 3. Delete ultra-thin summaries (<100 tokens, likely noise)
THIN=$(sqlite3 "$LCM_DB" "
DELETE FROM summaries_fts WHERE rowid IN (SELECT rowid FROM summaries WHERE token_count < 100);
DELETE FROM summary_parents WHERE summary_id IN (SELECT summary_id FROM summaries WHERE token_count < 100);
DELETE FROM summary_messages WHERE summary_id IN (SELECT summary_id FROM summaries WHERE token_count < 100);
DELETE FROM summaries WHERE token_count < 100;
SELECT changes();
")
log "Deleted $THIN ultra-thin summaries (<100 tokens)"

# 4. Delete orphaned summaries (no messages linked, kind=leaf, older than 3 days)
#    These are leaf summaries whose raw data is gone and no child references them
#    Only delete if >3 days old and <500 tokens to preserve quality context
OLD_ORPHANS=$(sqlite3 "$LCM_DB" "
DELETE FROM summaries_fts WHERE rowid IN (
  SELECT rowid FROM summaries WHERE summary_id IN (
    SELECT s.summary_id FROM summaries s
    LEFT JOIN summary_messages sm ON sm.summary_id = s.summary_id
    WHERE sm.message_id IS NULL
      AND s.kind = 'leaf'
      AND s.created_at < datetime('now', '-3 days')
      AND s.token_count < 500
  )
);
DELETE FROM summary_parents WHERE summary_id IN (
  SELECT s.summary_id FROM summaries s
  LEFT JOIN summary_messages sm ON sm.summary_id = s.summary_id
  WHERE sm.message_id IS NULL
    AND s.kind = 'leaf'
    AND s.created_at < datetime('now', '-3 days')
    AND s.token_count < 500
);
DELETE FROM summaries WHERE summary_id IN (
  SELECT s.summary_id FROM summaries s
  LEFT JOIN summary_messages sm ON sm.summary_id = s.summary_id
  WHERE sm.message_id IS NULL
    AND s.kind = 'leaf'
    AND s.created_at < datetime('now', '-3 days')
    AND s.token_count < 500
);
SELECT changes();
")
log "Deleted $OLD_ORPHANS old thin orphaned summaries"

# 7. Rebuild FTS indexes
sqlite3 "$LCM_DB" "INSERT INTO summaries_fts(summaries_fts) VALUES('rebuild');"
sqlite3 "$LCM_DB" "INSERT INTO messages_fts(messages_fts) VALUES('rebuild');" 2>/dev/null || true
log "FTS indexes rebuilt"

# 8. Vacuum
sqlite3 "$LCM_DB" "VACUUM;"
log "Database vacuumed"

# Final counts
FINAL_SUMMARIES=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM summaries;")
FINAL_MESSAGES=$(sqlite3 "$LCM_DB" "SELECT COUNT(*) FROM messages;")
FINAL_SIZE=$(du -h "$LCM_DB" | cut -f1)
FINAL_TOKENS=$(sqlite3 "$LCM_DB" "SELECT COALESCE(SUM(token_count), 0) FROM summaries;")

log "Final: $FINAL_SUMMARIES summaries ($FINAL_TOKENS tok), $FINAL_MESSAGES messages, $FINAL_SIZE DB"

# Clean up old backups (keep last 4 weeks)
ls -t "$BACKUP_DIR"/lcm.db.bak.* 2>/dev/null | tail -n +5 | xargs -r rm
log "Old backups pruned (keeping last 4)"

log "✅ Cleanup complete"
