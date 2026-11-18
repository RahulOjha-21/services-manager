"""(#2) Health checks: is a running service actually working (port / HTTP / log line)?"""
from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.request

from .registry import HealthDef

HEALTHY, STARTING, UNHEALTHY, UNKNOWN = "healthy", "starting", "unhealthy", "unknown"


def check_port(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} accepting connections"
    except OSError as e:
        return False, f"{host}:{port} {e.strerror or e}"


def check_http(url: str, timeout: float) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 400, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return e.code < 400, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 - any failure means unhealthy
        reason = getattr(e, "reason", e)
        return False, str(reason)


class HealthMonitor:
    def __init__(self, defn: HealthDef):
        self.defn = defn
        self._ready = re.compile(defn.log_ready) if defn.log_ready else None
        self._bad = re.compile(defn.log_unhealthy) if defn.log_unhealthy else None
        self.reset()

    def reset(self) -> None:
        self.log_state: bool | None = None       # True ready, False unhealthy, None not seen
        self.log_line: str | None = None
        self.state: str | None = None
        self.since: float | None = None
        self.checks: list[dict] = []
        self.last_check = 0.0

    # Called for every output line of the managed process ("last match wins").
    def on_line(self, text: str) -> None:
        if self._bad and self._bad.search(text):
            self.log_state, self.log_line = False, text[:200]
        elif self._ready and self._ready.search(text):
            self.log_state, self.log_line = True, text[:200]

    def due(self, now: float) -> bool:
        return now - self.last_check >= self.defn.interval

    def evaluate(self, running: bool, managed: bool, started_at: float | None) -> tuple[str | None, str | None]:
        """Run the checks. Returns (old_state, new_state)."""
        self.last_check = time.time()
        old = self.state
        if not running:
            self.state, self.checks = None, []
            if old is not None:
                self.since = None
            return old, None

        d = self.defn
        checks = []
        if d.port:
            ok, detail = check_port(d.host, d.port, d.timeout)
            checks.append({"name": f"Port {d.port}", "ok": ok, "detail": detail})
        if d.http:
            ok, detail = check_http(d.http, d.timeout)
            checks.append({"name": "HTTP", "ok": ok, "detail": f"{d.http} → {detail}"})
        if (self._ready or self._bad) and managed:
            if self.log_state is None:
                detail = "waiting for ready message" if self._ready else "no problem reported"
                ok = None if self._ready else True
            else:
                ok, detail = self.log_state, self.log_line
            checks.append({"name": "Log", "ok": ok, "detail": detail})

        age = (time.time() - started_at) if (managed and started_at) else float("inf")
        if not checks:
            new = UNKNOWN
        elif all(c["ok"] is True for c in checks):
            new = HEALTHY
        elif age < d.grace:
            new = STARTING
        else:
            new = UNHEALTHY
            for c in checks:
                if c["ok"] is None:
                    c["ok"], c["detail"] = False, f"not ready after {d.grace:g}s"

        self.checks = checks
        if new != old:
            self.since = time.time()
        self.state = new
        return old, new

    def info(self) -> dict:
        return {"state": self.state, "since": self.since, "checks": self.checks}
