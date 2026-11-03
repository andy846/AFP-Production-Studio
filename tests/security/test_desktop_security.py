"""The desktop shell must not weaken the security controls.

The native file dialog is a convenience. It returns a path and
nothing else, and the path still has to pass the same allow-list and
the same "never overwrite the source AFP" rule as a path typed into
the browser.
"""

from __future__ import annotations

import pytest

from afpstudio.desktop import DesktopBridge, LocalServer
from afpstudio.security.paths import PathError, resolve_under_roots, safe_output_path


@pytest.fixture
def desktop_config(tmp_path, monkeypatch):
    """A configuration that keeps everything inside ``tmp_path``."""
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    return Config()


class _FakeWindow:
    """A window that returns a fixed path from its dialog."""

    def __init__(self, result):
        self.result = result

    def create_file_dialog(self, dialog_type, **kwargs):
        return self.result


def test_the_desktop_server_binds_only_to_the_loopback(desktop_config):
    server = LocalServer(desktop_config)
    url = server.start()
    try:
        assert url.startswith("http://127.0.0.1:")
        assert "0.0.0.0" not in url
    finally:
        server.stop()


def test_the_bridge_offers_no_way_to_open_a_document():
    """The bridge exposes dialogs and nothing else."""
    public = [name for name in dir(DesktopBridge) if not name.startswith("_")]
    assert sorted(public) == [
        "attach",
        "pick_afp_file",
        "pick_form_definition",
        "save_report_file",
    ]


def test_a_chosen_path_still_has_to_pass_the_allow_list(tmp_path):
    """A path from the native dialog is not trusted any more than one
    that was typed in."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "job.afp"
    inside.write_bytes(b"x")
    outside = tmp_path / "outside.afp"
    outside.write_bytes(b"x")

    assert resolve_under_roots(str(inside), [allowed]) == inside.resolve()

    with pytest.raises(PathError):
        resolve_under_roots(str(outside), [allowed])


def test_a_chosen_export_path_still_cannot_overwrite_the_source(tmp_path):
    root = tmp_path
    source = root / "job.afp"
    source.write_bytes(b"original")

    with pytest.raises(PathError):
        safe_output_path(str(source), [root], source=source)


def test_the_bridge_only_returns_paths(tmp_path):
    bridge = DesktopBridge()
    bridge.attach(_FakeWindow((str(tmp_path / "job.afp"),)))

    result = bridge.pick_afp_file()
    assert set(result) == {"cancelled", "path"}
    assert result["cancelled"] is False
    # The bridge hands back a path. It does not read the file, so a
    # file it could not read is still handed back.
    assert result["path"].endswith("job.afp")


def test_the_desktop_mode_does_not_widen_the_allowed_roots(tmp_path, monkeypatch):
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_MODE", "desktop")
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    config = Config()

    assert config.allowed_roots() == [tmp_path.resolve()]


def test_the_desktop_mode_keeps_the_file_size_limit(tmp_path, monkeypatch):
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_MODE", "desktop")
    monkeypatch.setenv("AFP_STUDIO_MAX_LOCAL_FILE_BYTES", "1024")
    config = Config()

    assert config.max_local_file_bytes == 1024
