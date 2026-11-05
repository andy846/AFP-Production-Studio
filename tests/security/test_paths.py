"""Path validation and safe output."""

from __future__ import annotations

import os

import pytest

from afpstudio.security.paths import (
    PathError,
    atomic_write_bytes,
    resolve_under_roots,
    safe_output_path,
    FileGrants,
)


def test_dialog_grant_is_single_file_single_purpose_and_single_use(tmp_path):
    selected = tmp_path / "selected.afp"
    other = tmp_path / "other.afp"
    selected.write_bytes(b"a")
    other.write_bytes(b"b")
    grants = FileGrants()
    token = grants.issue(selected, "document")
    with pytest.raises(PathError):
        grants.consume(token, other, "document")
    with pytest.raises(PathError):
        grants.consume(token, selected, "document")
    token = grants.issue(selected, "document")
    with pytest.raises(PathError):
        grants.consume(token, selected, "formdef")
    token = grants.issue(selected, "document")
    assert grants.consume(token, selected, "document") == selected.resolve()
    with pytest.raises(PathError):
        grants.consume(token, selected, "document")


@pytest.mark.parametrize("path", [r"\\?\C:\file.afp", r"\\.\PhysicalDrive0"])
def test_device_paths_are_rejected_before_access(path, tmp_path):
    with pytest.raises(PathError):
        resolve_under_roots(path, [tmp_path])


def test_a_file_inside_a_root_is_accepted(tmp_path):
    target = tmp_path / "job.afp"
    target.write_bytes(b"x")
    assert resolve_under_roots(str(target), [tmp_path]) == target.resolve()


def test_a_file_outside_the_roots_is_refused(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    other = tmp_path / "other.afp"
    other.write_bytes(b"x")
    with pytest.raises(PathError):
        resolve_under_roots(str(other), [allowed.resolve()])


def test_traversal_is_refused(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret.afp"
    secret.write_bytes(b"x")
    with pytest.raises(PathError):
        resolve_under_roots(
            str(allowed / ".." / "secret.afp"), [allowed.resolve()]
        )


def test_a_unc_path_is_refused(tmp_path):
    with pytest.raises(PathError):
        resolve_under_roots(r"\\server\share\job.afp", [tmp_path.resolve()])


def test_a_null_byte_is_refused(tmp_path):
    with pytest.raises(PathError):
        resolve_under_roots("job.afp" + chr(0), [tmp_path.resolve()])


def test_a_directory_is_refused(tmp_path):
    with pytest.raises(PathError):
        resolve_under_roots(str(tmp_path), [tmp_path.parent.resolve()])


def test_an_empty_path_is_refused(tmp_path):
    with pytest.raises(PathError):
        resolve_under_roots("", [tmp_path.resolve()])


def test_an_export_may_not_overwrite_the_source(tmp_path):
    source = tmp_path / "job.afp"
    source.write_bytes(b"x")
    with pytest.raises(PathError):
        safe_output_path(str(source), [tmp_path.resolve()], source=source)


def test_an_export_may_not_overwrite_the_source_through_a_hard_link(tmp_path):
    source = tmp_path / "job.afp"
    source.write_bytes(b"x")
    link = tmp_path / "link.afp"
    try:
        os.link(source, link)
    except (OSError, NotImplementedError):
        pytest.skip("Hard links are not available here.")
    with pytest.raises(PathError):
        safe_output_path(str(link), [tmp_path.resolve()], source=source)


def test_an_export_must_be_absolute(tmp_path):
    with pytest.raises(PathError):
        safe_output_path("relative.csv", [tmp_path.resolve()])


def test_an_export_must_be_inside_a_root(tmp_path):
    with pytest.raises(PathError):
        safe_output_path(
            str(tmp_path.parent / "elsewhere.csv"), [tmp_path.resolve()]
        )


def test_atomic_write_leaves_no_partial_file(tmp_path):
    target = tmp_path / "out.csv"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"
    leftovers = [
        name
        for name in os.listdir(tmp_path)
        if name.endswith(".part")
    ]
    assert leftovers == []


def test_atomic_write_replaces_the_previous_file(tmp_path):
    target = tmp_path / "out.csv"
    atomic_write_bytes(target, b"one")
    atomic_write_bytes(target, b"two")
    assert target.read_bytes() == b"two"
