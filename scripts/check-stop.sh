#!/bin/bash
# Check if user sent emergency stop
# Usage: check-stop.sh && exit 1

# Check recent Telegram messages for "STOP MAARAA"
TOKEN="8677962428:AAEHnCJsk3g0YXfpXfvwU06-IMx8IbYIbi8"
UPDATES=$(curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates?offset=-5" 2>/dev/null)

if echo "$UPDATES" | jq -r '.result[].message.text // ""' 2>/dev/null | grep -qi "STOP MAARAA"; then
    echo "🔴 Emergency stop detected"
    exit 0  # Stop condition met
fi

exit 1  # Continue running