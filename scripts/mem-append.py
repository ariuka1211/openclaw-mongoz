#!/usr/bin/env python3
"""Append LLM-extracted items to MEMORY.md with metadata (date, status, source, container)."""
import json, os

MEMORY_FILE = os.environ["MEMORY_FILE"]

RESPONSE_FILE = "/tmp/memory-llm-response.json"
if os.path.exists(RESPONSE_FILE):
    with open(RESPONSE_FILE) as f:
        data = json.load(f)
else:
    data = json.loads(os.environ.get("LLM_RESPONSE", "{}"))

TAG_TO_SECTION = {
    "decision": "## Decisions [decision]",
    "lesson": "## Lessons [lesson]",
    "pattern": "## Patterns [pattern]",
    "rule": "## Key Rules [rule]",
    "fact": "## Setup [fact]",
    "open": "## Open Items [open]",
}

section_items = {}
for item in data.get("items", []):
    tag = item.get("tag", "fact")
    content = item.get("content", "").strip()
    date = item.get("date", "")
    status = item.get("status", "active")
    source = item.get("source", "")
    container = item.get("container", "")
    if not content:
        continue
    section = TAG_TO_SECTION.get(tag, "## Setup [fact]")
    if section not in section_items:
        section_items[section] = []

    parts = []
    if date:
        parts.append(f"[{date}]")
    parts.append(content)
    if container:
        parts.append(f"(container:{container})")
    if status and status != "active":
        parts.append(f"[{status}]")
    if source and source != f"container:{container}":
        parts.append(f"({source})")
    section_items[section].append(" ".join(parts))

with open(MEMORY_FILE) as f:
    lines = f.read().split("\n")

new_lines = []
items_added = 0
for i, line in enumerate(lines):
    new_lines.append(line)
    for section_header in section_items:
        if line.strip() == section_header.strip():
            if i + 1 < len(lines) and lines[i + 1].strip() == "":
                new_lines.append(lines[i + 1])
                i += 1
            for item_line in section_items[section_header]:
                new_lines.append(f"  {item_line}")
                items_added += 1

with open(MEMORY_FILE, "w") as f:
    f.write("\n".join(new_lines))

print(f"Appended {items_added} items to {len(section_items)} sections")
