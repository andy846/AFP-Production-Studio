"""The end to end API flow."""

from __future__ import annotations

import time
import pytest

from afpstudio.security.csrf import HEADER_NAME
from afpstudio.testing import synthetic


def open_document(client, path, **extra):
    """Open a document and wait for the job to finish."""
    token = client.get("/api/csrf").get_json()["token"]
    payload = {"path": str(path)}
    payload.update(extra)
    response = client.post(
        "/api/documents/open",
        json=payload,
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 200, response.get_json()
    job_id = response.get_json()["job"]["id"]
    for _ in range(400):
        job = client.get("/api/jobs/" + job_id).get_json()
        if job["state"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.01)
    assert job["state"] == "completed", job
    return job["result"]


def test_the_health_endpoint_reveals_nothing(app_client):
    response = app_client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert "filename" not in payload


def test_the_index_page_loads(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert b"AFP Production Studio" in response.data


def test_a_valid_afp_opens_and_renders(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(3))
    summary = open_document(app_client, path)
    assert summary["page_count"] == 3
    document_id = summary["document_id"]
    response = app_client.get(f"/api/documents/{document_id}/page/0.svg")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert b"<svg" in response.data


def test_the_preflight_reports_every_page(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(3))
    summary = open_document(app_client, path)
    payload = app_client.get(
        f"/api/documents/{summary['document_id']}/preflight"
    ).get_json()
    assert len(payload["pages"]) == 3
    assert payload["pages"][0]["medium_map"] == "MAP01"
    assert payload["pages"][0]["offset"] is not None


def test_selecting_a_field_locates_its_page(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.transition_document(4))
    summary = open_document(app_client, path)
    payload = app_client.get(
        f"/api/documents/{summary['document_id']}/fields?page=2"
    ).get_json()
    assert payload["fields"]
    for row in payload["fields"]:
        assert row["page"] == 2
        assert row["offset"] >= 0


def test_the_report_is_consistent(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(4))
    summary = open_document(app_client, path)
    payload = app_client.get(
        f"/api/documents/{summary['document_id']}/report"
    ).get_json()
    stats = payload["statistics"]
    assert stats["total_pages"]["value"] == 4
    assert stats["total_pages"]["basis"] == "parsed"
    assert stats["mailpieces"]["value"] is None
    control = payload["control_report"]
    assert control["stages"][0]["key"] == "afp_input"


def test_an_export_is_written(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))
    summary = open_document(app_client, path)
    token = app_client.get("/api/csrf").get_json()["token"]
    target = tmp_path / "report.csv"
    response = app_client.post(
        f"/api/documents/{summary['document_id']}/export",
        json={"format": "csv", "path": str(target)},
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 200
    assert target.exists()
    assert "Total AFP pages" in target.read_text(encoding="utf-8-sig")


def test_an_export_may_not_overwrite_the_source(app_client, tmp_path):
    path = tmp_path / "job.afp"
    original = synthetic.simplex_document(2)
    path.write_bytes(original)
    summary = open_document(app_client, path)
    token = app_client.get("/api/csrf").get_json()["token"]
    response = app_client.post(
        f"/api/documents/{summary['document_id']}/export",
        json={"format": "csv", "path": str(path)},
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 400
    assert path.read_bytes() == original


def test_changed_source_invalidates_session_and_cached_page(app_client, tmp_path):
    path = tmp_path / "changing.afp"
    path.write_bytes(synthetic.simplex_document(2))
    summary = open_document(app_client, path)
    doc_id = summary["document_id"]
    assert app_client.get(f"/api/documents/{doc_id}/page/0.svg").status_code == 200
    with path.open("ab") as handle:
        handle.write(b"changed")
    response = app_client.get(f"/api/documents/{doc_id}/preflight")
    assert response.status_code == 409
    assert response.get_json()["code"] == "source_changed"
    assert app_client.get(f"/api/documents/{doc_id}/page/0.svg").status_code == 404


def test_changed_source_during_analysis_does_not_publish(tmp_path, monkeypatch):
    import afpstudio.document_model.model as model

    path = tmp_path / "changing.afp"
    path.write_bytes(synthetic.simplex_document(2))
    original = model.analyse_media

    def change_after_analysis(index, source):
        result = original(index, source)
        with source.open("ab") as handle:
            handle.write(b"changed")
        return result

    monkeypatch.setattr(model, "analyse_media", change_after_analysis)
    store = model.DocumentStore()
    with pytest.raises(model.SourceChangedError):
        store.open(path)
    assert store.list() == []


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_export_suffix_cannot_overwrite_source(app_client, tmp_path, fmt):
    path = tmp_path / ("job." + fmt)
    original = synthetic.simplex_document(2)
    path.write_bytes(original)
    summary = open_document(app_client, path)
    token = app_client.get("/api/csrf").get_json()["token"]
    response = app_client.post(
        f"/api/documents/{summary['document_id']}/export",
        json={"format": fmt, "path": str(tmp_path / "job.other")},
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 400
    assert path.read_bytes() == original


def test_export_requires_explicit_confirmation_to_replace_report(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))
    target = tmp_path / "existing.csv"
    target.write_text("original", encoding="utf-8")
    summary = open_document(app_client, path)
    token = app_client.get("/api/csrf").get_json()["token"]
    endpoint = f"/api/documents/{summary['document_id']}/export"
    payload = {"format": "csv", "path": str(target)}
    response = app_client.post(endpoint, json=payload, headers={HEADER_NAME: token})
    assert response.status_code == 409
    assert target.read_text(encoding="utf-8") == "original"
    response = app_client.post(endpoint, json={**payload, "overwrite": True}, headers={HEADER_NAME: token})
    assert response.status_code == 200
    assert "Total AFP pages" in target.read_text(encoding="utf-8-sig")


def test_an_invalid_afp_does_not_crash_the_application(app_client, tmp_path):
    path = tmp_path / "broken.afp"
    path.write_bytes(b"\x00\x01\x02 not an afp file at all")
    summary = open_document(app_client, path)
    assert summary["page_count"] == 0
    assert summary["preflight_status"] == "fail"
    response = app_client.get(
        f"/api/documents/{summary['document_id']}/preflight"
    )
    assert response.status_code == 200
    assert app_client.get("/api/health").status_code == 200


def test_a_truncated_afp_does_not_produce_a_pass(app_client, tmp_path):
    path = tmp_path / "truncated.afp"
    path.write_bytes(synthetic.truncated_document())
    summary = open_document(app_client, path)
    assert summary["preflight_status"] == "fail"
    assert summary["parse_complete"] is False


def test_the_source_file_is_never_modified(app_client, tmp_path):
    path = tmp_path / "job.afp"
    original = synthetic.simplex_document(3)
    path.write_bytes(original)
    summary = open_document(app_client, path)
    app_client.get(f"/api/documents/{summary['document_id']}/page/0.svg")
    app_client.get(f"/api/documents/{summary['document_id']}/preflight")
    app_client.get(f"/api/documents/{summary['document_id']}/report")
    assert path.read_bytes() == original


def test_a_document_can_be_closed(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(1))
    summary = open_document(app_client, path)
    token = app_client.get("/api/csrf").get_json()["token"]
    response = app_client.delete(
        f"/api/documents/{summary['document_id']}",
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 200
    missing = app_client.get(
        f"/api/documents/{summary['document_id']}/preflight"
    )
    assert missing.status_code == 404


def test_an_unknown_document_is_a_clean_404(app_client):
    response = app_client.get("/api/documents/nope/preflight")
    assert response.status_code == 404
    assert response.get_json()["code"] == "document_not_found"


def test_search_finds_a_field(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))
    summary = open_document(app_client, path)
    payload = app_client.get(
        f"/api/documents/{summary['document_id']}/search?q=medium+map"
    ).get_json()
    assert payload["structure"]


def test_the_profiles_endpoint_lists_the_sample(app_client):
    payload = app_client.get("/api/profiles").get_json()
    assert payload["profiles"]
    assert "sample_profile" in [p["name"] for p in payload["profiles"]]


def test_the_parser_endpoint_says_when_the_java_parser_is_missing(app_client):
    payload = app_client.get("/api/parser").get_json()
    assert "available" in payload
