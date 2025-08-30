"""Entrypoint script to run the Flask development server.

Prefer importing *create_app()* from `src` in tests and CLI tooling.
"""

import os

from src import create_app  # noqa: E402


app = create_app()


if __name__ == "__main__":
    cfg = app.config
    app.run(host=cfg["FLASK_HOST"], port=cfg["FLASK_PORT"], debug=cfg["FLASK_DEBUG"])
