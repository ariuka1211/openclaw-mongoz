#!/bin/bash
# Health check for AI Trader daemon
# Run via cron every 5 minutes

LOG_FILE="/root/.openclaw/workspace/signals/ai-trader/logs/ai-trader.log"
DB_FILE="/root/.openclaw/workspace/signals/ai-trader/state/trader.db"
MAX_AGE_SECONDS=600  # 10 minutes

# Check if log exists and is recent
if [ ! -f "$LOG_FILE" ]; then
    echo "❌ AI Trader log not found"
    exit 1
fi

LAST_MODIFIED=$(stat -c %Y "$LOG_FILE" 2>/dev/null || stat -f %m "$LOG_FILE" 2>/dev/null)
NOW=$(date +%s)
AGE=$((NOW - LAST_MODIFIED))

if [ "$AGE" -gt "$MAX_AGE_SECONDS" ]; then
    echo "⚠️ AI Trader log stale (${AGE}s old, max ${MAX_AGE_SECONDS}s)"
    # Check if service is running
    if systemctl is-active --quiet ai-trader 2>/dev/null; then
        echo "Service reports active but log is stale. Restarting..."
        systemctl restart ai-trader
    fi
    exit 1
fi

# Check SQLite integrity
if [ -f "$DB_FILE" ]; then
    INTEGRITY=$(sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null)
    if [ "$INTEGRITY" != "ok" ]; then
        echo "❌ SQLite integrity check failed: $INTEGRITY"
        # Restore from backup
        BAK_FILE="${DB_FILE}.bak"
        if [ -f "$BAK_FILE" ]; then
            echo "Restoring from backup..."
            cp "$BAK_FILE" "$DB_FILE"
        fi
        exit 1
    fi
fi

echo "✅ AI Trader healthy (log age: ${AGE}s)"
exit 0
