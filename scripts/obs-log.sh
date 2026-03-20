#!/bin/bash
# obs-log.sh — Structured JSON logging for workspace scripts
# Usage: obs-log.sh <level> <source> <message> [key=value ...]
# Levels: debug, info, warn, error, critical
# Example: obs-log.sh error memory-daily "LLM call failed" attempt=3 model=gemini-2.5-flash
set -euo pipefail

LOG_DIR="/tmp/openclaw"
LOG_FILE="$LOG_DIR/obs.log"
mkdir -p "$LOG_DIR"

LEVEL="${1:-info}"
SOURCE="${2:-unknown}"
MESSAGE="${3:-}"
shift 3 2>/dev/null || true

# Build JSON
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
EXTRA=""
for arg in "$@"; do
  [ -z "$arg" ] && continue  # skip empty args
  KEY="${arg%%=*}"
  VAL="${arg#*=}"
  EXTRA="$EXTRA,\"$KEY\":\"$VAL\""
done

echo "{\"ts\":\"$TS\",\"level\":\"$LEVEL\",\"source\":\"$SOURCE\",\"msg\":\"$MESSAGE\"$EXTRA}" >> "$LOG_FILE"

# Echo to stdout if interactive
if [[ -t 1 ]]; then
  echo "[$LEVEL] $SOURCE: $MESSAGE"
fi
