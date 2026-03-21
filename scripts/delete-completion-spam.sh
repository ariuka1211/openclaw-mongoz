#!/bin/bash
# Delete completion notification spam messages
TOKEN="8677962428:AAEHnCJsk3g0YXfpXfvwU06-IMx8IbYIbi8"
CHAT_ID="1736401643"

deleted=0
checked=0

# Patterns to match
patterns=(
    "Sub-Agent"
    "Subagent.*complete"
    "✅.*complete"
    "Done\."
    "Task complete"
    "session complete"
    "✅.*Subagent"
    "✅.*task"
)

# Check message range from recent logs
for ((mid=5000; mid<=6400; mid++)); do
    checked=$((checked + 1))
    
    # Forward to read content
    result=$(curl -s "https://api.telegram.org/bot${TOKEN}/forwardMessage" \
        -d "chat_id=${CHAT_ID}" \
        -d "from_chat_id=${CHAT_ID}" \
        -d "message_id=${mid}" 2>/dev/null)
    
    ok=$(echo "$result" | jq -r '.ok' 2>/dev/null)
    [ "$ok" != "true" ] && continue
    
    fwd_id=$(echo "$result" | jq -r '.result.message_id' 2>/dev/null)
    is_bot=$(echo "$result" | jq -r '.result.forward_from.is_bot // "false"' 2>/dev/null)
    text=$(echo "$result" | jq -r '.result.text // ""' 2>/dev/null)
    
    # Delete forwarded copy
    curl -s "https://api.telegram.org/bot${TOKEN}/deleteMessage" \
        -d "chat_id=${CHAT_ID}" -d "message_id=${fwd_id}" >/dev/null 2>&1
    
    # Skip user messages
    [ "$is_bot" != "true" ] && continue
    
    # Check if text matches any completion pattern
    match=false
    for pattern in "${patterns[@]}"; do
        if echo "$text" | grep -qi "$pattern"; then
            match=true
            break
        fi
    done
    
    if [ "$match" = "true" ]; then
        # Delete original
        del_result=$(curl -s "https://api.telegram.org/bot${TOKEN}/deleteMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "message_id=${mid}" 2>/dev/null)
        del_ok=$(echo "$del_result" | jq -r '.ok' 2>/dev/null)
        if [ "$del_ok" = "true" ]; then
            deleted=$((deleted + 1))
            echo "✅ Deleted ID ${mid}: ${text:0:60}..."
        fi
    fi
    
    # Progress
    if (( checked % 100 == 0 )); then
        echo "Progress: ${checked} checked, ${deleted} deleted"
    fi
    
    sleep 0.04
done

echo "Final: ${checked} checked, ${deleted} deleted"