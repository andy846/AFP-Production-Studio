"""The desktop shell: the same application inside a native window.

Studio is a local web application. On Windows it can also present
itself as a desktop program: the Flask application is served on an
ephemeral loopback port and a native window (WebView2 on Windows)
loads it.

Nothing about the application changes. The same views, the same API,
the same path validation and the same content security policy apply.
Only the presentation differs, and the port is chosen by the operating
system rather than fixed, so two copies of Studio can run side by
side.

The window is closed, the server is shut down, so no stray process is
left behind.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .app import create_app
from .config import CONFIG, Config
from .security.paths import FileGrants, PathError, resolve_selected_file

#: The title of the native window.
WINDOW_TITLE = "AFP Production Studio"

#: The default size of the window, in logical pixels.
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900

#: The smallest useful size, in logical pixels.
WINDOW_MIN_WIDTH = 800
WINDOW_MIN_HEIGHT = 600


class DesktopBridge:
    """The small API the window may call.

    The bridge exposes native file selection, report destination selection
    and the startup file. Native input selection also returns a one-use
    capability limited to the selected file and purpose.

    The bridge never opens a document and never writes a file. It
    hands the selection back to the page, which passes it to the HTTP API.
    The API verifies the capability and the file path, and retains the
    "never overwrite the source AFP" rule.
    """

    def __init__(self, window: Any = None, startup_file: str | None = None, file_grants=None, startup_error: str | None = None) -> None:
        self._window = window
        self._lock = threading.Lock()
        self._startup_file = startup_file
        self._file_grants = file_grants
        self._startup_error = startup_error

    def consume_startup_file(self) -> str | None:
        """Return the launch argument once, without exposing it in a URL."""
        with self._lock:
            path = self._startup_file
            self._startup_file = None
            error = self._startup_error
            self._startup_error = None
            if error:
                raise RuntimeError(error)
            return path

    def attach(self, window: Any) -> None:
        """Remember the window so the dialogs have somewhere to open."""
        with self._lock:
            self._window = window

    def _dialog(self, kind: str, **kwargs: Any) -> list[str]:
        """Open a native file dialog and return the chosen paths."""
        with self._lock:
            window = self._window
        if window is None:
            return []
        try:
            import webview
        except ImportError as exc:
            # A window without pywebview cannot show a dialog. Say so
            # rather than pretending the operator cancelled it.
            raise RuntimeError(
                "The native file dialog needs pywebview, which is not "
                "installed."
            ) from exc
        # FileDialog is the current spelling; the older OPEN_DIALOG and
        # SAVE_DIALOG constants are deprecated.
        dialog_type = (
            webview.FileDialog.SAVE if kind == "save" else webview.FileDialog.OPEN
        )
        result = window.create_file_dialog(dialog_type, **kwargs)
        if not result:
            return []
        return [str(item) for item in result]

    def pick_afp_file(self, multiple: bool = False) -> dict[str, Any]:
        """Ask the operator to choose up to eight AFP files."""
        if type(multiple) is not bool:
            raise ValueError("Invalid selection mode.")
        paths = self._dialog(
            "open",
            allow_multiple=multiple,
            file_types=("AFP files (*.afp)", "All files (*.*)"),
        )
        if not paths:
            return {"cancelled": True, "files": []} if multiple else {"cancelled": True, "path": ""}
        if multiple:
            if len(paths) > 8:
                raise ValueError("Select no more than eight files at a time.")
            # Validate the complete selection before issuing any capabilities.
            if self._file_grants is not None:
                for path in paths:
                    resolve_selected_file(path)
            files = [{"path": path} for path in paths]
            if self._file_grants is not None:
                for item in files:
                    item["grant"] = self._file_grants.issue(item["path"], "document")
            return {"cancelled": False, "files": files}
        result = {"cancelled": False, "path": paths[0]}
        if self._file_grants is not None:
            result["grant"] = self._file_grants.issue(paths[0], "document")
        return result

    def pick_form_definition(self) -> dict[str, Any]:
        """Ask the operator to choose an external form definition."""
        paths = self._dialog(
            "open",
            allow_multiple=False,
            file_types=("AFP files (*.afp)", "All files (*.*)"),
        )
        if not paths:
            return {"cancelled": True, "path": ""}
        result = {"cancelled": False, "path": paths[0]}
        if self._file_grants is not None:
            result["grant"] = self._file_grants.issue(paths[0], "formdef")
        return result

    def save_report_file(self, default_name: str = "", file_type: str = "csv") -> dict[str, Any]:
        """Ask the operator where to write an exported report."""
        requested = str(file_type).lower()
        suffix = requested if requested in ("csv", "xlsx", "afp", "png", "pdf", "tiff") else "csv"
        paths = self._dialog(
            "save",
            allow_multiple=False,
            save_filename=default_name or ("report." + suffix),
            file_types=(f"{suffix.upper()} files (*.{suffix})",),
        )
        if not paths:
            return {"cancelled": True, "path": ""}
        return {"cancelled": False, "path": paths[0]}


class LocalServer:
    """The Flask application served on a loopback port.

    The port is chosen by the operating system (``port=0``), so it is
    never the one a browser would hit by accident and two instances
    never collide.
    """

    def __init__(self, config: Config | None = None, startup_file: Path | None = None, file_grants=None) -> None:
        self.config = config or CONFIG
        self.startup_file = startup_file
        self.file_grants = file_grants
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start serving and return the URL the window should load."""
        from werkzeug.serving import make_server
        from .security.access_log import LocalRequestHandler

        app = create_app(self.config, startup_file=self.startup_file, file_grants=self.file_grants)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=LocalRequestHandler)
        self._server = server
        thread = threading.Thread(
            target=server.serve_forever,
            name="afpstudio-desktop-http",
            daemon=True,
        )
        thread.start()
        self._thread = thread
        return f"http://127.0.0.1:{server.server_port}/"

    def stop(self) -> None:
        """Stop serving. Safe to call more than once."""
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=5)

    @property
    def port(self) -> int | None:
        """The port in use, or ``None`` when the server is stopped."""
        return getattr(self._server, "server_port", None)


def run_desktop(config: Config | None = None, startup_file: str | None = None) -> int:
    """Open the application in a native window.

    Returns a process exit code: zero when the window was closed
    normally, and a non-zero code when the desktop shell could not be
    started at all.
    """
    try:
        import webview
    except ImportError:
        print(
            "The desktop shell needs pywebview. Install it with:\n"
            "    pip install pywebview==6.2.1\n"
            "or run the application in a browser with:\n"
            "    python -m afpstudio"
        )
        return 2

    approved_file = None
    startup_error = None
    if startup_file:
        try:
            approved_file = resolve_selected_file(startup_file)
        except (OSError, ValueError, PathError) as exc:
            startup_error = str(exc)
    grants = FileGrants()
    server = LocalServer(config, startup_file=approved_file, file_grants=grants)
    url = server.start()
    print(f"AFP Production Studio is serving on {url}")
    print("Close the window to stop it.")

    bridge = DesktopBridge(startup_file=str(approved_file) if approved_file else startup_file, file_grants=grants, startup_error=startup_error)
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        js_api=bridge,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
    )
    bridge.attach(window)

    def _on_closed() -> None:
        # Closing the window ends the session: the server stops with
        # it, so no process and no port are left behind.
        server.stop()

    window.events.closed += _on_closed

    try:
        webview.start()
    finally:
        server.stop()
    return 0


def desktop_available() -> bool:
    """Is the desktop shell usable on this machine?"""
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True
