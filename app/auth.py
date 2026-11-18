"""(#13) Login and roles with the app's own user accounts (not Linux/PC accounts).

Users live in config/users.yaml with hashed passwords and are managed with manage.py.
Roles:  viewer  - read only
        operator - viewer + start / stop / restart / actions / clear console
        admin    - operator + reload configuration
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

import yaml
from flask import current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROLES = {"viewer": 1, "operator": 2, "admin": 3}
ROLE_HELP = {
    "viewer": "Read only",
    "operator": "Start / stop / restart services",
    "admin": "Everything, including reload configuration",
}
PUBLIC_ENDPOINTS = {"web.login", "web.logout", "static"}
LOCAL_USER = {"name": "local", "role": "admin", "auth": False}


class UserStore:
    """users.yaml  ->  users: { name: { password_hash, role, created } }"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._cache: tuple[float, dict] = (-1.0, {})

    def _read(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return {}
        if mtime != self._cache[0]:
            data = yaml.safe_load(self.path.read_text()) or {}
            self._cache = (mtime, data.get("users") or {})
        return self._cache[1]

    def _write(self, users: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("# Managed by manage.py - passwords are hashed.\n"
                       + yaml.safe_dump({"users": users}, sort_keys=True))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def all(self) -> dict:
        with self._lock:
            return dict(self._read())

    def get(self, name: str) -> dict | None:
        u = self.all().get(name)
        return {"name": name, "role": u.get("role", "viewer"), "auth": True} if u else None

    def verify(self, name: str, password: str) -> dict | None:
        u = self.all().get(name)
        if u and check_password_hash(u.get("password_hash", ""), password):
            return self.get(name)
        return None

    def set(self, name: str, password: str | None = None, role: str | None = None) -> None:
        if role and role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        with self._lock:
            users = dict(self._read())
            u = dict(users.get(name) or {"created": time.strftime("%Y-%m-%d %H:%M")})
            if password is not None:
                u["password_hash"] = generate_password_hash(password)
            if role:
                u["role"] = role
            u.setdefault("role", "viewer")
            users[name] = u
            self._write(users)

    def delete(self, name: str) -> bool:
        with self._lock:
            users = dict(self._read())
            if name not in users:
                return False
            del users[name]
            self._write(users)
            return True


# --------------------------------------------------------------------------- #
def load_secret_key(instance_dir: Path) -> str:
    key = os.environ.get("SM_SECRET_KEY")
    if key:
        return key
    path = instance_dir / "secret_key"
    if not path.exists():
        instance_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip()


def auth_enabled() -> bool:
    return current_app.extensions["service_manager"].settings.auth_enabled


def user_store() -> UserStore:
    settings = current_app.extensions["service_manager"].settings
    store = current_app.extensions.get("user_store")
    if store is None or store.path != settings.users_file:
        store = current_app.extensions["user_store"] = UserStore(settings.users_file)
    return store


def current_user() -> dict | None:
    return getattr(g, "user", None)


def current_username() -> str | None:
    u = current_user()
    return u["name"] if u else None


def has_role(role: str) -> bool:
    u = current_user()
    return bool(u) and ROLES.get(u["role"], 0) >= ROLES[role]


def require_role(role: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not has_role(role):
                return jsonify({"ok": False, "message": f"This needs the '{role}' role"}), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def init_auth(app) -> None:
    app.secret_key = load_secret_key(Path(app.config["BASE_DIR"]) / "instance")
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    @app.before_request
    def _authenticate():
        settings = app.extensions["service_manager"].settings
        app.permanent_session_lifetime = timedelta(hours=settings.session_hours)
        is_api = request.path.startswith("/api/")

        # CSRF guard: browsers cannot add this header on cross-site requests.
        if is_api and request.method not in ("GET", "HEAD", "OPTIONS") \
                and request.headers.get("X-Requested-With") != "ServicesManager":
            return jsonify({"ok": False, "message": "Missing request header"}), 403

        if not settings.auth_enabled:
            g.user = LOCAL_USER
            return None
        name = session.get("user")
        g.user = user_store().get(name) if name else None
        if g.user is None and name:
            session.clear()                         # user deleted meanwhile
        if g.user or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if is_api:
            return jsonify({"ok": False, "message": "Login required", "login": True}), 401
        return redirect(url_for("web.login", next=request.full_path.rstrip("?")))

    @app.context_processor
    def _inject_user():
        return {"current_user": current_user(), "auth_enabled": auth_enabled(), "ROLE_HELP": ROLE_HELP}
