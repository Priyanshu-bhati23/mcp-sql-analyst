"""Append-only JSONL audit log of every tool call the model makes."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: str | Path | None):
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self.entries: list[dict] = []  # also kept in memory for the session and for tests

    def record(self, tool: str, status: str, **fields) -> None:
        entry = {"ts": round(time.time(), 3), "tool": tool, "status": status, **fields}
        with self._lock:
            self.entries.append(entry)
            if self._path:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
