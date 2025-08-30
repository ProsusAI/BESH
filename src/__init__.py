import os
from flask import Flask, send_from_directory
from flask_cors import CORS

# Alembic-powered migrations via Flask-Migrate
from flask_migrate import Migrate  # type: ignore

from configs import get_config
from .models.user import db  # type: ignore
from .routes.user import user_bp  # type: ignore
from .routes.batch import batch_bp  # type: ignore
from .routes.files import files_bp  # type: ignore

# Service layer import (initialises background workers)
from .services.batch_manager import init_batch_manager  # noqa: E402


def create_app(config_name: str | None = None) -> Flask:  # noqa: D401
    """Application factory.

    Parameters
    ----------
    config_name: str | None, optional
        Name of the configuration to load. If *None*, value will be resolved
        from the ``FLASK_ENV`` environment variable and default to
        ``development``.

    Returns
    -------
    Flask
        A fully initialised Flask application instance.
    """
    # Resolve configuration
    config_obj = get_config(config_name)

    # Create Flask app – static folder lives inside *src/static*
    static_folder = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, static_folder=static_folder)

    # Load configuration
    config_obj.init_app(app)

    # Enable CORS for all domains – tighten in production
    CORS(app, origins="*")

    # Register blueprints
    app.register_blueprint(user_bp, url_prefix="/api")
    app.register_blueprint(batch_bp, url_prefix="/v1")
    app.register_blueprint(files_bp, url_prefix="/v1")

    # Ensure upload directory exists *after* config is loaded
    os.makedirs(config_obj.UPLOAD_FOLDER, exist_ok=True)

    # Initialise extensions
    db.init_app(app)

    # ------------------------------------------------------------------
    # Simple schema creation – always ensure tables exist on startup
    # ------------------------------------------------------------------
    # NOTE: We intentionally run `db.create_all()` unconditionally to keep the
    # development workflow simple and avoid the need for Alembic migrations.
    # If you prefer proper migrations, remove the following block and run
    # `flask db upgrade` instead.
    with app.app_context():
        db.create_all()

    # Keep Flask-Migrate initialised so migrations can still be used later if
    # desired, but the app no longer depends on them.
    # ------------------------------------------------------------------
    # Set up Alembic migrations (optional – non-destructive schema management)
    Migrate(app, db)

    # NOTE: We reverted to an unconditional `db.create_all()` call for a
    # simpler local setup. Migrations are still available but not required.

    # Kick-off background batch manager
    init_batch_manager(app)

    # ---------- Convenience routes ----------
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve(path: str):  # noqa: D401
        """Serve files from *static* folder or fall back to *index.html*"""
        if path and os.path.exists(os.path.join(app.static_folder, path)):
            return send_from_directory(app.static_folder, path)
        index_path = os.path.join(app.static_folder, "index.html")
        if os.path.exists(index_path):
            return send_from_directory(app.static_folder, "index.html")
        return "index.html not found", 404

    @app.route("/health", methods=["GET"])
    def health():  # noqa: D401
        return "OK"

    return app


__all__ = ["create_app"]
