"""Monitors: always-on background commands whose latest output becomes a live value."""
from __future__ import annotations

import re
import threading
import time

from .process import ManagedProcess, ProcessError
from .registry import MonitorDef


class Monitor:
    def __init__(self, service_id: str, defn: MonitorDef):
        self.defn = defn
        self._regex = re.compile(defn.pattern) if defn.pattern else None
        self.value: str | None = None
        self.updated_at: float | None = None
        self.last_message: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.process = ManagedProcess(f"{service_id}:monitor:{defn.id}", defn.command, defn.runtime,
                                      stop_timeout=3, on_line=self._on_line)

    def _on_line(self, stream: str, text: str) -> None:
        text = text.strip()
        if not text or text == "---":
            return
        if self._regex:
            m = self._regex.search(text)
            if not m:
                self.last_message = text
                return
            text = m.group(1) if m.groups() else m.group(0)
        elif stream == "stderr":
            self.last_message = text
            return
        self.value = text
        self.updated_at = time.time()

    # -- lifecycle (supervised: restarted automatically if it dies) ------- #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, daemon=True,
                                        name=f"monitor-{self.defn.id}")
        self._thread.start()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            if not self.process.is_alive():
                try:
                    self.process.start()
                except ProcessError as e:
                    self.last_message = str(e)
            self._stop.wait(self.defn.retry_delay)

    def stop(self) -> None:
        self._stop.set()
        self.process.stop(wait=True)

    def info(self) -> dict:
        age = time.time() - self.updated_at if self.updated_at else None
        if age is None:
            state = "waiting"
        elif age > self.defn.stale_after:
            state = "stale"
        else:
            state = "live"
        style = self.defn.states.get(self.value or "", "neutral") if state == "live" else "neutral"
        return {
            "id": self.defn.id,
            "label": self.defn.label,
            "value": self.value,
            "state": state,
            "style": style,
            "age": age,
            "message": self.last_message,
            "command": self.defn.command,
        }
