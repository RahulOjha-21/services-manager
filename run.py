#!/usr/bin/env python3
"""Entry point:  python3 run.py   ->  http://<host>:5000"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    # Single process, no reloader: the manager owns child processes and must exist only once.
    app.run(host=app.config["HOST"], port=app.config["PORT"],
            threaded=True, debug=False, use_reloader=False)
