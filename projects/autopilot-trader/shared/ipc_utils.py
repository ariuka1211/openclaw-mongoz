"""
Shared IPC utilities for cross-process JSON communication.

Provides atomic write (tmp + replace) and resilient read (retry on partial)
for the AI-trader ↔ bot IPC channel.
"""

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("ipc-utils")


def atomic_write(path: Path, data: dict) -> None:
    """Atomic JSON write — prevents partial reads by other processes.

    Writes to a .tmp file, then os.replace() to the target.
    os.replace is atomic on POSIX (single syscall).
    """
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(path))


def safe_read_json(path: Path, retries: int = 2, delay: float = 0.1) -> dict | None:
    """Read JSON with retry — handles race conditions from concurrent atomic_write.

    If we catch a file mid-write (partial JSON), retry after a short delay.
    os.replace is atomic, so 1 retry is almost always enough.
    """
    for attempt in range(retries + 1):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            if attempt < retries:
                time.sleep(delay)
            else:
                # Log non-"file not found" errors (permission, disk full, etc.)
                if not isinstance(e, FileNotFoundError):
                    log.warning(f"Failed to read {path}: {e}")
                return None
    return None
