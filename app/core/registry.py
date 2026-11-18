"""Load and validate config/services.yaml into typed definitions."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
STYLES = {"primary", "success", "danger", "warning", "info", "neutral"}


class ConfigError(Exception):
    """Raised when services.yaml is invalid."""


@dataclass(frozen=True)
class RuntimeEnv:
    """How to launch a command: working dir, scripts to source, extra env."""
    cwd: str | None = None
    sources: tuple[str, ...] = ()
    env: dict = field(default_factory=dict)

    def with_cwd(self, cwd: str | None) -> "RuntimeEnv":
        return RuntimeEnv(cwd or self.cwd, self.sources, self.env)


@dataclass
class ActionDef:
    id: str
    label: str
    command: str
    runtime: RuntimeEnv
    description: str = ""
    style: str = "primary"
    confirm: bool = False
    timeout: float = 30.0


@dataclass
class MonitorDef:
    id: str
    label: str
    command: str
    runtime: RuntimeEnv
    pattern: str | None = None
    states: dict = field(default_factory=dict)
    stale_after: float = 5.0
    retry_delay: float = 5.0


@dataclass
class HealthDef:
    """(#2) How to decide whether a running service is actually working."""
    port: int | None = None             # TCP port must accept connections
    host: str = "127.0.0.1"
    http: str | None = None             # URL must answer with HTTP < 400
    log_ready: str | None = None        # regex: output line meaning "ready"
    log_unhealthy: str | None = None    # regex: output line meaning "broken"
    interval: float = 5.0
    timeout: float = 2.0
    grace: float = 60.0                 # seconds after start before "Unhealthy"


@dataclass
class LinkDef:
    """(#3) An 'Open' button. url may contain {host}; from_log captures a URL from output."""
    label: str
    url: str | None = None
    from_log: str | None = None


@dataclass
class ServiceDef:
    id: str
    name: str
    command: str
    runtime: RuntimeEnv
    description: str = ""
    group: str = "General"
    autostart: bool = False
    restart_on_failure: bool = False
    max_restarts: int = 3
    restart_delay: float = 5.0
    stop_timeout: float = 10.0
    detect_external: str | None = None
    critical: bool = False              # (#12) controls physical equipment
    health: HealthDef | None = None
    links: list[LinkDef] = field(default_factory=list)
    actions: list[ActionDef] = field(default_factory=list)
    monitors: list[MonitorDef] = field(default_factory=list)

    def launch_key(self) -> tuple:
        """Fields that require a process restart when changed (#4)."""
        return (self.command, self.runtime)


@dataclass
class Settings:
    log_dir: Path
    log_buffer_lines: int = 3000
    log_file_max_mb: float = 5
    log_file_backups: int = 3
    metrics_interval: float = 2.0
    auth_enabled: bool = False
    users_file: Path | None = None
    session_hours: float = 12.0


# --------------------------------------------------------------------------- #
def _req(d: dict, key: str, ctx: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{ctx}: missing required field '{key}'")
    return d[key]


def _check_id(value, ctx: str) -> str:
    value = str(value)
    if not ID_RE.match(value):
        raise ConfigError(f"{ctx}: id '{value}' must match {ID_RE.pattern}")
    return value


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _regex(value, ctx: str) -> str | None:
    if not value:
        return None
    try:
        re.compile(value)
    except re.error as e:
        raise ConfigError(f"{ctx}: invalid regex '{value}': {e}") from e
    return str(value)


def _build_runtime(raw: dict, profiles: dict, ctx: str) -> RuntimeEnv:
    sources: list[str] = []
    env: dict = {}
    cwd = None
    for name in _as_list(raw.get("profile")):
        if name not in profiles:
            raise ConfigError(f"{ctx}: unknown profile '{name}'")
        p = profiles[name] or {}
        sources += [str(s) for s in _as_list(p.get("source"))]
        env.update({str(k): str(v) for k, v in (p.get("env") or {}).items()})
        cwd = p.get("cwd") or cwd
    sources += [str(s) for s in _as_list(raw.get("source"))]
    env.update({str(k): str(v) for k, v in (raw.get("env") or {}).items()})
    cwd = raw.get("cwd") or cwd
    return RuntimeEnv(cwd=str(cwd) if cwd else None, sources=tuple(sources), env=env)


def _style(value, ctx: str) -> str:
    value = str(value or "primary")
    if value not in STYLES:
        raise ConfigError(f"{ctx}: style '{value}' must be one of {sorted(STYLES)}")
    return value


def _parse_action(raw: dict, rt: RuntimeEnv, ctx: str) -> ActionDef:
    aid = _check_id(_req(raw, "id", ctx), ctx)
    ctx = f"{ctx} action '{aid}'"
    return ActionDef(
        id=aid,
        label=str(raw.get("label") or aid),
        command=str(_req(raw, "command", ctx)).strip(),
        runtime=rt.with_cwd(raw.get("cwd")),
        description=str(raw.get("description") or ""),
        style=_style(raw.get("style"), ctx),
        confirm=bool(raw.get("confirm", False)),
        timeout=float(raw.get("timeout", 30)),
    )


def _parse_monitor(raw: dict, rt: RuntimeEnv, ctx: str) -> MonitorDef:
    mid = _check_id(_req(raw, "id", ctx), ctx)
    ctx = f"{ctx} monitor '{mid}'"
    states = {str(k): _style(v, ctx) for k, v in (raw.get("states") or {}).items()}
    return MonitorDef(
        id=mid,
        label=str(raw.get("label") or mid),
        command=str(_req(raw, "command", ctx)).strip(),
        runtime=rt.with_cwd(raw.get("cwd")),
        pattern=_regex(raw.get("pattern"), ctx),
        states=states,
        stale_after=float(raw.get("stale_after", 5)),
        retry_delay=float(raw.get("retry_delay", 5)),
    )


def _parse_health(raw, ctx: str) -> HealthDef | None:
    if not raw:
        return None
    ctx = f"{ctx} health"
    h = HealthDef(
        port=int(raw["port"]) if raw.get("port") else None,
        host=str(raw.get("host", "127.0.0.1")),
        http=str(raw["http"]) if raw.get("http") else None,
        log_ready=_regex(raw.get("log_ready"), ctx),
        log_unhealthy=_regex(raw.get("log_unhealthy"), ctx),
        interval=float(raw.get("interval", 5)),
        timeout=float(raw.get("timeout", 2)),
        grace=float(raw.get("grace", 60)),
    )
    if not (h.port or h.http or h.log_ready or h.log_unhealthy):
        raise ConfigError(f"{ctx}: needs at least one of port, http, log_ready, log_unhealthy")
    return h


def _parse_link(raw: dict, ctx: str) -> LinkDef:
    link = LinkDef(
        label=str(raw.get("label") or "Open"),
        url=str(raw["url"]) if raw.get("url") else None,
        from_log=_regex(raw.get("from_log"), f"{ctx} link"),
    )
    if not (link.url or link.from_log):
        raise ConfigError(f"{ctx}: link '{link.label}' needs url or from_log")
    return link


def _parse_service(raw: dict, profiles: dict, idx: int) -> ServiceDef:
    ctx = f"services[{idx}]"
    sid = _check_id(_req(raw, "id", ctx), ctx)
    ctx = f"service '{sid}'"
    rt = _build_runtime(raw, profiles, ctx)

    actions = [_parse_action(a, rt, ctx) for a in _as_list(raw.get("actions"))]
    monitors = [_parse_monitor(m, rt, ctx) for m in _as_list(raw.get("monitors"))]
    for kind, items in (("action", actions), ("monitor", monitors)):
        ids = [i.id for i in items]
        if len(ids) != len(set(ids)):
            raise ConfigError(f"{ctx}: duplicate {kind} ids")

    return ServiceDef(
        id=sid,
        name=str(raw.get("name") or sid),
        command=str(_req(raw, "command", ctx)).strip(),
        runtime=rt,
        description=str(raw.get("description") or ""),
        group=str(raw.get("group") or "General"),
        autostart=bool(raw.get("autostart", False)),
        restart_on_failure=bool(raw.get("restart_on_failure", False)),
        max_restarts=int(raw.get("max_restarts", 3)),
        restart_delay=float(raw.get("restart_delay", 5)),
        stop_timeout=float(raw.get("stop_timeout", 10)),
        detect_external=_regex(raw.get("detect_external"), ctx),
        critical=bool(raw.get("critical", False)),
        health=_parse_health(raw.get("health"), ctx),
        links=[_parse_link(l, ctx) for l in _as_list(raw.get("links"))],
        actions=actions,
        monitors=monitors,
    )


def load_config(path: str | os.PathLike, base_dir: Path) -> tuple[Settings, list[ServiceDef]]:
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    s = data.get("settings") or {}
    log_dir = Path(os.path.expanduser(str(s.get("log_dir", "logs"))))
    if not log_dir.is_absolute():
        log_dir = base_dir / log_dir
    auth = s.get("auth") or {}
    users_file = Path(os.path.expanduser(str(auth.get("users_file", "users.yaml"))))
    if not users_file.is_absolute():
        users_file = path.parent / users_file
    settings = Settings(
        log_dir=log_dir,
        log_buffer_lines=int(s.get("log_buffer_lines", 3000)),
        log_file_max_mb=float(s.get("log_file_max_mb", 5)),
        log_file_backups=int(s.get("log_file_backups", 3)),
        metrics_interval=float(s.get("metrics_interval", 2)),
        auth_enabled=bool(auth.get("enabled", False)),
        users_file=users_file,
        session_hours=float(auth.get("session_hours", 12)),
    )

    # Relative `source` paths in profiles are relative to the config file's folder
    # (e.g. env/pnpm.sh -> config/env/pnpm.sh). Relative paths in a service's own
    # `source` stay relative to that service's cwd.
    profiles = data.get("profiles") or {}
    for prof in profiles.values():
        if prof and prof.get("source"):
            prof["source"] = [
                s if str(s).startswith(("/", "~")) else str(path.parent / str(s))
                for s in _as_list(prof["source"])
            ]
    services = [_parse_service(raw, profiles, i) for i, raw in enumerate(_as_list(data.get("services")))]
    ids = [svc.id for svc in services]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConfigError(f"Duplicate service ids: {', '.join(sorted(dupes))}")
    return settings, services
