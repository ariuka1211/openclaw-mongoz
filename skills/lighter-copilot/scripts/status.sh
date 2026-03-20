#!/bin/bash
# Check account status, positions, and P&L
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
VENV="/root/.openclaw/workspace/lighter-copilot/venv"
"$VENV/bin/python3" "$SCRIPT_DIR/status.py" "$@"
