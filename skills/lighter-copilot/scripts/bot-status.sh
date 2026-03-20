#!/bin/bash
# Check if the bot process is running
BOT_DIR="/root/.openclaw/workspace/lighter-copilot"

PID=$(pgrep -f "python3.*bot.py" 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "✅ Bot running (PID: $PID)"
    echo ""
    tail -10 "$BOT_DIR/bot.log"
else
    echo "❌ Bot is NOT running"
    echo "Start with: bash $BOT_DIR/scripts/restart.sh"
fi
