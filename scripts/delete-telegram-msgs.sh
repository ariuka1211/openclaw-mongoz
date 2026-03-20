#!/bin/bash
# Delete bot messages matching a text pattern from a DM chat
# Uses forwardMessage to check content, preserves user messages

TOKEN="8677962428:AAEHnCJsk3g0YXfpXfvwU06-IMx8IbYIbi8"
CHAT_ID="${1:-1736401643}"
PATTERN="${2:-Sub-Agent}"

deleted=0
checked=0
not_found=0
user_msgs=0
non_match=0

echo "Chat: ${CHAT_ID}"
echo "Pattern: ${PATTERN}"
echo ""

# First, get current offset to know the message ID range
# Get latest updates to find high water mark
UPDATES=$(curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates?limit=1&allowed_updates=[]" 2>/dev/null)
LAST_UPDATE_ID=$(echo "$UPDATES" | jq -r '.result[-1].update_id // 0' 2>/dev/null)

# Known DM message IDs from logs (today + yesterday)
# We'll check these plus gaps between them
DM_IDS=$(for f in /tmp/openclaw/openclaw-2026-03-{18,19}.log; do
    [ -f "$f" ] && grep "sendMessage ok.*chat=${CHAT_ID}" "$f" 2>/dev/null
done | grep -oP 'message=(\d+)' | sed 's/message=//' | sort -n | uniq)

# Also add all IDs in a reasonable range to catch unlogged messages
# DM messages in Telegram are sequential per chat, but IDs are global
# So there will be gaps. Let's check all IDs from min-100 to max+100
MIN_ID=$(echo "$DM_IDS" | head -1)
MAX_ID=$(echo "$DM_IDS" | tail -1)
START=$((MIN_ID - 50))
END=$((MAX_ID + 200))

echo "Scanning range: ${START} → ${END}"
echo "Known DM IDs: $(echo "$DM_IDS" | wc -l)"
echo ""

for ((mid=START; mid<=END; mid++)); do
    # Skip if this ID is in a known gap (>50 from previous known DM ID)
    # Actually, just try all of them - Telegram will return error for non-existent
    checked=$((checked + 1))
    
    # Forward to same chat to read content
    forward_result=$(curl -s "https://api.telegram.org/bot${TOKEN}/forwardMessage" \
        -d "chat_id=${CHAT_ID}" \
        -d "from_chat_id=${CHAT_ID}" \
        -d "message_id=${mid}" 2>/dev/null)
    
    ok=$(echo "$forward_result" | jq -r '.ok' 2>/dev/null)
    
    if [ "$ok" != "true" ]; then
        not_found=$((not_found + 1))
        # If 3 consecutive not found, we might be past the end
        continue
    fi
    
    # Get forwarded message ID
    fwd_mid=$(echo "$forward_result" | jq -r '.result.message_id' 2>/dev/null)
    
    # Check if original was from the bot
    is_bot=$(echo "$forward_result" | jq -r '.result.forward_from.is_bot // "false"' 2>/dev/null)
    
    # Get message text
    text=$(echo "$forward_result" | jq -r '.result.text // ""' 2>/dev/null)
    
    # Delete the forwarded copy immediately
    curl -s "https://api.telegram.org/bot${TOKEN}/deleteMessage" \
        -d "chat_id=${CHAT_ID}" \
        -d "message_id=${fwd_mid}" >/dev/null 2>&1
    
    if [ "$is_bot" != "true" ]; then
        user_msgs=$((user_msgs + 1))
        sleep 0.03
        continue
    fi
    
    # Check if text matches pattern
    if echo "$text" | grep -qi "$PATTERN"; then
        # Delete the original
        del_result=$(curl -s "https://api.telegram.org/bot${TOKEN}/deleteMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "message_id=${mid}" 2>/dev/null)
        del_ok=$(echo "$del_result" | jq -r '.ok' 2>/dev/null)
        if [ "$del_ok" = "true" ]; then
            deleted=$((deleted + 1))
            echo -ne "\r✅ Deleted: ${deleted} | Checked: ${checked} | ID: ${mid}       "
        fi
    else
        non_match=$((non_match + 1))
    fi
    
    # Progress every 50
    if (( checked % 50 == 0 )); then
        echo -ne "\rChecked: ${checked} | Deleted: ${deleted} | Not found: ${not_found} | User: ${user_msgs}   "
    fi
    
    # Rate limit: 2 API calls per iteration
    sleep 0.05
done

echo ""
echo "---"
echo "Done. Checked: ${checked}, Deleted: ${deleted}, Not found: ${not_found}, User msgs: ${user_msgs}, Bot non-match: ${non_match}"
