#!/usr/bin/env bash
# Nightly memory cleanup
# - Archives daily memory files older than 2 days
# - Promotes key items to MEMORY.md
# - Reports summary

set -euo pipefail

WORKSPACE="/root/.openclaw/workspace"
MEMORY_DIR="$WORKSPACE/memory"
ARCHIVE_DIR="$MEMORY_DIR/archive"
MEMORY_MD="$WORKSPACE/MEMORY.md"
TODAY=$(date +%Y-%m-%d)
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

mkdir -p "$ARCHIVE_DIR"

promoted=0
archived=0
lines_removed=0

# Find daily files (NNNN-NN-NN.md) older than yesterday
for f in "$MEMORY_DIR"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md; do
  [ -f "$f" ] || continue
  basename_f=$(basename "$f" .md)
  
  # Skip today and yesterday
  if [[ "$basename_f" == "$TODAY" || "$basename_f" == "$YESTERDAY" ]]; then
    continue
  fi

  lines=$(wc -l < "$f")
  
  # Check if file has substantive content (>10 lines) — if so, archive
  if [ "$lines" -gt 5 ]; then
    # Promote: extract headings as notable items to MEMORY.md
    headings=$(grep '^## ' "$f" 2>/dev/null || true)
    if [ -n "$headings" ]; then
      {
        echo ""
        echo "<!-- promoted from memory/$basename_f.md on $TODAY -->"
        echo "### Archived: $basename_f"
        echo "$headings"
        echo ""
      } >> "$MEMORY_MD"
      promoted=$((promoted + 1))
    fi
    
    # Move to archive
    mv "$f" "$ARCHIVE_DIR/"
    archived=$((archived + 1))
    lines_removed=$((lines_removed + lines))
  else
    # Small file, just remove
    rm "$f"
    lines_removed=$((lines_removed + lines))
  fi
done

# Also clean topic-named files older than 7 days
CUTOFF=$(date -d "7 days ago" +%Y-%m-%d)
for f in "$MEMORY_DIR"/*.md; do
  [ -f "$f" ] || continue
  basename_f=$(basename "$f")
  # Skip regular daily files, session.md, and recent topic files
  [[ "$basename_f" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\.md$ ]] && continue
  [[ "$basename_f" == "session.md" ]] && continue
  
  # Check if it starts with a date pattern YYYY-MM-DD-
  if [[ "$basename_f" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})- ]]; then
    file_date="${BASH_REMATCH[1]}"
    if [[ "$file_date" < "$CUTOFF" ]]; then
      lines=$(wc -l < "$f")
      # Promote key findings
      headings=$(grep '^## ' "$f" 2>/dev/null || true)
      if [ -n "$headings" ]; then
        {
          echo ""
          echo "<!-- promoted from memory/$basename_f on $TODAY -->"
          echo "### Archived: $basename_f"
          echo "$headings"
          echo ""
        } >> "$MEMORY_MD"
        promoted=$((promoted + 1))
      fi
      mv "$f" "$ARCHIVE_DIR/"
      archived=$((archived + 1))
      lines_removed=$((lines_removed + lines))
    fi
  fi
done

# Remove stale promote comments older than 30 days from MEMORY.md
# (keep MEMORY.md lean)
STALE_CUTOFF=$(date -d "30 days ago" +%Y-%m-%d)
# We don't auto-prune MEMORY.md itself — just report

echo "=== Nightly Memory Cleanup Summary ==="
echo "Date: $TODAY"
echo "Files archived: $archived"
echo "Items promoted to MEMORY.md: $promoted"
echo "Lines removed from memory/: $lines_removed"
echo "Archive dir: $ARCHIVE_DIR"
echo "======================================"
