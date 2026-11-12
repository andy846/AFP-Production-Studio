"""The desktop shell, end to end, without opening a real window.

The window is replaced by a recorder. The test then checks that the
desktop mode starts a server, points the window at it, and that the
application really answers on that address. The same code path runs in
the browser and in the frozen build; only the window is different.
"""

from __future__ import annotations

import sys
import types
import urllib.request

import pytest


class _FakeEvent:
    """Collects the callbacks the shell registers, like pywebview does."""

    def __init__(self):
        self._items = []

    def __iadd__(self, item):
        self._items.append(item)
        return self

    def __iter__(self):
        return iter(self._items)

    def fire(self, *args, **kwargs):
        for item in list(self._items):
            item(*args, **kwargs)


class _FakeEvents:
    """The events a window offers."""

    def __init__(self):
        self.closed = _FakeEvent()
        self.loaded = _FakeEvent()


class _FakeWindow:
    """Stands in for a pywebview window."""

    def __init__(self, title, url, **kwargs):
        self.title = title
        self.url = url
        self.kwargs = kwargs
        self.events = _FakeEvents()
        self.js_api = kwargs.get("js_api")

    def create_file_dialog(self, dialog_type, **kwargs):
        return ()


@pytest.fixture
def fake_webview(monkeypatch):
    """Install a fake ``webview`` module and record the window.

    The fake ``start`` runs the checks while the server is still
    alive, because the real call blocks until the window is closed.
    """
    module = types.ModuleType("webview")
    module.OPEN_DIALOG = 10
    module.SAVE_DIALOG = 30
    module.FileDialog = types.SimpleNamespace(OPEN=10, SAVE=30)
    created = []
    checked = []
    start_options = []

    def create_window(title, url, **kwargs):
        window = _FakeWindow(title, url, **kwargs)
        created.append(window)
        return window

    def start(*args, **kwargs):
        start_options.append(kwargs)
        for window in created:
            with urllib.request.urlopen(window.url, timeout=10) as response:
                checked.append(("index", response.status))
            with urllib.request.urlopen(
                window.url + "api/health", timeout=10
            ) as response:
                checked.append(("health", response.read().decode("utf-8")))

    module.create_window = create_window
    module.start = start
    monkeypatch.setitem(sys.modules, "webview", module)
    return {"windows": created, "checked": checked, "start_options": start_options}


@pytest.fixture
def desktop_env(tmp_path, monkeypatch):
    """A configuration that keeps everything inside ``tmp_path``."""
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))


def test_the_desktop_mode_opens_a_window_on_a_loopback_url(
    fake_webview, desktop_env
):
    from afpstudio.desktop import run_desktop

    assert run_desktop() == 0
    windows = fake_webview["windows"]
    assert len(windows) == 1
    assert windows[0].url.startswith("http://127.0.0.1:")
    assert windows[0].title


def test_the_desktop_window_serves_the_application(fake_webview, desktop_env):
    from afpstudio.desktop import run_desktop

    run_desktop()
    checked = fake_webview["checked"]
    assert ("index", 200) in checked


def test_desktop_uses_the_bundled_icon(fake_webview, desktop_env):
    from pathlib import Path
    from afpstudio.desktop import run_desktop
    from afpstudio.resources import frontend_dir

    run_desktop()
    icon = Path(fake_webview["start_options"][0]["icon"])
    assert icon == frontend_dir() / "static" / "studio-icon.ico"
    assert icon.is_file()


def test_the_desktop_window_serves_the_health_endpoint(
    fake_webview, desktop_env
):
    from afpstudio.desktop import run_desktop

    run_desktop()
    checked = fake_webview["checked"]
    health = [payload for kind, payload in checked if kind == "health"]
    assert health and "ok" in health[0]


def test_the_desktop_window_carries_the_file_dialog_bridge(
    fake_webview, desktop_env
):
    from afpstudio.desktop import run_desktop

    run_desktop()
    bridge = fake_webview["windows"][0].js_api
    assert bridge is not None
    assert hasattr(bridge, "pick_afp_file")
    assert hasattr(bridge, "save_report_file")


def test_closing_the_window_stops_the_server(fake_webview, desktop_env):
    from afpstudio.desktop import run_desktop

    run_desktop()
    window = fake_webview["windows"][0]
    url = window.url

    # The closed callback is what stops the server when the operator
    # closes the window.
    assert list(window.events.closed)
    window.events.closed.fire()

    with pytest.raises(Exception):
        urllib.request.urlopen(url, timeout=5)


def test_desktop_exposes_startup_file_once(fake_webview, desktop_env, tmp_path):
    from afpstudio.desktop import run_desktop

    source = tmp_path / "document with spaces.afp"
    source.write_bytes(b"example")
    assert run_desktop(startup_file=str(source)) == 0
    bridge = fake_webview["windows"][0].js_api
    assert bridge.consume_startup_file() == str(source.resolve())
    assert bridge.consume_startup_file() is None


def test_missing_startup_file_reports_error(fake_webview, desktop_env, tmp_path):
    from afpstudio.desktop import run_desktop

    assert run_desktop(startup_file=str(tmp_path / "missing.afp")) == 0
    bridge = fake_webview["windows"][0].js_api
    with pytest.raises(RuntimeError, match="not found|unavailable"):
        bridge.consume_startup_file()
