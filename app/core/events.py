"""(#8) Event history: who started/stopped what, crashes, health changes, logins.

Kept in memory for the UI and appended to logs/events.jsonl so it survives restarts.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

LEVELS = {"info", "success", "warning", "error"}


class EventLog:
    def __init__(self, path: Path, maxlen: int = 2000, max_bytes: int = 5 * 1024 * 1024):
        self.path = path
        self.max_bytes = max_bytes
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        for p in (self.path.with_suffix(".jsonl.1"), self.path):
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                self._seq += 1
                ev["seq"] = self._seq
                self._events.append(ev)

    def record(self, kind: str, message: str, *, service: str | None = None,
               user: str | None = None, level: str = "info") -> dict:
        ev = {"ts": time.time(), "kind": kind, "service": service, "user": user,
              "level": level if level in LEVELS else "info", "message": message}
        with self._lock:
            self._seq += 1
            self._events.append({**ev, "seq": self._seq})
            try:
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    self.path.replace(self.path.with_suffix(".jsonl.1"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except OSError:
                pass
        return ev

    def list(self, service: str | None = None, kind: str | None = None,
             level: str | None = None, limit: int = 200) -> list[dict]:
        with self._lock:
            items = list(self._events)
        out = []
        for ev in reversed(items):              # newest first
            if service and ev.get("service") != service:
                continue
            if kind and ev.get("kind") != kind:
                continue
            if level and ev.get("level") != level:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    def count(self, service: str, kinds: set[str], since: float) -> int:
        with self._lock:
            return sum(1 for e in self._events
                       if e.get("service") == service and e.get("kind") in kinds and e["ts"] >= since)


def start_of_today() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
