#!/usr/bin/env python3
"""Append a bounded, caller-anonymous event for the Spruik manager."""

import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time


def clean(value):
    value = re.sub(r"[^a-z0-9_-]", "", value.lower())
    return value[:40] or "unknown"


def main():
    category = clean(sys.argv[1] if len(sys.argv) > 1 else "call")
    outcome = clean(sys.argv[2] if len(sys.argv) > 2 else "unknown")
    route = clean(sys.argv[3] if len(sys.argv) > 3 else "none")
    data_dir = Path(os.getenv("SPRUIK_DATA_DIR", "/var/lib/spruik"))
    data_dir.mkdir(parents=True, exist_ok=True)
    destination = data_dir / f"{category}-history.jsonl"
    lock_path = data_dir / ".event-history.lock"
    record = json.dumps(
        {"timestamp": int(time.time()), "outcome": outcome, "route": route},
        separators=(",", ":"),
    )

    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            existing = destination.read_text(encoding="utf-8").splitlines()[-199:]
        except (FileNotFoundError, OSError):
            existing = []
        temporary = destination.with_suffix(".tmp")
        temporary.write_text("\n".join(existing + [record]) + "\n", encoding="utf-8")
        os.replace(temporary, destination)


if __name__ == "__main__":
    main()
