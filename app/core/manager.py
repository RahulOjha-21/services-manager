"""ServiceManager: owns every Service, the event log and the background supervisor."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .events import EventLog
from .metrics import scan_proc
from .registry import load_config
from .service import Service, ServiceError

log = logging.getLogger(__name__)


class ServiceNotFound(KeyError):
    pass


class ServiceManager:
    def __init__(self, config_path: str, base_dir: Path):
        self.config_path = config_path
        self.base_dir = base_dir
        settings, defs = load_config(config_path, base_dir)
        self.settings = settings
        self.events = EventLog(settings.log_dir / "events.jsonl")          # (#8)
        self._services = {d.id: Service(d, settings, self.events) for d in defs}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._shut_down = False
        log.info("Loaded %d service(s) from %s", len(defs), config_path)

    @classmethod
    def from_file(cls, path: str, base_dir: Path) -> "ServiceManager":
        return cls(path, base_dir)

    def get(self, service_id: str) -> Service:
        try:
            return self._services[service_id]
        except KeyError:
            raise ServiceNotFound(service_id) from None

    def all(self) -> list[Service]:
        return list(self._services.values())

    # ------------------------------------------------------------------ #
    def start_background(self) -> None:
        for svc in self.all():
            svc.start_monitors()
            if svc.defn.autostart:
                try:
                    svc.start(user="autostart")
                except ServiceError as e:
                    log.warning("Autostart of %s skipped: %s", svc.id, e)
        threading.Thread(target=self._supervise, daemon=True, name="supervisor").start()

    def _supervise(self) -> None:
        """Metrics (#7) every `metrics_interval`, health checks (#2) when due."""
        pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")
        last_metrics = 0.0
        while not self._stop.is_set():
            now = time.time()
            services = self.all()
            if now - last_metrics >= self.settings.metrics_interval:
                last_metrics = now
                try:
                    procs = scan_proc()
                    for svc in services:
                        svc.sample_metrics(procs)
                except Exception:
                    log.exception("metrics sampling failed")
            futures = [pool.submit(svc.check_health) for svc in services if svc.health]
            for f in futures:
                try:
                    f.result(timeout=10)
                except Exception:
                    log.exception("health check failed")
            self._stop.wait(1.0)
        pool.shutdown(wait=False)

    # ------------------------------------------------------------------ #
    def reload(self, user: str | None = None) -> dict:
        """(#4) Re-read services.yaml; only added/changed/removed services are touched."""
        with self._lock:
            settings, defs = load_config(self.config_path, self.base_dir)   # ConfigError -> caller
            result = {"added": [], "removed": [], "updated": [], "restarted": [], "unchanged": []}
            new: dict[str, Service] = {}
            for d in defs:
                svc = self._services.get(d.id)
                if svc is None:
                    svc = Service(d, settings, self.events)
                    svc.start_monitors()
                    result["added"].append(d.id)
                else:
                    result[svc.update_definition(d, user)].append(d.id)
                new[d.id] = svc
            for sid, svc in self._services.items():
                if sid not in new:
                    svc.shutdown()
                    result["removed"].append(sid)
            self._services = new
            self.settings = settings
            changes = {k: v for k, v in result.items() if v and k != "unchanged"}
            summary = ", ".join(f"{k}: {', '.join(v)}" for k, v in changes.items()) or "no changes"
            self.events.record("config_reload", f"Configuration reloaded ({summary})", user=user,
                               level="info")
            log.info("Config reloaded by %s: %s", user, summary)
            return result

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        self._stop.set()
        log.info("Stopping all managed processes...")
        for svc in self.all():
            try:
                svc.shutdown()
            except Exception:
                log.exception("Error stopping %s", svc.id)
