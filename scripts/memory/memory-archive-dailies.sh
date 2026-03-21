#!/bin/bash
# memory-archive-dailies.sh — Weekly archival of old daily memory files
#
# - Moves daily files older than 7 days → memory/archived/
# - Deletes archived files older than 30 days
#
# Runs weekly via cron.
set -euo pipefail

W="/root/.openclaw/workspace"
DAILY_DIR="$W/memory/daily"
ARCHIVE_DIR="$W/memory/archived"

mkdir -p "$ARCHIVE_DIR"

moved=0
deleted=0

# Move daily files older than 7 days to archive
while IFS= read -r f; do
  [ -f "$f" ] || continue
  mv "$f" "$ARCHIVE_DIR/"
  (( moved++ ))
done < <(find "$DAILY_DIR" -maxdepth 1 -name '*.md' -mtime +7 -type f 2>/dev/null)

# Delete archived files older than 30 days
while IFS= read -r f; do
  [ -f "$f" ] || continue
  rm -f "$f"
  (( deleted++ ))
done < <(find "$ARCHIVE_DIR" -name '*.md' -mtime +30 -type f 2>/dev/null)

echo "Archive: moved=$moved deleted=$deleted"

# Cron: memory-archive-weekly (Sunday 06:00 UTC)
