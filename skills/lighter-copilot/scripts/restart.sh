#!/bin/bash
# Restart the Lighter Copilot bot
set -e
BOT_DIR="/root/.openclaw/workspace/lighter-copilot"

echo "🔄 Stopping old bot..."
pkill -f "python3.*bot.py" 2>/dev/null || true
sleep 2

echo "🚀 Starting bot..."
cd "$BOT_DIR"
source venv/bin/activate
nohup python3 bot.py > bot.log 2>&1 &
PID=$!
echo "$PID" > bot.pid
echo "✅ Bot started (PID: $PID)"

sleep 3
echo ""
echo "Recent log:"
tail -5 bot.log
