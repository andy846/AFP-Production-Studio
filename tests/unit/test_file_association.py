"""Windows sends a document path as one argument to the frozen program."""

import sys
from unittest.mock import patch

import pytest

from afpstudio.__main__ import _run


def test_filename_starts_desktop_without_losing_spaces(tmp_path):
    source = tmp_path / "a file.afp"
    with patch("afpstudio.desktop.run_desktop", return_value=0) as run:
        assert _run(["--desktop", str(source)]) == 0
        run.assert_called_once_with(startup_file=str(source))


def test_web_mode_rejects_filename(tmp_path):
    with pytest.raises(SystemExit):
        _run(["--web", str(tmp_path / "file.afp")])


def test_frozen_launcher_defaults_to_desktop_for_a_filename(tmp_path, monkeypatch):
    import afpstudio_launcher
    source = str(tmp_path / "one file.afp")
    monkeypatch.setattr(sys, "argv", ["studio.exe", source])
    with patch("afpstudio.__main__._run", return_value=0) as run:
        assert afpstudio_launcher.main() == 0
        run.assert_called_once_with(["--desktop", source])
