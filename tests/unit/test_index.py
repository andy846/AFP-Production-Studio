"""The streaming structured field index."""

from __future__ import annotations

from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index


def test_index_records_every_field(tmp_path):
    path = tmp_path / "simplex.afp"
    path.write_bytes(synthetic.simplex_document(3))
    index = build_index(path)
    assert index.field_count > 0
    assert index.page_count == 3
    assert index.documents == 1


def test_index_keeps_the_offsets(tmp_path):
    path = tmp_path / "simplex.afp"
    data = synthetic.simplex_document(2)
    path.write_bytes(data)
    index = build_index(path)
    offset = index.offsets[0]
    assert data[offset] == 0x5A
    length = int.from_bytes(data[offset + 1 : offset + 3], "big")
    assert index.lengths[0] == length + 1


def test_page_starts_point_at_begin_page(tmp_path):
    path = tmp_path / "simplex.afp"
    path.write_bytes(synthetic.simplex_document(2))
    index = build_index(path)
    for page, start in enumerate(index.page_starts):
        assert index.sf_ids[
            index.page_field_starts[page]
        ] == 0xD3A8AF
        assert index.offsets[index.page_field_starts[page]] == start


def test_offset_maps_back_to_a_page(tmp_path):
    path = tmp_path / "simplex.afp"
    path.write_bytes(synthetic.simplex_document(3))
    index = build_index(path)
    for page in range(index.page_count):
        offset = index.page_starts[page]
        assert index.page_for_offset(offset) == page


def test_truncated_file_is_reported(tmp_path):
    path = tmp_path / "truncated.afp"
    path.write_bytes(synthetic.truncated_document())
    index = build_index(path)
    assert index.truncated is True
    assert index.warnings
    assert index.warnings[0]["code"] in ("SF_TRUNCATED", "CARRIAGE_CONTROL_INVALID")


def test_garbage_after_the_last_field_is_reported(tmp_path):
    path = tmp_path / "garbage.afp"
    data = synthetic.simplex_document(1) + b"\x99\x99\x99"
    path.write_bytes(data)
    index = build_index(path)
    assert index.truncated is True
    assert index.warnings[0]["code"] == "CARRIAGE_CONTROL_INVALID"


def test_an_impossible_length_stops_the_scan(tmp_path):
    path = tmp_path / "badlength.afp"
    path.write_bytes(synthetic.invalid_length_document())
    index = build_index(path)
    assert index.truncated is True


def test_names_are_decoded(tmp_path):
    path = tmp_path / "simplex.afp"
    path.write_bytes(synthetic.simplex_document(1))
    index = build_index(path)
    names = {
        index.token_at(i)
        for i in range(index.field_count)
        if index.token_at(i)
    }
    assert "MAP01" in names
    assert "FORM01" in names


def test_index_of_a_large_document_is_cheap(tmp_path):
    path = tmp_path / "many.afp"
    path.write_bytes(synthetic.simplex_document(2000))
    index = build_index(path)
    assert index.page_count == 2000
    # The index keeps compact arrays, not the file.
    assert index.file_size > 100_000
    assert index.field_count < 100_000


def test_the_demo_job_carries_text_and_a_medium_map_change(tmp_path):
    import re

    from afpstudio.viewer.renderer import PageRenderer

    path = tmp_path / "demo.afp"
    path.write_bytes(synthetic.demo_job(4))

    index = build_index(path)
    assert index.page_count == 4
    assert index.page_groups == ["LETTER"]

    svg = PageRenderer(path, index).render(0)
    texts = [
        chunk
        for chunk in re.findall(r">([^<]{2,})<", svg)
        if chunk.strip()
    ]
    assert "AFP Production Studio" in texts
    assert "Media preflight demonstration job" in texts


def test_presentation_text_survives_several_lines(tmp_path):
    import re

    from afpstudio.viewer.renderer import PageRenderer

    path = tmp_path / "lines.afp"
    path.write_bytes(synthetic.demo_job(4))

    svg = PageRenderer(path, build_index(path)).render(1)
    texts = [
        chunk
        for chunk in re.findall(r">([^<]{2,})<", svg)
        if chunk.strip()
    ]
    assert "Page 2 of 4" in texts
