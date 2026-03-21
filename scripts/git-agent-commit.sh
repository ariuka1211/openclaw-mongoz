#!/bin/bash
# Git commit helper for agents
# Usage: ./scripts/git-agent-commit.sh <agent-id> <message> [files...]
#
# Examples:
#   ./scripts/git-agent-commit.sh mzinho "fix: add cooldown to bot.py" executor/bot.py
#   ./scripts/git-agent-commit.sh mzinho "fix: add cooldown" executor/bot.py executor/dsl.py
#   ./scripts/git-agent-commit.sh mzinho "chore: update config" --all    (stage everything)

set -e

AGENT_ID="$1"
MESSAGE="$2"
shift 2
FILES=("$@")
WORKSPACE="/root/.openclaw/workspace"

if [[ -z "$AGENT_ID" || -z "$MESSAGE" ]]; then
    echo "Usage: $0 <agent-id> <message> [files...]"
    echo "  Stage only specific files (recommended):"
    echo "    $0 mzinho 'fix: description' path/to/file.py"
    echo "  Stage everything (use sparingly):"
    echo "    $0 mzinho 'big refactor' --all"
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
    maaraa)
        git config user.name "maaraa"
        git config user.email "maaraa@openclaw.local"
        ;;
    *)
        echo "Unknown agent: $AGENT_ID (valid: mzinho, techno4k, blitz, maaraa)"
        exit 1
        ;;
esac

# Stage files
if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "ERROR: No files specified. List the files you changed:"
    echo "  $ $0 $AGENT_ID '$MESSAGE' <file1> <file2> ..."
    echo ""
    echo "Or use --all to stage everything (rarely needed)."
    exit 1
elif [[ "${FILES[0]}" == "--all" ]]; then
    git add -A
    echo "Staged ALL changes"
else
    git add "${FILES[@]}"
    echo "Staged: ${FILES[*]}"
fi

# Check if anything is staged
if git diff --cached --quiet; then
    echo "Nothing staged. Make sure the files exist and have changes."
    exit 1
fi

# Commit
git commit -m "[$AGENT_ID] $MESSAGE"

echo "✓ Committed as $AGENT_ID: $MESSAGE"
echo "  Files staged (review before Maaraa pushes):"
git diff --cached --stat