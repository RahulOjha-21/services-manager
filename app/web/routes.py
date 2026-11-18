"""HTML pages. All live data is loaded by JavaScript from the /api endpoints."""
import time

from flask import abort, flash, redirect, render_template, request, session, url_for

from . import web_bp
from .. import get_manager
from ..auth import auth_enabled, current_user, user_store
from ..core import ServiceNotFound


@web_bp.get("/")
def dashboard():
    return render_template("dashboard.html")


@web_bp.get("/services/<sid>")
def service_detail(sid):
    try:
        svc = get_manager().get(sid)
    except ServiceNotFound:
        abort(404)
    return render_template("service.html", service=svc.defn)


@web_bp.get("/events")
def events():
    return render_template("events.html", services=[s.defn for s in get_manager().all()])


# ------------------------------------------------------------ (#13) auth --- #
def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("web.dashboard")


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("web.dashboard"))
    next_url = _safe_next(request.values.get("next"))
    if current_user():
        return redirect(next_url)
    no_users = not user_store().all()
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        user = user_store().verify(name, request.form.get("password", ""))
        events = get_manager().events
        if user:
            session.clear()
            session.permanent = True
            session["user"] = user["name"]
            events.record("login", f"Signed in from {request.remote_addr}", user=user["name"],
                          level="info")
            return redirect(next_url)
        events.record("login_failed", f"Failed sign-in for '{name}' from {request.remote_addr}",
                      user=name or None, level="warning")
        time.sleep(1)                               # slow down password guessing
        flash("Invalid username or password.")
    return render_template("login.html", next=next_url, no_users=no_users)


@web_bp.get("/logout")
def logout():
    user = session.get("user")
    session.clear()
    if user:
        get_manager().events.record("logout", "Signed out", user=user)
    return redirect(url_for("web.login"))
