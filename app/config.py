"""Flask application settings (override with environment variables)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    BASE_DIR = BASE_DIR
    SERVICES_FILE = os.environ.get("SM_SERVICES_FILE", str(BASE_DIR / "config" / "services.yaml"))
    HOST = os.environ.get("SM_HOST", "0.0.0.0")
    PORT = int(os.environ.get("SM_PORT", "5000"))
    LOG_LEVEL = os.environ.get("SM_LOG_LEVEL", "INFO")
    JSON_SORT_KEYS = False
