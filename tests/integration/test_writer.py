import hashlib

import pytest

from afpstudio.document_model.model import DocumentStore
from afpstudio.engineering.writer import write_document, EditError
from afpstudio.testing import synthetic


def test_noop_roundtrip_preserves_unknown_fields_and_original_bytes(tmp_path):
    raw = synthetic.bdt() + synthetic.structured_field(0xD3EEEE, b"\x00\xffUNKNOWN") + synthetic.bpg() + synthetic.epg() + synthetic.edt()
    source = tmp_path / "original.afp"
    source.write_bytes(raw)
    target = tmp_path / "copy.afp"
    report = write_document(DocumentStore().open(source), target, [])
    assert target.read_bytes() == source.read_bytes() == raw
    assert report["byte_identical"] is True
    assert report["output_sha256"] == hashlib.sha256(raw).hexdigest()


def test_imm_edit_is_reparsed_and_changes_only_named_bytes(tmp_path):
    source = tmp_path / "job.afp"
    raw = synthetic.transition_document(4)
    source.write_bytes(raw)
    session = DocumentStore().open(source)
    invoke = session.analysis.invokes[0]
    target = tmp_path / "edited.afp"
    report = write_document(session, target, [{"offset": invoke.offset, "medium_map": "MAP02"}])
    edited = DocumentStore().open(target)
    assert edited.preflight.pages[0].medium_map == "MAP02"
    assert source.read_bytes() == raw
    assert target.read_bytes()[:invoke.offset + 9] == raw[:invoke.offset + 9]
    assert target.read_bytes()[invoke.offset + 17:] == raw[invoke.offset + 17:]
    assert report["changes"][0]["offset"] == invoke.offset


@pytest.mark.parametrize("case", ["source", "existing", "unknown_map", "wrong_offset"])
def test_unsafe_or_ambiguous_edit_never_publishes(tmp_path, case):
    source = tmp_path / "job.afp"
    source.write_bytes(synthetic.simplex_document(2))
    session = DocumentStore().open(source)
    target = source if case == "source" else tmp_path / "output.afp"
    if case == "existing":
        target.write_bytes(b"KEEP")
    patches = [{"offset": session.analysis.invokes[0].offset, "medium_map": "UNKNOWN"}] if case == "unknown_map" else []
    if case == "wrong_offset":
        patches = [{"offset": 1, "medium_map": "MAP01"}]
    before = source.read_bytes()
    with pytest.raises(EditError):
        write_document(session, target, patches)
    assert source.read_bytes() == before
    if case == "existing":
        assert target.read_bytes() == b"KEEP"
    assert not list(tmp_path.glob("*.part"))
