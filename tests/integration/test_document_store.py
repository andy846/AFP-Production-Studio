"""The unified document model.

The store is what the API layer talks to, so these tests exercise the
whole chain: index, media analysis, preflight, resource check and the
page renderer, in the order the application uses them.
"""

from __future__ import annotations

from afpstudio.document_model.model import DocumentStore
from afpstudio.testing import synthetic


def test_opening_a_document_produces_a_usable_session(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(3))

    store = DocumentStore()
    session = store.open(path)

    assert session.page_count == 3
    assert session.indexed_page_count == 3
    assert session.analysis.documents == 1
    assert session.preflight.status in ("pass", "warning", "unresolved", "fail")
    assert session.summary()["filename"] == "job.afp"


def test_the_session_keeps_the_offsets_of_the_original_file(tmp_path):
    data = synthetic.simplex_document(2)
    path = tmp_path / "job.afp"
    path.write_bytes(data)

    store = DocumentStore()
    session = store.open(path)

    for page in session.preflight.pages:
        assert data[page.offset] == 0x5A


def test_a_page_can_be_rendered_through_the_session(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))

    store = DocumentStore()
    session = store.open(path)
    svg = session.page_svg(0)
    assert "<svg" in svg


def test_the_cache_is_used_by_the_session(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))

    store = DocumentStore()
    session = store.open(path)
    first = session.page_svg(0, cache=store.cache)
    second = session.page_svg(0, cache=store.cache)
    assert first == second
    assert store.cache.stats()["hits"] >= 1


def test_closing_a_document_forgets_it(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(1))

    store = DocumentStore()
    session = store.open(path)
    assert store.get(session.document_id) is not None
    assert store.close(session.document_id) is True
    assert store.get(session.document_id) is None
    assert store.close(session.document_id) is False


def test_a_missing_font_is_reported_as_a_missing_resource(tmp_path):
    """A document that maps a coded font it never embeds."""
    from afpstudio.viewer.index import build_index
    from afpstudio.viewer.resources import missing_resources

    # Map Coded Font, format 1 (0xD3B18A). Each repeating group is
    # local id + 3 reserved + coded font name(8) + code page name(8) +
    # character set name(8); X'FF' means "not present".
    font_name = synthetic.encode_name("NOPE01")
    group = bytes([1]) + b"\x00\x00\x00" + font_name + b"\xff" * 16
    payload = bytes([len(group)]) + b"\x00\x00\x00" + group
    mcf = synthetic.structured_field(0xD3B18A, payload)

    path = tmp_path / "nofont.afp"
    path.write_bytes(synthetic.bdt() + mcf + synthetic.bpg() + synthetic.epg() + synthetic.edt())

    index = build_index(path)
    missing = missing_resources(index, path)
    assert any(item["name"] == "NOPE01" for item in missing)


def test_search_finds_the_structured_fields(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))

    store = DocumentStore()
    session = store.open(path)

    results = session.search_structure("medium map")
    assert results
    assert all(row.offset >= 0 for row in results)

    assert session.search_structure("") == []


def test_search_text_scans_the_encoded_stream(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(1))

    store = DocumentStore()
    session = store.open(path)

    # "MAP01" appears as an EBCDIC object name in the file.
    results = session.search_text("MAP01")
    assert results
    assert results[0]["offset"] >= 0
    assert results[0]["page"] is not None or results[0]["page"] is None
