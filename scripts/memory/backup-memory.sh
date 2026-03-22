#!/usr/bin/env bash
# Backup Memory — copies MEMORY.md + memory/ to a timestamped backup dir
set -euo pipefail

WORKSPACE="/root/.openclaw/workspace"
BACKUP_DIR="/root/.openclaw/backups/memory"
TIMESTAMP=$(TZ='America/Denver' date +"%Y-%m-%dT%H%M%S")
DEST="$BACKUP_DIR/$TIMESTAMP"

mkdir -p "$DEST"

# Copy MEMORY.md if it exists
if [ -f "$WORKSPACE/MEMORY.md" ]; then
  cp "$WORKSPACE/MEMORY.md" "$DEST/MEMORY.md"
fi

# Copy memory/ directory if it exists
if [ -d "$WORKSPACE/memory" ]; then
  cp -r "$WORKSPACE/memory/" "$DEST/memory/"
fi

# Keep only the last 14 backups
cd "$BACKUP_DIR"
ls -1dt */ 2>/dev/null | tail -n +15 | xargs -r rm -rf

echo "[$(TZ='America/Denver' date +"%Y-%m-%d %H:%M:%S %Z")] Backup complete → $DEST"
touch /tmp/cron-state/backup.last-run
