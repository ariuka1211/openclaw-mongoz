"""
Shared IPC utilities for cross-process JSON communication.

Provides atomic write (tmp + replace) and resilient read (retry on partial)
for the AI-trader bot IPC channel.
"""

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("ipc-utils")


def atomic_write(path: Path, data: dict) -> None:
    """Atomic JSON write prevents partial reads by other processes."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(path))


def safe_read_json(path: Path, retries: int = 2, delay: float = 0.1) -> dict | None:
    """Read JSON with retry handles race conditions from concurrent atomic_write."""
    for attempt in range(retries + 1):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            if attempt < retries:
                time.sleep(delay)
            else:
                if not isinstance(e, FileNotFoundError):
                    log.warning(f"Failed to read {path}: {e}")
                return None
    return None
