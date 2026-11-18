"""(#7) CPU and memory per service, read directly from /proc (no extra dependencies).

A managed service runs in its own session, so every child (ros2 launch nodes, uvicorn
reload workers, vite, ...) is counted. A service started from a terminal is measured
by the process group(s) of its detected PIDs.
"""
from __future__ import annotations

import os
import time

CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def scan_proc() -> dict[int, tuple[int, int, int, int]]:
    """pid -> (pgrp, session, cpu_ticks, rss_bytes) for every process."""
    out = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                data = f.read().decode(errors="replace")
        except OSError:
            continue
        rest = data[data.rfind(")") + 2:].split()
        try:
            # fields after "(comm)": state ppid pgrp session ... utime(11) stime(12) ... rss(21)
            out[int(name)] = (int(rest[2]), int(rest[3]),
                              int(rest[11]) + int(rest[12]), int(rest[21]) * PAGE_SIZE)
        except (IndexError, ValueError):
            continue
    return out


class GroupMeter:
    """Turns successive /proc snapshots into CPU % for one group of processes."""

    def __init__(self):
        self._key = None
        self._prev_ts = 0.0
        self._prev: dict[int, int] = {}

    def measure(self, procs: dict, key: tuple[str, frozenset]) -> dict:
        kind, ids = key
        idx = 1 if kind == "session" else 0
        members = {pid: v for pid, v in procs.items() if v[idx] in ids}
        now = time.time()
        ticks = {pid: v[2] for pid, v in members.items()}
        cpu = 0.0
        if key == self._key and self._prev_ts:
            dt = now - self._prev_ts
            used = sum(t - self._prev.get(pid, 0) for pid, t in ticks.items())
            cpu = max(0.0, used / CLK_TCK / dt * 100) if dt > 0 else 0.0
        self._key, self._prev_ts, self._prev = key, now, ticks
        return {"cpu": round(cpu, 1),
                "mem_mb": round(sum(v[3] for v in members.values()) / 1048576, 1),
                "procs": len(members)}

    def reset(self) -> None:
        self._key, self._prev_ts, self._prev = None, 0.0, {}
