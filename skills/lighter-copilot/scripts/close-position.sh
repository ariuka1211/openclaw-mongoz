#!/bin/bash
# Close an open position
# Usage: bash close-position.sh <symbol>
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="/root/.openclaw/workspace/lighter-copilot/venv"
"$VENV/bin/python3" "$SCRIPT_DIR/close_position.py" "$@"
