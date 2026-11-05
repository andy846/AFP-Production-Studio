"""The Java Alpheus adapter, when it has been built.

The adapter is an optional component: Studio works without it. These
tests therefore skip rather than fail when the jar is absent, but they
do run in a build that has the jar.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from afpstudio.parser_adapters.alpheus import AlpheusAdapter
from afpstudio.parser_adapters.contract import compare_with_local
from afpstudio.viewer.index import build_index
from afpstudio.preflight.media import analyse_media


def find_jar() -> Path | None:
    """Locate the adapter jar, if one has been built."""
    env = os.environ.get("AFP_STUDIO_ALPHEUS_JAR")
    if env:
        candidate = Path(env)
        if candidate.is_file():
            return candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "alpheus-adapter" / "build" / "alpheus-adapter.jar"
        if candidate.is_file():
            return candidate
    return None


JAR = find_jar()
JAVA = os.environ.get("AFP_STUDIO_JAVA") or shutil.which("java")

requires_jar = pytest.mark.skipif(
    JAR is None,
    reason=(
        "The Alpheus adapter jar has not been built. Run "
        "scripts/setup_deps.ps1 to build it."
    ),
)


@requires_jar
def test_the_adapter_agrees_with_the_local_parser(tmp_path):
    from afpstudio.testing import synthetic

    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(3))

    adapter = AlpheusAdapter(jar=JAR, java_executable=JAVA or "java")
    result = adapter.parse(path)

    index = build_index(path)
    analysis = analyse_media(index, path)

    assert result.complete is True
    assert result.contract_version == "2.0.0"
    assert len(result.field_index) == index.field_count * 4
    assert result.page_count == index.page_count
    assert compare_with_local(result, index.page_count, sorted(analysis.medium_maps)) == []
    assert compare_with_local(result, index.page_count, sorted(analysis.medium_maps), index=index, analysis=analysis) == []


@requires_jar
def test_explicit_duplex_is_independently_compared(tmp_path):
    from afpstudio.testing import synthetic
    path = tmp_path / "duplex.afp"
    path.write_bytes(synthetic.duplex_document(2))
    result = AlpheusAdapter(jar=JAR, java_executable=JAVA or "java").parse(path)
    index = build_index(path)
    analysis = analyse_media(index, path)
    assert result.medium_maps[0].duplex == "duplex-long-edge"
    assert compare_with_local(result, 2, ["MAPDUP"], index=index, analysis=analysis) == []
    result.medium_maps[0].duplex = "simplex"
    differences = compare_with_local(result, 2, ["MAPDUP"], index=index, analysis=analysis)
    assert differences[0]["kind"] == "duplex"
    assert differences[0]["offset"] == analysis.medium_maps["MAPDUP"].offset


@requires_jar
def test_the_adapter_reports_the_offsets_of_the_medium_maps(tmp_path):
    from afpstudio.testing import synthetic

    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.transition_document(4))

    adapter = AlpheusAdapter(jar=JAR, java_executable=JAVA or "java")
    result = adapter.parse(path)

    assert result.medium_maps
    for medium_map in result.medium_maps:
        assert medium_map.offset is not None
        assert medium_map.offset >= 0
        assert medium_map.name

    assert result.invokes
    for invoke in result.invokes:
        assert invoke.offset is not None


@requires_jar
def test_the_adapter_never_modifies_the_document(tmp_path):
    from afpstudio.testing import synthetic

    data = synthetic.simplex_document(2)
    path = tmp_path / "job.afp"
    path.write_bytes(data)

    adapter = AlpheusAdapter(jar=JAR, java_executable=JAVA or "java")
    adapter.parse(path)
    assert path.read_bytes() == data


@requires_jar
def test_the_adapter_survives_a_broken_document(tmp_path):
    path = tmp_path / "broken.afp"
    path.write_bytes(b"this is not an afp file")

    adapter = AlpheusAdapter(jar=JAR, java_executable=JAVA or "java")
    # Either the parser reports the problem or it returns a result that
    # says the parse was incomplete; it must not crash the server.
    try:
        result = adapter.parse(path)
    except Exception as exc:  # noqa: BLE001 - the point of the test
        assert getattr(exc, "code", "") in (
            "adapter_failed",
            "invalid_result",
            "timeout",
        )
    else:
        assert result.complete is False or result.errors


def test_the_adapter_is_reported_as_unavailable_when_it_is_not_built(tmp_path):
    adapter = AlpheusAdapter(jar=tmp_path / "missing.jar")
    described = adapter.describe()
    assert described["available"] is False
    assert "does not exist" in described["reason"]
