#!/usr/bin/env python3
"""LLM-powered cleanup: deduplicate, trim, and reorganize each MEMORY.md section."""
import json, re, sys, os, subprocess

MEMORY_FILE = os.environ["MEMORY_FILE"]
WORKSPACE = os.environ.get("WORKSPACE", "/root/.openclaw/workspace")

with open(MEMORY_FILE) as f:
    content = f.read()

parts = re.split(r'(^## .+\[.*?\]\s*$)', content, flags=re.MULTILINE)
section_map = {}
for i in range(len(parts)):
    if re.match(r'^## ', parts[i]):
        header = parts[i].strip()
        body_parts = []
        j = i + 1
        while j < len(parts) and not re.match(r'^## ', parts[j]):
            body_parts.append(parts[j])
            j += 1
        section_map[header] = {"idx": i, "body": ''.join(body_parts)}

total_cleaned = 0
total_moved = 0

PROMPT_TEMPLATE = open(os.path.join(WORKSPACE, "prompts/cleanup.txt")).read()

for header, info in section_map.items():
    body = info["body"]
    idx = info["idx"]

    prompt = (PROMPT_TEMPLATE
              .replace("{{SECTION}}", header)
              .replace("{{BODY}}", body)
              .replace("{{SECTIONS}}", '\n'.join(section_map.keys())))

    result = subprocess.run(
        ["bash", f"{WORKSPACE}/scripts/memory-llm.sh",
         "You are a memory curator. Respond only with valid JSON.",
         "google/gemini-2.5-flash", "2048"],
        input=prompt, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        continue

    try:
        data = json.loads(result.stdout)
        sc = data.get("section_content", "")
        if isinstance(sc, list):
            new_body = '\n'.join(str(item) if isinstance(item, str) else json.dumps(item) for item in sc)
        else:
            new_body = str(sc).strip()
        removed = data.get("items_removed", 0)
        moved = data.get("items_moved", [])
        reason = data.get("reason", "")

        if new_body and (removed > 0 or moved):
            if idx + 1 < len(parts):
                parts[idx + 1] = '\n' + new_body + '\n'
            total_cleaned += removed
            total_moved += len(moved)
            print(f"Cleaned {header}: -{removed}, +{len(moved)} ({reason})")

            for move in moved:
                target = move.get("target_section", "").strip()
                item = move.get("item", "")
                if target in section_map:
                    t_idx = section_map[target]["idx"]
                    if t_idx + 1 < len(parts):
                        parts[t_idx + 1] = parts[t_idx + 1].rstrip() + '\n' + item + '\n'
                    else:
                        print(f"  WARN: target '{target}' body index {t_idx + 1} out of bounds (len={len(parts)})")
    except (json.JSONDecodeError, KeyError):
        continue

if total_cleaned > 0 or total_moved > 0:
    with open(MEMORY_FILE, "w") as f:
        f.write(''.join(parts))
    print(f"Total: cleaned {total_cleaned}, moved {total_moved}")
else:
    print("No cleanup needed")
