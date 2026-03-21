#!/bin/bash
# Open a market position
# Usage: bash open-position.sh <symbol> <side> <size_usdc> [leverage]
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="/root/.openclaw/workspace/lighter-copilot/venv"
"$VENV/bin/python3" "$SCRIPT_DIR/open_position.py" "$@"
