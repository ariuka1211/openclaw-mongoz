#!/bin/bash
# Git commit helper for agents
# Usage: ./scripts/git-agent-commit.sh <agent-id> <message>

set -e

AGENT_ID="$1"
MESSAGE="$2"
WORKSPACE="/root/.openclaw/workspace"

if [[ -z "$AGENT_ID" || -z "$MESSAGE" ]]; then
    echo "Usage: $0 <agent-id> <message>"
    echo "Example: $0 mzinho 'fix: add cooldown to bot.py'"
    exit 1
fi

cd "$WORKSPACE"

# Set git user based on agent
case "$AGENT_ID" in
    mzinho)
        git config user.name "mzinho"
        git config user.email "mzinho@openclaw.local"
        ;;
    techno4k)
        git config user.name "techno4k" 
        git config user.email "techno4k@openclaw.local"
        ;;
    blitz)
        git config user.name "blitz"
        git config user.email "blitz@openclaw.local"
        ;;
    *)
        echo "Unknown agent: $AGENT_ID"
        exit 1
        ;;
esac

# Commit with proper attribution
git add -A
git commit -m "[$AGENT_ID] $MESSAGE"

echo "✓ Committed as $AGENT_ID: $MESSAGE"