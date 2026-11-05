from afpstudio.document_model.mailpieces import build_mailpiece_index
from afpstudio.document_model.model import DocumentStore
from afpstudio.testing import synthetic


def test_group_is_not_a_mailpiece_without_an_explicit_customer_rule(tmp_path):
    path = tmp_path / "group.afp"
    path.write_bytes(synthetic.demo_job())
    document = DocumentStore().open(path)
    result = build_mailpiece_index(document)
    assert result["count"] is None and not result["complete"]
    assert result["status"] == "unresolved"


def test_attested_group_rule_indexes_page_counts_and_media_without_claiming_dispatch(tmp_path):
    path = tmp_path / "groups.afp"
    path.write_bytes(synthetic.bdt() + synthetic.bng("LETTER") + synthetic.bpg() + synthetic.epg() + synthetic.eng("LETTER") +
        synthetic.bng("LETTER") + synthetic.bpg() + synthetic.epg() + synthetic.bpg() + synthetic.epg() + synthetic.eng("LETTER") + synthetic.edt())
    document = DocumentStore().open(path)
    rule = {"page_group_name": "LETTER", "evidence_ref": "Synthetic customer indexing rules", "verified_by": "UAT", "verified_at": "2026-11-06"}
    result = build_mailpiece_index(document, rule)
    assert result["count"] == 2 and result["complete"]
    assert [item["page_count"] for item in result["items"]] == [1, 2]
    assert result["basis"] == "calculated_from_operator_attested_boundary_rule"
    assert result["actual_mailpieces"] is None
