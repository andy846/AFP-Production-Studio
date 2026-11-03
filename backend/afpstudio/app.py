"""The AFP Production Studio web application.

The server binds to the loopback interface by default, serves a strict
content security policy, and never sends document content anywhere.
Everything it needs is on the machine it runs on.
"""

from __future__ import annotations

from flask import Flask, redirect, render_template, session, url_for

from .api import bp as api_blueprint
from .config import CONFIG, Config
from .document_model.model import DocumentStore
from .parser_adapters.alpheus import AlpheusAdapter
from .printer_profiles.store import ProfileStore
from .resources import frontend_dir
from .security.csrf import issue_token
from .security.headers import apply_headers
from .viewer.cache import PageCache
from .viewer.jobs import JobManager

FRONTEND_DIR = frontend_dir()


def create_app(config: Config | None = None) -> Flask:
    """Build the Flask application."""
    config = config or CONFIG
    config.ensure_dirs()

    app = Flask(
        __name__,
        template_folder=str(FRONTEND_DIR / "templates"),
        static_folder=str(FRONTEND_DIR / "static"),
    )
    app.config.update(
        SECRET_KEY=_secret_key(config),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=config.max_upload_bytes,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=False,
    )

    profiles = ProfileStore(config.profile_dir)
    profiles.ensure_sample()

    app.extensions["studio"] = {
        "config": config,
        "documents": DocumentStore(PageCache(config.page_cache_bytes)),
        "jobs": JobManager(config.max_concurrent_jobs),
        "profiles": profiles,
        "alpheus": AlpheusAdapter(
            jar=config.adapter_jar,
            java_executable=config.java_executable,
            timeout_seconds=config.adapter_timeout_seconds,
            max_heap=config.adapter_max_heap,
            max_output_bytes=config.adapter_max_output_bytes,
        ),
    }

    app.register_blueprint(api_blueprint)

    @app.after_request
    def _harden(response):
        return apply_headers(response, config.allow_external_resources)

    @app.get("/")
    def index():
        if "csrf_token" not in session:
            issue_token()
        return render_template(
            "index.html",
            csrf_token=session.get("csrf_token", ""),
            version=_version(),
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/favicon.ico")
    def favicon():
        """Older browsers probe /favicon.ico even when a SVG icon is linked."""
        return redirect(url_for("static", filename="favicon.svg"))

    return app


def _version() -> str:
    from . import __version__

    return __version__


def _secret_key(config: Config) -> str:
    """A per-install session key kept in the data directory.

    The key never leaves the machine, so the session cookie cannot be
    forged from another host.
    """
    key_file = config.data_dir / "session.key"
    if key_file.exists():
        return key_file.read_text(encoding="ascii").strip()
    import secrets

    key = secrets.token_hex(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key, encoding="ascii")
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


app = create_app()


def main() -> None:
    """Run the development server."""
    config = CONFIG
    if not config.is_loopback:
        print(
            "WARNING: the server is not bound to the loopback interface. "
            "Document content will be reachable from the network."
        )
    app.run(host=config.host, port=config.port, debug=config.debug)


if __name__ == "__main__":
    main()
