#!/bin/bash
# Start the Lighter Bot Dashboard
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../.." || exit 1
exec python3 -m signals.dashboard.app
