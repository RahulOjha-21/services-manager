"""Actions: one-shot commands (buttons) attached to a service."""
from __future__ import annotations

import threading
import time

from .logbuffer import LogBuffer
from .process import ManagedProcess, ProcessError
from .registry import ActionDef

MAX_OUTPUT_LINES = 200


class ActionError(Exception):
    pass


class ActionRunner:
    def __init__(self, service_id: str, defn: ActionDef, logs: LogBuffer, on_finish=None):
        self.defn = defn
        self._on_finish = on_finish          # callback(action_def, status, exit_code)
        self.service_id = service_id
        self.logs = logs
        self._lock = threading.Lock()
        self._proc: ManagedProcess | None = None
        self._timer: threading.Timer | None = None
        self._timed_out = False
        self.last: dict | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    @property
    def pgid(self) -> int | None:
        return self._proc.pgid if self._proc else None

    def run(self) -> None:
        with self._lock:
            if self.running:
                raise ActionError(f"'{self.defn.label}' is already running")
            output: list[str] = []
            label = self.defn.label

            def on_line(stream: str, text: str) -> None:
                text = text.rstrip("\r\n")
                output.append(text)
                if len(output) > MAX_OUTPUT_LINES:
                    del output[0]
                self.logs.append("action", f"[{label}] {text}")

            self._timed_out = False
            self.last = {"status": "running", "started_at": time.time(), "duration": None,
                         "exit_code": None, "output": output}
            self._proc = ManagedProcess(f"{self.service_id}:action:{self.defn.id}", self.defn.command,
                                        self.defn.runtime, stop_timeout=3,
                                        on_line=on_line, on_exit=self._on_exit)
            self.logs.system(f'Action "{label}" started: {self.defn.command}')
            try:
                self._proc.start()
            except ProcessError as e:
                self.last.update(status="failed", duration=0)
                output.append(str(e))
                self.logs.system(f'Action "{label}" could not start: {e}', "error")
                raise ActionError(str(e)) from e
            self._timer = threading.Timer(self.defn.timeout, self._on_timeout)
            self._timer.daemon = True
            self._timer.start()

    def _on_timeout(self) -> None:
        if self.running:
            self._timed_out = True
            self.logs.system(f'Action "{self.defn.label}" timed out after {self.defn.timeout:g}s', "error")
            self._proc.stop()

    def _on_exit(self, rc: int, requested: bool) -> None:
        if self._timer:
            self._timer.cancel()
        last = self.last
        last["exit_code"] = rc
        last["duration"] = time.time() - last["started_at"]
        if self._timed_out:
            last["status"] = "timeout"
        elif rc == 0:
            last["status"] = "success"
            self.logs.system(f'Action "{self.defn.label}" completed ({last["duration"]:.1f}s)', "success")
        else:
            last["status"] = "failed"
            self.logs.system(f'Action "{self.defn.label}" failed with exit code {rc}', "error")
        if self._on_finish:
            try:
                self._on_finish(self.defn, last["status"], rc)
            except Exception:
                pass

    def info(self) -> dict:
        last = None
        if self.last:
            last = {k: v for k, v in self.last.items() if k != "output"}
            last["output"] = "\n".join(self.last["output"][-50:])
        return {
            "id": self.defn.id,
            "label": self.defn.label,
            "description": self.defn.description,
            "style": self.defn.style,
            "confirm": self.defn.confirm,
            "command": self.defn.command,
            "running": self.running,
            "last": last,
        }
