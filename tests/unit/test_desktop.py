"""The desktop shell: the server it starts and the resources it finds.

The desktop mode is the same application as the browser mode. These
tests check the parts that are specific to it: that the server binds
to the loopback interface on an operating-system chosen port, that it
really serves the application, that it stops when it is told to, and
that the bundled resources are found both in a source tree and in a
frozen build.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

import pytest

from afpstudio import resources
from afpstudio.desktop import (
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    DesktopBridge,
    LocalServer,
)


@pytest.fixture
def desktop_config(tmp_path, monkeypatch):
    """A configuration that keeps everything inside ``tmp_path``."""
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    return Config()


def test_the_server_binds_to_the_loopback_interface(desktop_config):
    server = LocalServer(desktop_config)
    url = server.start()
    try:
        assert url.startswith("http://127.0.0.1:")
        assert server.port and server.port > 0
    finally:
        server.stop()


def test_the_server_uses_an_ephemeral_port_not_the_configured_one(
    desktop_config,
):
    desktop_config.port = 8787
    server = LocalServer(desktop_config)
    url = server.start()
    try:
        assert server.port != 8787
        assert str(server.port) in url
    finally:
        server.stop()


def test_the_server_actually_serves_the_application(desktop_config):
    server = LocalServer(desktop_config)
    url = server.start()
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode("utf-8")
            status = response.status
        assert status == 200
        assert "AFP Production Studio" in body
    finally:
        server.stop()


def test_the_health_endpoint_answers_on_the_desktop_port(desktop_config):
    server = LocalServer(desktop_config)
    url = server.start()
    try:
        with urllib.request.urlopen(url + "api/health", timeout=10) as response:
            payload = response.read().decode("utf-8")
        assert '"status"' in payload and "ok" in payload
    finally:
        server.stop()


def test_stopping_the_server_releases_the_port(desktop_config):
    server = LocalServer(desktop_config)
    url = server.start()
    server.stop()

    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        urllib.request.urlopen(url, timeout=5)


def test_stopping_twice_is_harmless(desktop_config):
    server = LocalServer(desktop_config)
    server.start()
    server.stop()
    server.stop()
    assert server.port is None


def test_the_bridge_returns_a_cancelled_result_without_a_window():
    bridge = DesktopBridge()
    assert bridge.pick_afp_file() == {"cancelled": True, "path": ""}
    assert bridge.pick_form_definition() == {"cancelled": True, "path": ""}
    assert bridge.save_report_file("out.csv", "csv") == {
        "cancelled": True,
        "path": "",
    }


def test_the_bridge_says_so_when_pywebview_is_missing(monkeypatch):
    """A missing pywebview must not look like a cancelled dialog."""
    import builtins

    real_import = builtins.__import__

    def _no_webview(name, *args, **kwargs):
        if name == "webview":
            raise ImportError("no pywebview here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_webview)

    bridge = DesktopBridge()
    bridge.attach(_FakeWindow(("C:/jobs/task.afp",)))
    with pytest.raises(RuntimeError):
        bridge.pick_afp_file()


class _FakeWindow:
    """A window that records the dialog it was asked to open."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append((dialog_type, kwargs))
        return self.result


def test_the_bridge_returns_the_chosen_path():
    bridge = DesktopBridge()
    window = _FakeWindow(("C:/jobs/task.afp",))
    bridge.attach(window)

    result = bridge.pick_afp_file()
    assert result == {"cancelled": False, "path": "C:/jobs/task.afp"}
    assert window.calls, "the native dialog was never opened"


def test_the_bridge_reports_a_cancelled_dialog():
    bridge = DesktopBridge()
    bridge.attach(_FakeWindow(()))
    assert bridge.pick_afp_file()["cancelled"] is True


def test_the_save_dialog_suggests_the_requested_name():
    bridge = DesktopBridge()
    window = _FakeWindow(("C:/out/report.csv",))
    bridge.attach(window)

    result = bridge.save_report_file("report.csv", "csv")
    assert result["path"] == "C:/out/report.csv"
    _, kwargs = window.calls[0]
    assert kwargs["save_filename"] == "report.csv"


def test_the_window_has_a_usable_size():
    assert WINDOW_WIDTH >= WINDOW_MIN_WIDTH
    assert WINDOW_HEIGHT >= WINDOW_MIN_HEIGHT
    assert "AFP Production Studio" in WINDOW_TITLE


def test_the_frontend_is_found_in_a_source_tree():
    frontend = resources.frontend_dir()
    assert (frontend / "templates" / "index.html").is_file()
    assert (frontend / "static" / "app.js").is_file()


def test_the_frontend_is_found_in_a_frozen_build(monkeypatch, tmp_path):
    frozen = tmp_path / "frozen"
    (frozen / "frontend" / "templates").mkdir(parents=True)
    (frozen / "frontend" / "templates" / "index.html").write_text("x")
    (frozen / "frontend" / "static").mkdir(parents=True)

    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    assert resources.frontend_dir() == frozen / "frontend"
    assert (resources.frontend_dir() / "templates" / "index.html").is_file()


def test_the_sample_profile_is_found_in_a_source_tree():
    source = (
        resources.package_data_dir("afpstudio.printer_profiles")
        / "profiles"
        / "sample_profile.json"
    )
    assert source.is_file()


def test_the_sample_profile_is_found_in_a_frozen_build(monkeypatch, tmp_path):
    frozen = tmp_path / "frozen"
    target = frozen / "afpstudio" / "printer_profiles" / "profiles"
    target.mkdir(parents=True)
    (target / "sample_profile.json").write_text("{}")

    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    source = (
        resources.package_data_dir("afpstudio.printer_profiles")
        / "profiles"
        / "sample_profile.json"
    )
    assert source.is_file()


def test_the_mode_is_read_from_the_environment(monkeypatch):
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_MODE", "desktop")
    assert Config().mode == "desktop"

    monkeypatch.setenv("AFP_STUDIO_MODE", "web")
    assert Config().mode == "web"

    monkeypatch.delenv("AFP_STUDIO_MODE")
    assert Config().mode == "web"


def test_the_desktop_mode_still_uses_the_loopback_interface(monkeypatch):
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_MODE", "desktop")
    config = Config()
    # The desktop shell always serves on 127.0.0.1 whatever the
    # configured host says, but the guard must still hold.
    assert config.is_loopback
