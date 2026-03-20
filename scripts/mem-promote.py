#!/usr/bin/env python3
"""Promote items that appear in 3+ independent summaries (⭐ prefix).
Only scans Decisions, Lessons, Patterns sections."""
import sqlite3, re, sys, os

MEMORY_FILE = os.environ["MEMORY_FILE"]
LCM_DB = "/root/.openclaw/lcm.db"
MIN_SUMMARIES = 4
WORD_MATCH_RATIO = 0.7

with open(MEMORY_FILE) as f:
    content = f.read()

parts = re.split(r'(^## .+\[.*?\]\s*$)', content, flags=re.MULTILINE)
sections = {}
for i in range(len(parts)):
    if re.match(r'^## ', parts[i]):
        header = parts[i].strip()
        body_parts = []
        j = i + 1
        while j < len(parts) and not re.match(r'^## ', parts[j]):
            body_parts.append(parts[j])
            j += 1
        sections[header] = {"idx": i, "body": ''.join(body_parts)}

conn = sqlite3.connect(LCM_DB)
cur = conn.cursor()
cur.execute("""
    SELECT summary_id, content FROM summaries
    WHERE created_at > datetime('now', '-7 days')
    ORDER BY created_at DESC
""")
summaries = cur.fetchall()
conn.close()

if not summaries:
    print("No recent summaries to check")
    sys.exit(0)

SCAN_SECTIONS = ["## Decisions [decision]", "## Lessons [lesson]", "## Patterns [pattern]"]
stop_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
              'had', 'her', 'was', 'one', 'our', 'out', 'has', 'have', 'been',
              'from', 'that', 'this', 'with', 'were', 'they', 'each', 'what',
              'when', 'into', 'than', 'its', 'also', 'which', 'their', 'will',
              'more', 'some', 'just'}

promoted = []

for section in SCAN_SECTIONS:
    if section not in sections:
        continue
    body = sections[section]["body"]

    for line in body.strip().split('\n'):
        stripped = line.strip()
        if not stripped.startswith('-') or stripped.startswith('---'):
            continue
        if '⭐' in stripped:
            continue

        text = re.sub(r'^-\s*(\[[^\]]+\]\s*)?', '', stripped)
        if len(text) < 15:
            continue

        words = set(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', text)
                    if w.lower() not in stop_words)
        if len(words) < 3:
            continue

        matching_summaries = 0
        for sid, scontent in summaries:
            summary_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', scontent))
            overlap = len(words & summary_words)
            if overlap / len(words) >= WORD_MATCH_RATIO:
                matching_summaries += 1

        if matching_summaries >= MIN_SUMMARIES:
            promoted.append({"text": text.strip(), "section": section, "line": line})
            sections[section]["body"] = sections[section]["body"].replace(
                line, line.replace('- ', '- ⭐ ', 1), 1)

if not promoted:
    print("No items promoted")
    sys.exit(0)

output = []
i = 0
while i < len(parts):
    if re.match(r'^## ', parts[i]):
        header = parts[i].strip()
        if header in sections:
            output.append(parts[i])
            output.append(sections[header]["body"])
        else:
            output.append(parts[i])
        j = i + 1
        while j < len(parts) and not re.match(r'^## ', parts[j]):
            j += 1
        i = j
    else:
        output.append(parts[i])
        i += 1

with open(MEMORY_FILE, "w") as f:
    f.write(''.join(output))

print(f"Promoted {len(promoted)} items:")
for p in promoted:
    print(f"  ⭐ {p['text']}")
