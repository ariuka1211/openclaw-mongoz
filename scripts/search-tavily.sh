#!/bin/bash
# Tavily search wrapper - outputs JSON results
# Usage: search-tavily.sh "query" [max_results]

TAVILY_KEY="${TAVILY_API_KEY:-<REDACTED>-WBSQ0KZ15yQVMXqOfCUETyviccXL4jsoJyWHHuvuUi}"
QUERY="${1:-}"
MAX_RESULTS="${2:-5}"

if [ -z "$QUERY" ]; then
  echo '{"error": "Usage: search-tavily.sh \"query\" [max_results]"}'
  exit 1
fi

curl -s -X POST "https://api.tavily.com/search" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TAVILY_KEY" \
  -d "{\"query\": $(echo "$QUERY" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'), \"max_results\": $MAX_RESULTS, \"search_depth\": \"basic\"}"
