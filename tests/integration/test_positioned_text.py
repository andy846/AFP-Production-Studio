from afpstudio.document_model.model import DocumentStore
from afpstudio.testing import synthetic


def test_positioned_runs_have_page_coordinates_and_original_ptx_offset(tmp_path):
    path = tmp_path / "text.afp"
    prefix = synthetic.bdt() + synthetic.bpg() + synthetic.pgd(210, 297)
    path.write_bytes(prefix + synthetic.ptx_text([("REFERENCE42", 480)]) + synthetic.epg() + synthetic.edt())
    document = DocumentStore().open(path)
    data = document.renderer.text_runs(0, codepage="cp500")
    run = next(row for row in data["items"] if "REFERENCE42" in row["text"])
    assert run["page"] == 1 and run["offset"] == len(prefix)
    assert run["offset_kind"] == "structured_field"
    assert type(run["x"]) is int and type(run["y"]) is int
    assert data["basis"] == "readAFP_preview"
    assert data["codepage_basis"] == "embedded_mapping_or_operator_fallback"


def test_text_runs_require_explicit_supported_fallback(tmp_path):
    import pytest
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.demo_job())
    renderer = DocumentStore().open(path).renderer
    with pytest.raises(ValueError):
        renderer.text_runs(0, codepage="automatic")
