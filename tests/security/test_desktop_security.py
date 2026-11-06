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
        "consume_startup_file",
        "pick_afp_file",
        "pick_form_definition",
        "pick_report_folder",
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


def test_launch_argument_authorizes_only_that_file(tmp_path, monkeypatch):
    from afpstudio.app import create_app
    from afpstudio.config import Config
    from afpstudio.security.csrf import HEADER_NAME
    from afpstudio.testing.synthetic import simplex_document

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    source = other_dir / "selected.afp"
    other = other_dir / "neighbour.afp"
    source.write_bytes(simplex_document(1))
    other.write_bytes(simplex_document(1))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    app = create_app(Config(), startup_file=source.resolve())
    with app.test_client() as client:
        token = client.get("/api/csrf").get_json()["token"]
        headers = {HEADER_NAME: token}
        response = client.post("/api/documents/open", json={"path": str(other)}, headers=headers)
        assert response.status_code == 400
        response = client.post("/api/documents/open", json={"path": str(source)}, headers=headers)
        assert response.status_code == 200


def test_native_selection_grant_opens_only_selected_file(tmp_path, monkeypatch):
    from afpstudio.app import create_app
    from afpstudio.config import Config
    from afpstudio.security.csrf import HEADER_NAME
    from afpstudio.security.paths import FileGrants
    from afpstudio.testing.synthetic import simplex_document

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    selected = other_dir / "selected.afp"
    neighbour = other_dir / "neighbour.afp"
    selected.write_bytes(simplex_document(1))
    neighbour.write_bytes(simplex_document(1))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    grants = FileGrants()
    app = create_app(Config(), file_grants=grants)
    with app.test_client() as client:
        token = client.get("/api/csrf").get_json()["token"]
        headers = {HEADER_NAME: token}
        grant = grants.issue(selected, "document")
        response = client.post("/api/documents/open", json={"path": str(neighbour), "path_grant": grant}, headers=headers)
        assert response.status_code == 400
        grant = grants.issue(selected, "document")
        response = client.post("/api/documents/open", json={"path": str(selected), "path_grant": grant}, headers=headers)
        assert response.status_code == 200
        response = client.post("/api/documents/open", json={"path": str(selected), "path_grant": grant}, headers=headers)
        assert response.status_code == 400


def test_native_file_dialog_issues_a_purpose_bound_grant(tmp_path):
    from afpstudio.security.paths import FileGrants

    source = tmp_path / "file with spaces.afp"
    source.write_bytes(b"test")
    grants = FileGrants()
    bridge = DesktopBridge(file_grants=grants)
    bridge.attach(_FakeWindow((str(source),)))
    result = bridge.pick_form_definition()
    assert result["path"] == str(source)
    assert grants.consume(result["grant"], result["path"], "formdef") == source.resolve()


def test_native_report_folder_selection_is_one_use_and_purpose_bound(tmp_path):
    from afpstudio.security.paths import FileGrants

    grants = FileGrants()
    bridge = DesktopBridge(_FakeWindow((str(tmp_path),)), file_grants=grants)
    selection = bridge.pick_report_folder()
    assert not selection["cancelled"]
    assert grants.consume(selection["grant"], selection["path"], "folder-report") == tmp_path.resolve()
    with pytest.raises(PathError):
        grants.consume(selection["grant"], selection["path"], "folder-report")
    selection = bridge.pick_report_folder()
    with pytest.raises(PathError):
        grants.consume(selection["grant"], selection["path"], "document")


def test_multiple_selection_grants_are_one_use_and_file_specific(tmp_path):
    from afpstudio.security.paths import FileGrants
    files = [tmp_path / "one.afp", tmp_path / "two.afp"]
    for path in files:
        path.write_bytes(b"synthetic")
    grants = FileGrants()
    bridge = DesktopBridge(file_grants=grants)
    window = _FakeWindow(tuple(str(path) for path in files))
    bridge.attach(window)
    result = bridge.pick_afp_file(multiple=True)
    assert [item["path"] for item in result["files"]] == list(map(str, files))
    first, second = result["files"]
    with pytest.raises(PathError):
        grants.consume(first["grant"], second["path"], "document")
    assert grants.consume(second["grant"], second["path"], "document") == files[1].resolve()


def test_multi_picker_cancel_does_not_issue_grants():
    bridge = DesktopBridge()
    bridge.attach(_FakeWindow(()))
    assert bridge.pick_afp_file(multiple=True) == {"cancelled": True, "files": []}
    with pytest.raises(ValueError):
        bridge.pick_afp_file(multiple="true")


def test_multiple_selection_rejects_over_eight_files(tmp_path):
    bridge = DesktopBridge()
    bridge.attach(_FakeWindow(tuple(str(tmp_path / f"{i}.afp") for i in range(9))))
    with pytest.raises(ValueError):
        bridge.pick_afp_file(multiple=True)


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
