"""A Service = one long-running command + its health, metrics, links, actions, monitors and logs."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from collections import deque

from .action import ActionError, ActionRunner
from .events import EventLog, start_of_today
from .health import HealthMonitor
from .logbuffer import LogBuffer
from .metrics import GroupMeter
from .monitor import Monitor
from .process import ManagedProcess, ProcessError, State, describe_exit
from .registry import ServiceDef, Settings

HISTORY_POINTS = 90


class ServiceError(Exception):
    pass


class Service:
    def __init__(self, defn: ServiceDef, settings: Settings, events: EventLog):
        self.events = events
        self.logs = LogBuffer(
            defn.id, settings.log_buffer_lines, settings.log_dir / f"{defn.id}.log",
            max_bytes=int(settings.log_file_max_mb * 1024 * 1024), backups=settings.log_file_backups,
        )
        self.restart_count = 0
        self._op_lock = threading.Lock()
        self._restart_timer: threading.Timer | None = None
        self._ext_cache: tuple[float, list[int]] = (0.0, [])
        self.meter = GroupMeter()                     # (#7)
        self.metrics: dict | None = None
        self.metrics_history: deque = deque(maxlen=HISTORY_POINTS)
        self.link_urls: dict[str, str] = {}           # (#3) URLs captured from output
        self.monitors: dict[str, Monitor] = {}
        self.actions: dict[str, ActionRunner] = {}
        self._build(defn, new_process=True)

    def _build(self, defn: ServiceDef, new_process: bool) -> None:
        self.defn = defn
        if new_process:
            self.process = ManagedProcess(defn.id, defn.command, defn.runtime,
                                          stop_timeout=defn.stop_timeout,
                                          on_line=self._on_line, on_exit=self._on_exit)
        else:
            self.process.stop_timeout = defn.stop_timeout
        self.health = HealthMonitor(defn.health) if defn.health else None
        self._link_res = [(l.label, re.compile(l.from_log)) for l in defn.links if l.from_log]
        self.monitors = {m.id: Monitor(defn.id, m) for m in defn.monitors}
        self.actions = {a.id: ActionRunner(defn.id, a, self.logs, on_finish=self._action_done)
                        for a in defn.actions}

    @property
    def id(self) -> str:
        return self.defn.id

    def _event(self, kind: str, message: str, user: str | None = None, level: str = "info") -> None:
        self.events.record(kind, message, service=self.id, user=user, level=level)

    # ------------------------------------------------------------------ #
    #  Output handling
    # ------------------------------------------------------------------ #
    def _on_line(self, stream: str, text: str) -> None:
        clean = self.logs.append(stream, text)
        if self.health:
            self.health.on_line(clean)
        for label, rx in self._link_res:
            m = rx.search(clean)
            if m:
                self.link_urls[label] = m.group(1) if m.groups() else m.group(0)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #
    def start(self, manual: bool = True, user: str | None = None) -> None:
        with self._op_lock:
            if self.process.is_alive():
                raise ServiceError(f"{self.defn.name} is already running")
            ext = self.external_pids(fresh=True)
            if ext:
                raise ServiceError(f"{self.defn.name} is already running outside the manager "
                                   f"(PID {', '.join(map(str, ext))}). Stop it first.")
            if manual:
                self.restart_count = 0
                self._cancel_restart()
            if self.health:
                self.health.reset()
            self.link_urls.clear()
            self.meter.reset()
            who = f" by {user}" if user else ""
            self.logs.system(f"Starting{who}: {self.defn.command}  (cwd: {self.process.cwd or '-'})")
            try:
                pid = self.process.start()
            except ProcessError as e:
                self.logs.system(f"Failed to start: {e}", "error")
                self._event("start_failed", f"Could not start: {e}", user, "error")
                raise ServiceError(str(e)) from e
            self.logs.system(f"Process started (PID {pid})", "success")
            if manual:
                self._event("start", f"Started (PID {pid})", user, "success")

    def stop(self, wait: bool = False, user: str | None = None, record: bool = True) -> None:
        self._cancel_restart()
        if not self.process.is_alive():
            raise ServiceError(f"{self.defn.name} is not running")
        who = f" by {user}" if user else ""
        self.logs.system(f"Stopping{who} (sending Ctrl+C / SIGINT)...")
        if record:
            self._event("stop", "Stop requested", user, "warning")
        self.process.stop(wait=wait)

    def restart(self, user: str | None = None) -> None:
        self._event("restart", "Restart requested", user, "info")

        def _do():
            try:
                if self.process.is_alive():
                    self.stop(wait=True, user=user, record=False)
                self.start(user=user, manual=True)
            except ServiceError as e:
                self.logs.system(f"Restart failed: {e}", "error")
        self.logs.system("Restart requested")
        threading.Thread(target=_do, daemon=True).start()

    def _on_exit(self, rc: int, requested: bool) -> None:
        code = describe_exit(rc)
        if requested:
            self.logs.system(f"Process stopped (exit code {code})", "info")
            return
        if rc == 0:
            self.logs.system("Process exited by itself (exit code 0)", "warning")
            self._event("exit", "Exited by itself (code 0)", "system", "warning")
            return
        self.logs.system(f"Process exited unexpectedly (exit code {code})", "error")
        self._event("crash", f"Crashed (exit code {code})", "system", "error")
        d = self.defn
        if d.restart_on_failure and self.restart_count < d.max_restarts:
            self.restart_count += 1
            self.logs.system(f"Auto-restart {self.restart_count}/{d.max_restarts} in {d.restart_delay:g}s", "warning")
            self._event("auto_restart", f"Auto-restart {self.restart_count}/{d.max_restarts}", "system", "warning")
            self._restart_timer = threading.Timer(d.restart_delay, self._auto_restart)
            self._restart_timer.daemon = True
            self._restart_timer.start()
        elif d.restart_on_failure:
            self.logs.system("Auto-restart limit reached; giving up", "error")

    def _auto_restart(self) -> None:
        try:
            self.start(manual=False)
        except ServiceError as e:
            self.logs.system(f"Auto-restart failed: {e}", "error")

    def _cancel_restart(self) -> None:
        if self._restart_timer:
            self._restart_timer.cancel()
            self._restart_timer = None

    # ------------------------------------------------------------------ #
    #  (#4) Apply a changed definition from a config reload
    # ------------------------------------------------------------------ #
    def update_definition(self, new: ServiceDef, user: str | None = None) -> str:
        """Returns 'unchanged', 'updated' (no restart needed) or 'restarted'."""
        old = self.defn
        if new == old:
            return "unchanged"
        launch_changed = new.launch_key() != old.launch_key()
        was_running = self.process.is_alive()
        if launch_changed and was_running:
            self.logs.system("Configuration changed: restarting with the new command/environment", "warning")
            self.process.stop(wait=True)
        for m in self.monitors.values():
            m.stop()
        self._build(new, new_process=launch_changed)
        self.start_monitors()
        if launch_changed and was_running:
            try:
                self.start(user=user)
            except ServiceError as e:
                self.logs.system(f"Restart after config change failed: {e}", "error")
            return "restarted"
        return "updated"

    # ------------------------------------------------------------------ #
    #  Actions / monitors
    # ------------------------------------------------------------------ #
    def run_action(self, action_id: str, user: str | None = None) -> None:
        runner = self.actions.get(action_id)
        if not runner:
            raise ServiceError(f"Unknown action '{action_id}'")
        try:
            runner.run()
        except ActionError as e:
            raise ServiceError(str(e)) from e
        self._event("action", f'Action "{runner.defn.label}" run', user)

    def _action_done(self, adef, status: str, rc: int) -> None:
        if status != "success":
            self._event("action_failed", f'Action "{adef.label}" {status} (code {rc})', "system", "error")

    def start_monitors(self) -> None:
        for m in self.monitors.values():
            m.start()

    # ------------------------------------------------------------------ #
    #  Periodic work, called by the manager's supervisor thread
    # ------------------------------------------------------------------ #
    def metric_key(self) -> tuple[str, frozenset] | None:
        if self.process.is_alive():
            return ("session", frozenset({self.process.pid}))
        groups = set()
        for pid in self.external_pids():
            try:
                groups.add(os.getpgid(pid))
            except ProcessLookupError:
                pass
        return ("pgrp", frozenset(groups)) if groups else None

    def sample_metrics(self, procs: dict) -> None:
        key = self.metric_key()
        if key is None:
            self.metrics = None
            self.meter.reset()
            return
        m = self.meter.measure(procs, key)
        self.metrics = m
        self.metrics_history.append((time.time(), m["cpu"], m["mem_mb"]))

    def check_health(self, force: bool = False) -> None:
        h = self.health
        if not h or not (force or h.due(time.time())):
            return
        managed = self.process.is_alive()
        running = managed or bool(self.external_pids())
        old, new = h.evaluate(running, managed, self.process.started_at if managed else None)
        if old == new or new is None or new == "starting":
            return
        if new == "healthy":
            took = ""
            if old in (None, "starting") and managed and self.process.started_at:
                took = f" (ready after {time.time() - self.process.started_at:.0f}s)"
            self._event("healthy", f"Healthy{took}", "system", "success")
        elif new == "unhealthy":
            reasons = "; ".join(f"{c['name']}: {c['detail']}" for c in h.checks if not c["ok"])
            self._event("unhealthy", f"Unhealthy: {reasons}", "system", "error")
            self.logs.system(f"Health check failed: {reasons}", "error")

    # ------------------------------------------------------------------ #
    #  Detect the same program started outside the manager (e.g. a terminal)
    # ------------------------------------------------------------------ #
    def _own_pgids(self) -> set[int]:
        pgids = {self.process.pgid}
        pgids |= {m.process.pgid for m in self.monitors.values()}
        pgids |= {a.pgid for a in self.actions.values()}
        pgids.add(os.getpgid(0))
        return {p for p in pgids if p}

    def external_pids(self, fresh: bool = False) -> list[int]:
        pattern = self.defn.detect_external
        if not pattern:
            return []
        ts, cached = self._ext_cache
        if not fresh and time.time() - ts < 1.5:
            return cached
        try:
            out = subprocess.run(["pgrep", "-f", "--", pattern], capture_output=True,
                                 text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        own = self._own_pgids()
        pids = []
        for tok in out.split():
            pid = int(tok)
            try:
                if pid == os.getpid() or os.getpgid(pid) in own:
                    continue
            except ProcessLookupError:
                continue
            pids.append(pid)
        self._ext_cache = (time.time(), pids)
        return pids

    def stop_external(self, user: str | None = None) -> list[int]:
        pids = self.external_pids(fresh=True)
        if not pids:
            raise ServiceError("No external process found")
        self.logs.system(f"Stopping external process(es) {pids} with SIGINT...", "warning")
        self._event("stop_external", f"Stop of external PID {pids} requested", user, "warning")

        def _escalate():
            deadline = time.time() + self.defn.stop_timeout
            while time.time() < deadline:
                if not self.external_pids(fresh=True):
                    self.logs.system("External process stopped", "success")
                    return
                time.sleep(0.5)
            for pid in self.external_pids(fresh=True):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError) as e:
                    self.logs.system(f"Could not SIGTERM {pid}: {e}", "error")
            self.logs.system("External process did not exit on SIGINT; sent SIGTERM", "warning")

        for pid in pids:
            try:
                os.kill(pid, signal.SIGINT)
            except PermissionError as e:
                raise ServiceError(f"Permission denied stopping PID {pid}: {e}") from e
            except ProcessLookupError:
                pass
        threading.Thread(target=_escalate, daemon=True).start()
        return pids

    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        self._cancel_restart()
        for m in self.monitors.values():
            m.stop()
        if self.process.is_alive():
            self.logs.system("Services Manager shutting down, stopping process")
            self.process.stop(wait=True)

    def _links(self) -> list[dict]:
        out = []
        for l in self.defn.links:
            url = self.link_urls.get(l.label) or l.url
            if url:
                out.append({"label": l.label, "url": url})
        return out

    def status(self) -> dict:
        p = self.process
        ext = [] if p.is_alive() else self.external_pids()
        state = "external" if ext else p.state
        d = self.defn
        hist = list(self.metrics_history)[-60:]
        running = state in (State.RUNNING, State.STARTING, State.STOPPING, "external")
        return {
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "group": d.group,
            "command": d.command,
            "cwd": p.cwd,
            "sources": list(d.runtime.sources),
            "state": state,
            "process_state": p.state,
            "pid": p.pid if p.is_alive() else None,
            "external_pids": ext,
            "started_at": p.started_at,
            "stopped_at": p.stopped_at,
            "uptime": p.uptime(),
            "exit_code": p.exit_code,
            "exit_desc": describe_exit(p.exit_code),
            "last_error": p.last_error,
            "restart_count": self.restart_count,
            "autostart": d.autostart,
            "restart_on_failure": d.restart_on_failure,
            "critical": d.critical,
            "can_start": state in (State.STOPPED, State.EXITED, State.FAILED),
            "can_stop": state in (State.RUNNING, State.STARTING, "external"),
            "can_restart": state == State.RUNNING,
            "health": (self.health.info() if self.health else None) if running else None,
            "has_health": self.health is not None,
            "links": self._links() if running else [],
            "metrics": self.metrics if running else None,
            "history": {"cpu": [h[1] for h in hist], "mem": [h[2] for h in hist]} if running else None,
            "crashes_today": self.events.count(d.id, {"crash"}, start_of_today()),
            "logs": {"errors": self.logs.counts["error"], "warnings": self.logs.counts["warning"],
                     "last_seq": self.logs.last_seq},
            "monitors": [m.info() for m in self.monitors.values()],
            "actions": [a.info() for a in self.actions.values()],
        }
