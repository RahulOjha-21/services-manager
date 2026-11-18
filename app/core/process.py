"""Generic managed subprocess: start/stop a shell command, stream its output line by line."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from .registry import RuntimeEnv

LineCallback = Callable[[str, str], None]          # (stream, text)
ExitCallback = Callable[[int, bool], None]         # (return_code, stop_requested)

# Applied to every child so output is line-buffered and colour-free.
DEFAULT_ENV = {"PYTHONUNBUFFERED": "1", "RCUTILS_COLORIZED_OUTPUT": "0"}


class ProcessError(Exception):
    pass


class State:
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXITED = "exited"      # ended by itself with code 0
    FAILED = "failed"      # ended by itself with non-zero code / could not start


def build_script(command: str, sources: tuple[str, ...]) -> str:
    lines = [f"source {shlex.quote(os.path.expanduser(s))}" for s in sources]
    # No `exec`: it would break compound commands (a && b). Bash already execs a
    # single simple command itself, and stop() signals the whole process group.
    lines.append(command)
    return "\n".join(lines)


def clean_env() -> dict:
    """Environment for child commands, WITHOUT the manager's own virtualenv.

    The manager may run inside .venv; children must use the system python3
    (with ROS / ~/.local packages such as pymodbus) exactly like a normal terminal.
    """
    env = os.environ.copy()
    venv_dirs = {d for d in (env.pop("VIRTUAL_ENV", None),
                             sys.prefix if sys.prefix != sys.base_prefix else None) if d}
    venv_bins = {os.path.realpath(os.path.join(d, "bin")) for d in venv_dirs}
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep)
        if p and os.path.realpath(p) not in venv_bins)
    for key in ("PYTHONHOME", "VIRTUAL_ENV_PROMPT"):
        env.pop(key, None)
    return env


def describe_exit(rc: int | None) -> str:
    if rc is None:
        return "-"
    if rc < 0:
        try:
            return f"{rc} ({signal.Signals(-rc).name})"
        except ValueError:
            return str(rc)
    return str(rc)


class ManagedProcess:
    def __init__(self, name: str, command: str, runtime: RuntimeEnv, *,
                 stop_timeout: float = 10.0,
                 on_line: LineCallback | None = None,
                 on_exit: ExitCallback | None = None):
        self.name = name
        self.command = command
        self.runtime = runtime
        self.stop_timeout = stop_timeout
        self._on_line = on_line or (lambda s, t: None)
        self._on_exit = on_exit
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._stop_requested = False
        self.state = State.STOPPED
        self.pid: int | None = None
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        self.exit_code: int | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ #
    @property
    def cwd(self) -> str | None:
        return os.path.expanduser(self.runtime.cwd) if self.runtime.cwd else None

    def is_alive(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def pgid(self) -> int | None:
        return self.pid if self.is_alive() else None

    def uptime(self) -> float | None:
        if self.started_at and self.state in (State.RUNNING, State.STOPPING, State.STARTING):
            return time.time() - self.started_at
        return None

    # ------------------------------------------------------------------ #
    def start(self) -> int:
        with self._lock:
            if self.is_alive():
                raise ProcessError("Process is already running")
            cwd = self.cwd
            if cwd and not os.path.isdir(cwd):
                self._fail(f"Working directory not found: {cwd}")
            for src in self.runtime.sources:
                # relative paths are sourced from the command's working directory
                path = os.path.join(cwd or os.getcwd(), os.path.expanduser(src))
                if not os.path.isfile(path):
                    self._fail(f"Environment script not found: {path}")

            env = clean_env()
            env.update(DEFAULT_ENV)
            env.update(self.runtime.env)
            self._stop_requested = False
            self.state = State.STARTING
            try:
                proc = subprocess.Popen(
                    ["bash", "-c", build_script(self.command, self.runtime.sources)],
                    cwd=cwd, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True,      # own process group -> clean stop of children
                )
            except OSError as e:
                self._fail(f"Could not launch: {e}")

            self._proc = proc
            self.pid = proc.pid
            self.started_at = time.time()
            self.stopped_at = None
            self.exit_code = None
            self.last_error = None
            self.state = State.RUNNING

        readers = [
            threading.Thread(target=self._read, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._read, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for t in readers:
            t.start()
        threading.Thread(target=self._wait, args=(proc, readers), daemon=True,
                         name=f"wait-{self.name}").start()
        return proc.pid

    def _fail(self, msg: str):
        self.state = State.FAILED
        self.last_error = msg
        raise ProcessError(msg)

    def _read(self, pipe, stream: str) -> None:
        try:
            for raw in iter(pipe.readline, b""):
                try:
                    self._on_line(stream, raw.decode("utf-8", errors="replace"))
                except Exception:   # never let a callback kill the reader
                    pass
        finally:
            pipe.close()

    def _wait(self, proc: subprocess.Popen, readers: list[threading.Thread]) -> None:
        rc = proc.wait()
        for t in readers:
            t.join(timeout=2)
        with self._lock:
            if proc is not self._proc:
                return
            requested = self._stop_requested
            self.exit_code = rc
            self.stopped_at = time.time()
            if requested:
                self.state = State.STOPPED
            else:
                self.state = State.EXITED if rc == 0 else State.FAILED
        if self._on_exit:
            try:
                self._on_exit(rc, requested)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def stop(self, wait: bool = False) -> bool:
        """Graceful stop: SIGINT (like Ctrl+C) -> SIGTERM -> SIGKILL on the whole group."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return False
            self._stop_requested = True
            self.state = State.STOPPING
        t = threading.Thread(target=self._terminate, args=(proc,), daemon=True)
        t.start()
        if wait:
            t.join()
        return True

    def _terminate(self, proc: subprocess.Popen) -> None:
        steps = ((signal.SIGINT, self.stop_timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0))
        for sig, timeout in steps:
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=timeout)
                break
            except subprocess.TimeoutExpired:
                self._on_line("system", f"Still running {timeout:g}s after {sig.name}, escalating...")
        # The leader may exit before its children (e.g. `ros2 run` -> python node).
        # Make sure the whole group is gone: wait, then SIGTERM, then SIGKILL.
        for sig, timeout in ((None, self.stop_timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 3.0)):
            if not _group_alive(proc.pid):
                return
            if sig is not None:
                self._on_line("system", f"Child processes still running, sending {sig.name} to the group")
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    return
            deadline = time.time() + timeout
            while time.time() < deadline and _group_alive(proc.pid):
                time.sleep(0.2)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
