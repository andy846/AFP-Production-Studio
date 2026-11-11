"""Physical-offset and viewer acceptance for the supported RDW subset."""
import json
from xml.etree import ElementTree as ET

import pytest

from afpstudio.document_model.model import DocumentStore
from afpstudio.document_model.records import iter_records
from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index


def wrap_rdw(data):
    import io
    return b"".join((len(r.payload) + 4).to_bytes(2, "big") + b"\0\0" + r.payload
                    for r in iter_records(io.BytesIO(data), "standard"))


def test_rdw_keeps_physical_offsets_and_matches_rendering(tmp_path):
    original = synthetic.transition_document(4)
    standard = tmp_path / "standard.afp"
    rdw = tmp_path / "rdw.afp"
    standard.write_bytes(original)
    wrapped = wrap_rdw(original)
    rdw.write_bytes(wrapped)
    a = DocumentStore().open(standard)
    b = DocumentStore().open(rdw, input_format="rdw")
    assert b.index.page_count == a.index.page_count == 4
    assert list(b.index.sf_ids) == list(a.index.sf_ids)
    for i in range(a.index.field_count):
        assert b.index.offsets[i] == a.index.offsets[i] + (i + 1) * 4
    assert [p.medium_map for p in b.preflight.pages] == [p.medium_map for p in a.preflight.pages]
    standard_svg = ET.fromstring(a.page_svg(0))
    rdw_svg = ET.fromstring(b.page_svg(0))
    standard_issues = json.loads(standard_svg.attrib.pop("data-render-issues", "[]"))
    rdw_issues = json.loads(rdw_svg.attrib.pop("data-render-issues", "[]"))
    # Drawing remains identical; diagnostics deliberately retain each input's
    # original physical offsets, including the RDW headers.
    assert ET.tostring(rdw_svg) == ET.tostring(standard_svg)
    assert len(rdw_issues) == len(standard_issues)
    offsets = dict(zip(a.index.offsets, b.index.offsets))
    for standard_issue, rdw_issue in zip(standard_issues, rdw_issues):
        assert rdw_issue == {**standard_issue, "offset": offsets[standard_issue["offset"]]}
    assert rdw.read_bytes() == wrapped


@pytest.mark.parametrize("data", [b"\x00\x04\0\0", b"\x00\x10\0\0\x5a",
                                      b"\x00\x0d\x01\0" + synthetic.bdt(),
                                      b"\x00\x0e\0\0" + synthetic.bdt() + b"x"])
def test_invalid_rdw_is_incomplete_and_never_passes(tmp_path, data):
    path = tmp_path / "bad.afp"
    path.write_bytes(data)
    index = build_index(path, input_format="rdw")
    assert index.truncated and index.warnings
    session = DocumentStore().open(path, input_format="rdw")
    assert session.preflight.status != "pass"
    assert not session.parse_complete


def test_rdw_api_open_render_and_java_rejection(app_client, tmp_path):
    from afpstudio.security.csrf import HEADER_NAME
    import time
    path = tmp_path / "rdw.afp"
    path.write_bytes(wrap_rdw(synthetic.simplex_document(2)))
    token = app_client.get("/api/csrf").get_json()["token"]
    body = {"path": str(path), "input_format": "rdw"}
    response = app_client.post("/api/documents/open", json=body, headers={HEADER_NAME: token})
    assert response.status_code == 200
    job_id = response.get_json()["job"]["id"]
    for _ in range(300):
        job = app_client.get("/api/jobs/" + job_id).get_json()
        if job["state"] in ("completed", "failed"):
            break
        time.sleep(.01)
    assert job["state"] == "completed", job
    result = job["result"]
    assert result["input_format"] == "rdw" and result["page_count"] == 2
    assert app_client.get(f"/api/documents/{result['document_id']}/page/1.svg").status_code == 200
    body["use_java_parser"] = True
    response = app_client.post("/api/documents/open", json=body, headers={HEADER_NAME: token})
    assert response.status_code == 400
    assert response.get_json()["code"] == "unsupported_comparison"
