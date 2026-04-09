#!/usr/bin/env python3
"""
Migrate OpenClaw memory/*.md files to memvid workspace-memory.mv2

Reads all .md files in the memory directory (except session.md),
converts each to a memvid record with proper titles/labels/metadata,
and writes them into workspace-memory.mv2.
"""

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ingest import MemvidIngest, MemvidIngestError


MEMORY_DIR = "/root/.openclaw/workspace/memory"
MV2_FILE = "/root/.openclaw/workspace/workspace-memory.mv2"
EXCLUDE_FILES = {"session.md"}


def extract_metadata_from_filename(filename: str) -> dict:
    """
    Extract metadata from memory filename patterns.
    
    Patterns:
    - YYYY-MM-DD.md -> daily log
    - YYYY-MM-DD-description.md -> topic-specific daily note
    - mental-health.md, todo.md, etc. -> named memory
    """
    name = filename.replace(".md", "")
    metadata = {
        "source": "openclaw_memory",
        "source_file": filename,
    }
    
    # Try to parse date prefix
    parts = name.split("-", 2)
    if len(parts) >= 2:
        try:
            date_str = f"{parts[0]}-{parts[1]}"
            # Check if third part is also a date component or a description
            if len(parts) == 3:
                # Could be YYYY-MM-DD-description or YYYY-MM-DD-HHMM
                third = parts[2]
                if third.isdigit() and len(third) == 4:
                    # Time component like 2254
                    metadata["date"] = date_str
                    metadata["time"] = third
                    metadata["type"] = "session_log"
                else:
                    # Description
                    metadata["date"] = date_str
                    metadata["topic"] = third
                    metadata["type"] = "daily_note"
            else:
                metadata["date"] = date_str
                metadata["type"] = "daily_log"
        except:
            metadata["type"] = "named_memory"
    else:
        metadata["type"] = "named_memory"
        metadata["name"] = name
    
    return metadata


def generate_title(filename: str, content: str) -> str:
    """
    Generate a title for the memvid record.
    Uses filename as base, optionally extracts first heading from content.
    """
    # Default title from filename
    default_title = f"Memory: {filename}"
    
    # Try to extract first heading from content
    lines = content.split("\n")
    for line in lines[:10]:  # Check first 10 lines
        line = line.strip()
        if line.startswith("# "):
            heading = line[2:].strip()
            # Combine with filename for uniqueness
            return f"{heading} ({filename})"
    
    return default_title


def infer_labels(filename: str, content: str) -> list:
    """
    Infer labels from filename and content keywords.
    """
    labels = set()
    name_lower = filename.lower()
    content_lower = content.lower()
    
    # Filename-based labels
    if "cleanup" in name_lower:
        labels.add("cleanup")
    if "refactor" in name_lower:
        labels.add("refactor")
    if "restart" in name_lower or "start" in name_lower:
        labels.add("restart")
    if "fix" in name_lower or "bug" in name_lower:
        labels.add("bugfix")
    if "bot" in name_lower:
        labels.add("bot")
    if "ai" in name_lower:
        labels.add("ai")
    if "leverage" in name_lower:
        labels.add("leverage")
    if "position" in name_lower:
        labels.add("positions")
    if "alert" in name_lower:
        labels.add("alerts")
    if "dashboard" in name_lower:
        labels.add("dashboard")
    if "docs" in name_lower or "doc" in name_lower:
        labels.add("documentation")
    if "session" in name_lower:
        labels.add("session")
    if "gateway" in name_lower:
        labels.add("gateway")
    if "scanner" in name_lower:
        labels.add("scanner")
    if "trading" in name_lower or "trade" in name_lower:
        labels.add("trading")
    if "memory" in name_lower:
        labels.add("memory")
    if "mental-health" in name_lower:
        labels.add("mental-health")
    if "todo" in name_lower:
        labels.add("todo")
    
    # Content-based labels
    if "bitcoin" in content_lower or "btc" in content_lower:
        labels.add("bitcoin")
    if "grid" in content_lower:
        labels.add("grid-bot")
    if "autopilot" in content_lower or "auto-pilot" in content_lower:
        labels.add("autopilot")
    if "binance" in content_lower:
        labels.add("binance")
    
    # Default label
    if not labels:
        labels.add("memory")
    
    return sorted(list(labels))


def migrate_memory_files():
    """
    Main migration function.
    """
    print(f"=== Memvid Memory Migration ===")
    print(f"Source: {MEMORY_DIR}")
    print(f"Target: {MV2_FILE}")
    print()
    
    # Check source directory
    if not os.path.exists(MEMORY_DIR):
        print(f"ERROR: Memory directory not found: {MEMORY_DIR}")
        sys.exit(1)
    
    # Collect files to migrate
    files = []
    for filename in sorted(os.listdir(MEMORY_DIR)):
        if not filename.endswith(".md"):
            continue
        if filename in EXCLUDE_FILES:
            print(f"SKIP (excluded): {filename}")
            continue
        
        filepath = os.path.join(MEMORY_DIR, filename)
        if not os.path.isfile(filepath):
            continue
        
        files.append((filename, filepath))
    
    print(f"Files to migrate: {len(files)}")
    print()
    
    # Create/open memvid file
    try:
        ingestor = MemvidIngest(MV2_FILE, auto_create=True)
        print(f"Opened/created memvid file: {MV2_FILE}")
    except Exception as e:
        print(f"ERROR: Failed to open memvid file: {e}")
        sys.exit(1)
    
    # Track results
    results = {
        "total": len(files),
        "ingested": 0,
        "skipped": 0,
        "errors": [],
        "frame_ids": []
    }
    
    # Process each file
    for idx, (filename, filepath) in enumerate(files, 1):
        try:
            # Read file content
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Generate title
            title = generate_title(filename, content)
            
            # Extract metadata
            metadata = extract_metadata_from_filename(filename)
            metadata["migration_timestamp"] = datetime.now(timezone.utc).isoformat()
            metadata["file_size"] = os.path.getsize(filepath)
            
            # Infer labels
            labels = infer_labels(filename, content)
            label = labels[0]  # Primary label
            
            # Create memvid record
            frame_id = ingestor.add_document(
                title=title,
                content=content,
                label=label,
                metadata=metadata
            )
            
            results["ingested"] += 1
            results["frame_ids"].append(frame_id)
            
            if idx % 10 == 0 or idx == len(files):
                print(f"  [{idx}/{len(files)}] OK: {filename}")
            
        except Exception as e:
            error_msg = f"{filename}: {e}"
            results["errors"].append(error_msg)
            print(f"  [{idx}/{len(files)}] ERROR: {error_msg}")
    
    ingestor.close()
    
    # Print summary
    print()
    print(f"=== Migration Summary ===")
    print(f"Total files found: {results['total']}")
    print(f"Ingested: {results['ingested']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Errors: {len(results['errors'])}")
    
    if results["errors"]:
        print()
        print("Errors:")
        for err in results["errors"]:
            print(f"  - {err}")
    
    # Verify
    print()
    print(f"Verification:")
    if results["ingested"] == results["total"]:
        print(f"  ✓ Record count matches file count ({results['ingested']} == {results['total']})")
    else:
        print(f"  ✗ MISMATCH: {results['ingested']} records vs {results['total']} files")
    
    return results


if __name__ == "__main__":
    results = migrate_memory_files()
    
    if results["errors"]:
        sys.exit(1)
    sys.exit(0)
