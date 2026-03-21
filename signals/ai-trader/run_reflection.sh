#!/usr/bin/env bash
# Run reflection agent — logs output to logs/reflection.log
# Schedule via cron: */30 * * * * /root/.openclaw/workspace/signals/ai-trader/run_reflection.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs

LOG_FILE="logs/reflection.log"

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ===" >> "$LOG_FILE"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

python3 reflection.py >> "$LOG_FILE" 2>&1
echo "Exit code: $?" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
