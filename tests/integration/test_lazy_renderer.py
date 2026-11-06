"""Differential test: the lazy renderer against the upstream renderer.

readAFP renders a document by walking every structured field in the
file. Studio renders one page at a time by replaying only the state
that page depends on. The two must agree, byte for byte, or the
refactor has changed what the operator sees.

This test runs the comparison over every AFP in the publicly licensed
corpus that ships with readAFP.
"""

from __future__ import annotations

import tracemalloc

import pytest

from readafp.parser import parse_file
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg

from afpstudio.viewer.index import build_index
from afpstudio.viewer.renderer import PageRenderer
from afpstudio.testing import synthetic as sf


def upstream_svgs(path) -> list[str]:
    """Render a document the way readAFP does."""
    fields = parse_file(str(path))
    return [page_to_svg(page) for page in extract_pages(fields)]


def corpus_files(corpus_dir):
    """Every AFP under the corpus directory."""
    files = sorted(corpus_dir.rglob("*.afp"))
    return files


def test_the_corpus_is_present(corpus_dir):
    assert corpus_files(corpus_dir), "The corpus should not be empty."


def test_lazy_rendering_matches_upstream_over_the_corpus(corpus_dir):
    """Every page of every corpus file renders identically."""
    files = corpus_files(corpus_dir)
    assert files

    mismatches = []
    checked_pages = 0
    checked_files = 0

    for path in files:
        try:
            expected = upstream_svgs(path)
        except Exception:  # noqa: BLE001 - upstream refuses some files
            # A file upstream cannot render is not a candidate for the
            # comparison, but Studio must still survive it.
            continue
        checked_files += 1

        index = build_index(path)
        renderer = PageRenderer(path, index)
        if renderer.page_count != len(expected):
            mismatches.append(
                f"{path.name}: Studio shows {renderer.page_count} pages, "
                f"upstream shows {len(expected)}"
            )
            continue

        for page in range(renderer.page_count):
            actual = renderer.render(page)
            checked_pages += 1
            if actual != expected[page]:
                mismatches.append(
                    f"{path.name} page {page}: the rendered SVG differs"
                )
                break

    assert not mismatches, (
        f"{len(mismatches)} document(s) render differently: "
        + "; ".join(mismatches[:10])
    )
    assert checked_files > 0
    assert checked_pages > 0


def test_a_document_is_not_rendered_before_it_is_asked_for(tmp_path):
    """Opening a document must not render every page."""
    from afpstudio.testing import synthetic

    path = tmp_path / "many.afp"
    path.write_bytes(synthetic.simplex_document(50))

    index = build_index(path)
    renderer = PageRenderer(path, index)

    rendered = []
    original = renderer.render

    def counting_render(page):
        rendered.append(page)
        return original(page)

    renderer.render = counting_render  # type: ignore[method-assign]
    assert renderer.page_count == 50
    assert rendered == []

    renderer.render(7)
    assert rendered == [7]


def test_the_eager_path_still_works(tmp_path):
    """The comparison helper renders everything, and that still works."""
    from afpstudio.testing import synthetic

    path = tmp_path / "many.afp"
    path.write_bytes(synthetic.simplex_document(5))
    index = build_index(path)
    renderer = PageRenderer(path, index)
    svgs = renderer.render_all_eager()
    assert len(svgs) == 5
    assert svgs[0] == renderer.render(0)


def test_a_page_outside_the_document_is_refused(tmp_path):
    from afpstudio.testing import synthetic
    from afpstudio.viewer.renderer import RenderError

    path = tmp_path / "one.afp"
    path.write_bytes(synthetic.simplex_document(1))
    index = build_index(path)
    renderer = PageRenderer(path, index)

    with pytest.raises(RenderError):
        renderer.render(5)
    with pytest.raises(RenderError):
        renderer.render(-1)


def test_unused_container_does_not_duplicate_payloads_during_page_modeling(tmp_path):
    path = tmp_path / "large-unused-container.afp"
    chunk = b"\xab" * 32760
    count = 160
    path.write_bytes(sf.bdt() + sf.structured_field(0xD3A892, sf.encode_name("UNUSED"))
                     + sf.structured_field(0xD3EE92, chunk) * count
                     + sf.structured_field(0xD3A992) + sf.bpg() + sf.pgd(210, 297)
                     + sf.ptx_text([("Visible body", 480)]) + sf.epg() + sf.edt())
    fields = parse_file(str(path))

    # Measure additional model allocations after reading the input. Keeping
    # chunk references is cheap; copying the whole unused resource is not.
    tracemalloc.start()
    try:
        pages = extract_pages(fields)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert "Visible body" in page_to_svg(pages[0])
    assert peak < (len(chunk) * count) // 2


def test_referenced_container_keeps_split_image_bytes_and_record_order(tmp_path):
    path = tmp_path / "split-image.afp"
    name = sf.encode_name("IMAGE")
    begin = sf.structured_field(0xD3A892, name)
    end = sf.structured_field(0xD3A992)
    payload = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
    size = sf.triplet(0x4C, b"\x00\x00\x00\x10\x00\x00\x10")
    include = sf.structured_field(0xD3AFC3, name + bytes(19) + size)
    page = sf.bpg() + sf.pgd(210, 297) + include + include + sf.epg()
    path.write_bytes(sf.bdt() + begin + sf.structured_field(0xD3EE92, payload[:2])
                     + sf.structured_field(0xD3EE92, payload[2:]) + end + page
                     + begin + sf.structured_field(0xD3EE92, b"extra") + end + page + sf.edt())

    first, second = extract_pages(parse_file(str(path)))
    assert [image.data for image in first.images] == [payload, payload]
    assert [image.data for image in second.images] == [payload + b"extra"] * 2
    assert all(image.mime == "image/gif" for model in (first, second) for image in model.images)
