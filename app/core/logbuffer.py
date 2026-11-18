"""Thread-safe in-memory log ring buffer with change notification + rotating file."""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
SGR_RE = re.compile(r"\x1b\[[0-9;]*m")                      # colour codes (kept for the UI)
NON_SGR_RE = re.compile(r"(?!\x1b\[[0-9;]*m)(?:" + ANSI_RE.pattern + ")")
ERROR_RE = re.compile(r"\[(ERROR|FATAL)\]|Traceback \(most recent call last\)|\b\w*(Error|Exception):|\bERROR\b|\bFATAL\b")
WARN_RE = re.compile(r"\[WARN(ING)?\]|\bWARN(ING)?\b")
LEVELS = ("debug", "info", "success", "warning", "error")


def classify(text: str) -> str:
    if ERROR_RE.search(text):
        return "error"
    if WARN_RE.search(text):
        return "warning"
    return "info"


class LogBuffer:
    def __init__(self, name: str, maxlen: int = 3000, file_path: Path | None = None,
                 max_bytes: int = 5 * 1024 * 1024, backups: int = 3):
        self.name = name
        self._lines: deque[dict] = deque(maxlen=maxlen)
        self._seq = 0
        self._cond = threading.Condition()
        self.counts = {"error": 0, "warning": 0}
        self.file_path = file_path
        self._file_logger = None
        if file_path:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger(f"servicelog.{name}")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            if not logger.handlers:
                handler = RotatingFileHandler(file_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
            self._file_logger = logger

    # -- writing ----------------------------------------------------------- #
    def append(self, stream: str, text: str, level: str | None = None) -> str:
        """Store one line. Returns the plain (colour-free) text."""
        raw = text.rstrip("\r\n")
        if "\r" in raw:                     # progress bars: keep the final state
            raw = raw.rsplit("\r", 1)[-1]
        text = ANSI_RE.sub("", raw)
        if level not in LEVELS:
            level = classify(text)
        now = time.time()
        entry = {"seq": 0, "ts": now, "stream": stream, "level": level, "text": text}
        if "\x1b" in raw:
            coloured = NON_SGR_RE.sub("", raw)
            if coloured != text:
                entry["ansi"] = coloured     # (#9) colours rendered by the web console
        with self._cond:
            self._seq += 1
            entry["seq"] = self._seq
            self._lines.append(entry)
            if level in self.counts:
                self.counts[level] += 1
            self._cond.notify_all()
        if self._file_logger:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            self._file_logger.info(f"{stamp}.{int(now % 1 * 1000):03d} [{stream:<6}] {text}")
        return text

    def system(self, text: str, level: str = "info") -> None:
        self.append("system", text, level)

    def clear(self) -> None:
        with self._cond:
            self._lines.clear()
            self.counts = {"error": 0, "warning": 0}
            self._cond.notify_all()

    # -- reading ----------------------------------------------------------- #
    @property
    def last_seq(self) -> int:
        return self._seq

    def since(self, seq: int, limit: int | None = None) -> list[dict]:
        with self._cond:
            lines = [l for l in self._lines if l["seq"] > seq]
        return lines[-limit:] if limit else lines

    def wait_for(self, seq: int, timeout: float) -> bool:
        """Block until a line newer than `seq` exists (or timeout)."""
        with self._cond:
            return self._cond.wait_for(lambda: self._seq > seq, timeout)
