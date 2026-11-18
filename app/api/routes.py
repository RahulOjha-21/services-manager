"""REST + Server-Sent-Events API used by the web UI (also usable from scripts/curl).

POST requests must send the header  X-Requested-With: ServicesManager
(and a logged-in session cookie when authentication is enabled).
"""
import json

from flask import Response, jsonify, request, send_file, stream_with_context

from . import api_bp
from .. import get_manager
from ..auth import current_user, current_username, require_role
from ..core import ConfigError, ServiceError, ServiceNotFound


def ok(message: str = "", **extra):
    return jsonify({"ok": True, "message": message, **extra})


@api_bp.errorhandler(ServiceNotFound)
def _not_found(e):
    return jsonify({"ok": False, "message": f"Unknown service '{e.args[0]}'"}), 404


@api_bp.errorhandler(ServiceError)
def _service_error(e):
    return jsonify({"ok": False, "message": str(e)}), 409


@api_bp.errorhandler(ConfigError)
def _config_error(e):
    return jsonify({"ok": False, "message": f"Configuration not reloaded: {e}"}), 400


# ---------------------------------------------------------------- status --- #
@api_bp.get("/me")
def me():
    return jsonify({"ok": True, "user": current_user()})


@api_bp.get("/services")
def list_services():
    return jsonify({"ok": True, "services": [s.status() for s in get_manager().all()]})


@api_bp.get("/services/<sid>")
def get_service(sid):
    return jsonify({"ok": True, "service": get_manager().get(sid).status()})


# ------------------------------------------------------------- lifecycle --- #
@api_bp.post("/services/<sid>/start")
@require_role("operator")
def start_service(sid):
    svc = get_manager().get(sid)
    svc.start(user=current_username())
    return ok(f"{svc.defn.name} started", service=svc.status())


@api_bp.post("/services/<sid>/stop")
@require_role("operator")
def stop_service(sid):
    svc = get_manager().get(sid)
    if not svc.process.is_alive() and svc.external_pids(fresh=True):
        pids = svc.stop_external(user=current_username())
        return ok(f"Stopping external process {pids}", service=svc.status())
    svc.stop(user=current_username())
    return ok(f"Stopping {svc.defn.name}...", service=svc.status())


@api_bp.post("/services/<sid>/restart")
@require_role("operator")
def restart_service(sid):
    svc = get_manager().get(sid)
    svc.restart(user=current_username())
    return ok(f"Restarting {svc.defn.name}...", service=svc.status())


@api_bp.post("/services/<sid>/actions/<aid>")
@require_role("operator")
def run_action(sid, aid):
    svc = get_manager().get(sid)
    svc.run_action(aid, user=current_username())
    return ok(f"{svc.actions[aid].defn.label} sent", service=svc.status())


# ----------------------------------------------------- (#4) config reload --- #
@api_bp.post("/config/reload")
@require_role("admin")
def reload_config():
    result = get_manager().reload(user=current_username())
    changed = {k: v for k, v in result.items() if v and k != "unchanged"}
    msg = "; ".join(f"{k}: {', '.join(v)}" for k, v in changed.items()) or "No changes found"
    return ok(f"Configuration reloaded. {msg}", result=result)


# ------------------------------------------------------- (#8) event log --- #
@api_bp.get("/events")
def list_events():
    args = request.args
    events = get_manager().events.list(
        service=args.get("service") or None, kind=args.get("kind") or None,
        level=args.get("level") or None, limit=min(args.get("limit", 200, type=int), 2000))
    return jsonify({"ok": True, "events": events})


# ------------------------------------------------------------------ logs --- #
@api_bp.get("/services/<sid>/logs")
def get_logs(sid):
    svc = get_manager().get(sid)
    since = request.args.get("since", 0, type=int)
    limit = request.args.get("limit", 1000, type=int)
    return jsonify({"ok": True, "lines": svc.logs.since(since, limit), "last_seq": svc.logs.last_seq})


@api_bp.get("/services/<sid>/logs/stream")
def stream_logs(sid):
    """SSE stream of new log lines. Resumes from ?since= or the Last-Event-ID header."""
    svc = get_manager().get(sid)
    since = max(request.args.get("since", 0, type=int),
                request.headers.get("Last-Event-ID", 0, type=int))
    if since > svc.logs.last_seq:      # manager restarted -> sequence was reset
        since = 0

    def generate(since=since):
        yield "retry: 3000\n\n"
        while True:
            lines = svc.logs.since(since)
            if lines:
                since = lines[-1]["seq"]
                yield f"id: {since}\ndata: {json.dumps(lines)}\n\n"
            elif not svc.logs.wait_for(since, timeout=15):
                yield ": keep-alive\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api_bp.post("/services/<sid>/logs/clear")
@require_role("operator")
def clear_logs(sid):
    get_manager().get(sid).logs.clear()
    return ok("Console cleared")


@api_bp.get("/services/<sid>/logs/download")
def download_logs(sid):
    svc = get_manager().get(sid)
    path = svc.logs.file_path
    if not path or not path.exists():
        return jsonify({"ok": False, "message": "No log file yet"}), 404
    return send_file(path, as_attachment=True, download_name=f"{sid}.log", mimetype="text/plain")
