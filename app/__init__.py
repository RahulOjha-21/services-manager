"""Services Manager - Flask application factory."""
import atexit
import logging
import signal
import socket
import sys

from flask import Flask, current_app

from .config import Config
from .core import ServiceManager


def get_manager() -> ServiceManager:
    return current_app.extensions["service_manager"]


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    logging.basicConfig(level=app.config["LOG_LEVEL"],
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    manager = ServiceManager.from_file(app.config["SERVICES_FILE"], app.config["BASE_DIR"])
    app.extensions["service_manager"] = manager

    from .auth import init_auth
    init_auth(app)

    from .api import api_bp
    from .web import web_bp
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.context_processor
    def _globals():
        return {"hostname": socket.gethostname()}

    manager.start_background()
    atexit.register(manager.shutdown)

    # Managed processes run in their own sessions; stop them on SIGTERM (systemd stop) too.
    def _on_term(signum, frame):
        manager.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_term)
    return app
