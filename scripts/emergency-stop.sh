#!/bin/bash
# Emergency kill switch - stops all Maaraa's running processes
echo "🔴 EMERGENCY STOP triggered"

# Kill common script patterns
pkill -9 -f "delete-.*\.sh"
pkill -9 -f "search-.*\.sh" 
pkill -9 -f "memory-.*\.sh"
pkill -9 -f "lighter-copilot"

# Kill any bash processes from workspace
for pid in $(pgrep -f "/root/.openclaw/workspace"); do
    kill -9 "$pid" 2>/dev/null
done

echo "✅ All processes terminated"