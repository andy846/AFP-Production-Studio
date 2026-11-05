import pytest

from afpstudio.document_model.model import DocumentStore
from afpstudio.engineering.documents import combine_documents, DocumentOperationError
from afpstudio.testing import synthetic


def job(name):
    return synthetic.DocumentSpec(medium_maps=[synthetic.MediumMapSpec(name=name)], page_invokes=[name] * 2).build()


def test_merge_and_extract_only_complete_self_contained_documents(tmp_path):
    a, b = tmp_path / "a.afp", tmp_path / "b.afp"
    a.write_bytes(job("MAP01"))
    b.write_bytes(job("MAP02"))
    store = DocumentStore()
    merged = tmp_path / "merged.afp"
    report = combine_documents([store.open(a), store.open(b)], merged)
    assert merged.read_bytes() == a.read_bytes() + b.read_bytes()
    assert report["documents"] == 2
    document = store.open(merged)
    assert [p.document_id for p in document.preflight.pages] == ["D1", "D1", "D2", "D2"]
    extracted = tmp_path / "second.afp"
    combine_documents([document], extracted, document_numbers=[2])
    assert extracted.read_bytes() == b.read_bytes()


def test_colliding_names_and_unverified_resource_closure_are_refused(tmp_path):
    a, b = tmp_path / "a.afp", tmp_path / "b.afp"
    a.write_bytes(job("MAP01"))
    b.write_bytes(job("MAP01"))
    store = DocumentStore()
    target = tmp_path / "bad.afp"
    with pytest.raises(DocumentOperationError):
        combine_documents([store.open(a), store.open(b)], target)
    assert not target.exists()
    a.write_bytes(synthetic.bdt() + synthetic.bpg() + synthetic.structured_field(0xD3AFD8, synthetic.encode_name("OVERLAY") + b"\0" * 6) + synthetic.epg() + synthetic.edt())
    with pytest.raises(DocumentOperationError):
        combine_documents([store.open(a)], target)
    assert not target.exists()


def test_merge_does_not_borrow_other_document_media_definitions(tmp_path):
    a, b = tmp_path / "dependent.afp", tmp_path / "definition.afp"
    a.write_bytes(synthetic.DocumentSpec(with_form_map=False, page_invokes=["MAP01"]).build())
    b.write_bytes(job("MAP01"))
    store = DocumentStore()
    target = tmp_path / "bad-merge.afp"
    with pytest.raises(DocumentOperationError):
        combine_documents([store.open(a), store.open(b)], target)
    assert not target.exists()
