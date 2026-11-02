"""Large document behaviour.

These tests are about the *shape* of the work, not about a wall clock
number: indexing a large document must not read the whole file into
memory, and opening one must not render its pages. The measured numbers
live in docs/PERFORMANCE.md and come from scripts/measure_performance.py.
"""

from __future__ import annotations

import tracemalloc

from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index
from afpstudio.viewer.renderer import PageRenderer


def test_a_thousand_page_document_indexes_quickly(tmp_path):
    path = tmp_path / "big.afp"
    path.write_bytes(synthetic.simplex_document(1000))
    index = build_index(path)
    assert index.page_count == 1000


def test_the_index_stays_small_relative_to_the_file(tmp_path):
    """The index holds headers, not payloads."""
    path = tmp_path / "big.afp"
    # A large opaque payload makes the file big without adding any
    # structured-field state. The index must not retain that payload.
    chunk = b"\x00" * 60_000  # below the 65,535 byte SF length ceiling
    data = b"".join(
        synthetic.structured_field(0xD3EEEE, chunk) for _ in range(280)
    )
    data += synthetic.simplex_document(1000)
    path.write_bytes(data)
    size = path.stat().st_size

    tracemalloc.start()
    index = build_index(path)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # The index itself must not approach the size of the document.
    assert index.file_size == size
    # The index keeps only compact arrays of headers, not the payload.
    assert peak < size // 4


def test_opening_a_large_document_renders_nothing(tmp_path):
    path = tmp_path / "big.afp"
    path.write_bytes(synthetic.simplex_document(500))

    index = build_index(path)
    renderer = PageRenderer(path, index)

    rendered = []
    original = renderer.render
    renderer.render = lambda page: (rendered.append(page), original(page))[1]

    assert renderer.page_count == 500
    assert rendered == []


def test_one_page_of_a_large_document_can_be_rendered_alone(tmp_path):
    path = tmp_path / "big.afp"
    path.write_bytes(synthetic.simplex_document(500))

    index = build_index(path)
    renderer = PageRenderer(path, index)
    svg = renderer.render(250)
    assert "<svg" in svg


def test_the_media_walk_scales_with_the_page_count(tmp_path):
    from afpstudio.preflight.media import analyse_media

    path = tmp_path / "big.afp"
    path.write_bytes(synthetic.simplex_document(1000))
    index = build_index(path)
    analysis = analyse_media(index, path)
    assert len(analysis.medium_maps) == 1
    assert len(analysis.invokes) == 1000
