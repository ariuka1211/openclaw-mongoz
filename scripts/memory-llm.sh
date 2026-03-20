#!/bin/bash
# memory-llm.sh — OpenRouter API wrapper for memory operations
# Usage: echo "prompt" | memory-llm.sh [system_prompt] [model] [max_tokens]
set -euo pipefail

API_KEY="${OPENROUTER_API_KEY:-}"
MODEL="${2:-google/gemini-2.5-flash}"
MAX_TOKENS="${3:-4096}"

[ -z "$API_KEY" ] && echo '{"error": "OPENROUTER_API_KEY not set"}' >&2 && exit 1

USER_PROMPT=$(cat)
SYSTEM_PROMPT="${1:-You are a memory extraction assistant. Respond only with valid JSON.}"

PAYLOAD=$(python3 -c "
import json, sys
payload = {
    'model': '$MODEL',
    'max_tokens': $MAX_TOKENS,
    'messages': [
        {'role': 'system', 'content': sys.argv[1]},
        {'role': 'user', 'content': sys.stdin.read()}
    ]
}
print(json.dumps(payload))
" "$SYSTEM_PROMPT" <<< "$USER_PROMPT")

RESPONSE=$(curl -s -X POST "https://openrouter.ai/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d "$PAYLOAD" \
  --max-time 60)

echo "$RESPONSE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if 'error' in data:
        print(json.dumps(data), file=sys.stderr)
        sys.exit(1)
    content = data['choices'][0]['message']['content']
    content = content.strip()
    if content.startswith('\`\`\`'):
        lines = content.split('\n')
        if lines[0].startswith('\`\`\`json'):
            lines = lines[1:]
        elif lines[0].startswith('\`\`\`'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '\`\`\`':
            lines = lines[:-1]
        content = '\n'.join(lines)
    print(content)
except Exception as e:
    print(f'Parse error: {e}', file=sys.stderr)
    sys.exit(1)
"
