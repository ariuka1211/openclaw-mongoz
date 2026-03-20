#!/usr/bin/env python3
"""Supersede outdated facts, apply TTL decay, archive old items."""
import json, re, sys, os
from datetime import datetime, timezone

MEMORY_FILE = os.environ["MEMORY_FILE"]
WORKSPACE = os.environ.get("WORKSPACE", "/root/.openclaw/workspace")
TTL_CONFIG_FILE = os.path.join(WORKSPACE, "memory/ttl-config.json")

data = json.loads(os.environ.get("LLM_RESPONSE", "{}"))
updates = data.get("updates", [])
items = data.get("items", [])

def load_ttl_config():
    try:
        with open(TTL_CONFIG_FILE) as f:
            config = json.load(f)
        return config.get("sections", {}), config.get("defaults", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}

ttl_sections, ttl_defaults = load_ttl_config()

def parse_sections(content):
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
    return parts, sections

# ── Supersede outdated facts ──
if updates:
    with open(MEMORY_FILE) as f:
        content = f.read()

    superseded_count = 0
    lines = content.split('\n')

    for update in updates:
        original = update.get("original", "").strip()
        updated = update.get("updated", "").strip()
        if not original or not updated:
            continue

        orig_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{5,}', original))
        if not orig_words:
            continue

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith('-'):
                continue

            clean_line = re.sub(r'\s*\[(superseded|expired|active)\]', '', stripped)
            clean_line = re.sub(r'^-\s*(?:⭐\s*)?', '', clean_line)
            clean_line = re.sub(r'^\[[\d-]+\]\s*', '', clean_line)

            line_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{5,}', clean_line))
            if not line_words:
                continue

            overlap = len(orig_words & line_words) / len(orig_words)
            if overlap >= 0.6 and '[superseded]' not in lines[i]:
                lines[i] = lines[i].rstrip() + ' [superseded]'
                superseded_count += 1
                print(f"  Superseded: {stripped[:80]}...")
                print(f"    → {updated[:80]}...")
                break

    if superseded_count > 0:
        with open(MEMORY_FILE, 'w') as f:
            f.write('\n'.join(lines))
        print(f"Superseded {superseded_count} outdated fact(s)")

# ── Enrich new items ──
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
enriched_count = 0

for item in items:
    if not item.get("content", "").strip():
        continue
    if not item.get("date"):
        item["date"] = today
    if not item.get("status"):
        item["status"] = "active"
    enriched_count += 1

if items:
    with open("/tmp/memory-llm-response.json", 'w') as f:
        json.dump(data, f)
    print(f"Wrote enriched response to /tmp/memory-llm-response.json")

if enriched_count > 0:
    print(f"Enriched {enriched_count} new item(s) with metadata")

# ── TTL-based expiry ──
if ttl_sections:
    with open(MEMORY_FILE) as f:
        content = f.read()

    parts, sections = parse_sections(content)
    expired_count = 0
    today_dt = datetime.now(timezone.utc)

    for header, info in sections.items():
        ttl_days = None
        for config_header, config in ttl_sections.items():
            if config_header in header or header in config_header:
                ttl_days = config.get("ttl_days")
                break
        if not ttl_days:
            continue

        body = info["body"]
        idx = info["idx"]
        new_lines = []
        section_expired = 0

        for line in body.split('\n'):
            stripped = line.strip()
            if not stripped.startswith('-') or '[superseded]' in stripped or '[expired]' in stripped:
                new_lines.append(line)
                continue

            date_match = re.search(r'\[(\d{4}-\d{2}-\d{2})\]', stripped)
            if not date_match:
                new_lines.append(line)
                continue

            try:
                item_date = datetime.strptime(date_match.group(1), '%Y-%m-%d').replace(tzinfo=timezone.utc)
                age_days = (today_dt - item_date).days
                if age_days > ttl_days:
                    new_lines.append(line.rstrip() + ' [expired]')
                    section_expired += 1
                    print(f"  Expired ({age_days}d > {ttl_days}d TTL): {stripped[:70]}...")
                else:
                    new_lines.append(line)
            except ValueError:
                new_lines.append(line)

        if section_expired > 0:
            parts[idx + 1] = '\n'.join(new_lines) + '\n'
            expired_count += section_expired

    if expired_count > 0:
        with open(MEMORY_FILE, 'w') as f:
            f.write(''.join(parts))
        print(f"Expired {expired_count} item(s) based on TTLs")
else:
    print("No TTL config found, skipping expiry check")

# ── Archive old superseded/expired items ──
with open(MEMORY_FILE) as f:
    content = f.read()

lines = content.split('\n')
archived = 0
keep_lines = []
archive_days = ttl_defaults.get("archive_after_days", 30)

for line in lines:
    if '[superseded]' in line or '[expired]' in line:
        date_match = re.search(r'\[(\d{4}-\d{2}-\d{2})\]', line)
        if date_match:
            try:
                item_date = datetime.strptime(date_match.group(1), '%Y-%m-%d').replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - item_date).days
                if age_days > archive_days:
                    archived += 1
                    print(f"  Archived ({age_days}d old): {line.strip()[:70]}...")
                    continue
            except ValueError:
                pass
    keep_lines.append(line)

if archived > 0:
    with open(MEMORY_FILE, 'w') as f:
        f.write('\n'.join(keep_lines))
    print(f"Archived {archived} old superseded/expired item(s)")

print("Versioning complete")
