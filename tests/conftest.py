"""Shared fixtures and import setup for the Studio test suite.

The tests run against the application source and the vendored readAFP
tree. Both are put on ``sys.path`` here so ``pytest`` works from a clean
checkout without environment variables.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
READAFP_SRC = BACKEND / "vendor" / "readafp" / "src"
READAFP_TESTS = BACKEND / "vendor" / "readafp" / "tests"
CORPUS = BACKEND / "vendor" / "readafp" / "testdata" / "alpheus-corpus"

for path in (str(BACKEND), str(READAFP_SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from afpstudio.testing import synthetic  # noqa: E402
from afpstudio.viewer.index import build_index  # noqa: E402
from afpstudio.preflight.media import analyse_media  # noqa: E402


@pytest.fixture
def corpus_dir() -> Path:
    """The publicly licensed AFP corpus."""
    return CORPUS


@pytest.fixture
def media_corpus(corpus_dir: Path) -> Path:
    """The corpus files that carry real media controls."""
    return corpus_dir / "external"


@pytest.fixture
def sample_afp(tmp_path: Path) -> Path:
    """A simple synthetic document with one form map and one medium map."""
    path = tmp_path / "simplex.afp"
    path.write_bytes(synthetic.simplex_document(3))
    return path


@pytest.fixture
def indexed(tmp_path):
    """Build an index and a media analysis for a synthetic document."""

    def build(name: str, data: bytes):
        path = tmp_path / name
        path.write_bytes(data)
        index = build_index(path)
        analysis = analyse_media(index, path)
        return path, index, analysis

    return build


@pytest.fixture
def sample_profile():
    """The bundled sample printer profile."""
    from afpstudio.printer_profiles.schema import validate_profile

    profile_file = (
        BACKEND
        / "afpstudio"
        / "printer_profiles"
        / "profiles"
        / "sample_profile.json"
    )
    import json

    return validate_profile(json.loads(profile_file.read_text(encoding="utf-8")))


@pytest.fixture
def permissive_profile():
    """A profile that maps MAP01 and MAPDUP and applies no rules."""
    from afpstudio.printer_profiles.schema import validate_profile

    return validate_profile(
        {
            "schema_version": "1.0",
            "printer_profile": "Test Printer",
            "media_maps": {
                "MAP01": {
                    "stock_name": "A4 White 80gsm",
                    "tray": "Tray 1",
                    "paper_size": {"width_mm": 210.0, "height_mm": 297.0},
                },
                "MAPDUP": {
                    "stock_name": "A4 White 80gsm",
                    "tray": "Tray 1",
                    "paper_size": {"width_mm": 210.0, "height_mm": 297.0},
                },
            },
            "rules": [],
        }
    )


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """A Flask test client whose allowed roots point at ``tmp_path``."""
    from afpstudio.app import create_app
    from afpstudio.config import Config

    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    config = Config()
    application = create_app(config)
    application.config.update(TESTING=True)
    with application.test_client() as client:
        yield client
