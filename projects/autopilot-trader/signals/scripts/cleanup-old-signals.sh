#!/bin/bash
# cleanup-old-signals.sh — Remove opportunities older than 20 minutes from signals.json
# Fallback method if scanner cleanup isn't running
set -e

cd /root/.openclaw/workspace/projects/autopilot-trader/signals

if [ ! -f signals.json ]; then
  echo "❌ signals.json not found"
  exit 1
fi

python3 -c "
import json
from datetime import datetime, timedelta, timezone

with open('signals.json') as f:
    data = json.load(f)

before = len(data.get('opportunities', []))
cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)

filtered = []
for o in data.get('opportunities', []):
    detected = o.get('detectedAt')
    if not detected:
        filtered.append(o)  # Keep legacy entries without timestamp
        continue
    try:
        dt = datetime.fromisoformat(detected.replace('Z', '+00:00'))
        if dt > cutoff:
            filtered.append(o)
    except:
        filtered.append(o)  # Keep if can't parse

data['opportunities'] = filtered
with open('signals.json', 'w') as f:
    json.dump(data, f, indent=2)

removed = before - len(filtered)
print(f'✅ Cleaned: {len(filtered)} signals kept, {removed} removed (>{20}min old)')
"
