import time
import threading

from afpstudio.testing import synthetic as sf
from afpstudio.security.csrf import HEADER_NAME


def wait_job(client, response):
    assert response.status_code == 200, response.get_json()
    job_id = response.get_json()["job"]["id"]
    for _ in range(1000):
        job = client.get("/api/jobs/" + job_id).get_json()
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(.01)
    raise AssertionError("Job did not terminate")


def open_session(client, path):
    headers = {HEADER_NAME: client.get("/api/csrf").get_json()["token"]}
    job = wait_job(client, client.post("/api/documents/open", json={"path": str(path)}, headers=headers))
    return job["result"], headers


def clean_document(pages):
    return sf.bdt() + b"".join(sf.bpg() + sf.pgd(25.4, 25.4) + sf.epg() for _ in range(pages)) + sf.edt()


def test_pdf_api_exports_all_and_normalizes_ranges(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(clean_document(24))
    doc, headers = open_session(app_client, path)
    endpoint = f"/api/documents/{doc['document_id']}/pdf-export"
    assert app_client.post(endpoint, json={}).status_code == 403
    output = tmp_path / "all.pdf"
    job = wait_job(app_client, app_client.post(endpoint, json={"path": str(output)}, headers=headers))
    assert job["state"] == "completed", job
    assert job["result"]["pages"] == list(range(1, 25))
    job = wait_job(app_client, app_client.post(endpoint, json={"path": str(tmp_path / 'range.pdf'),
        "selection": "range", "page_range": "8,1-3,2"}, headers=headers))
    assert job["result"]["pages"] == [1, 2, 3, 8]
    for raw in ("0", "9-2", "1,,2", "25", "x", ""):
        assert app_client.post(endpoint, json={"path": str(tmp_path / 'bad.pdf'),
            "selection": "range", "page_range": raw}, headers=headers).status_code == 400


def test_pdf_cannot_replace_source_after_suffix_or_existing_output(app_client, tmp_path):
    source = tmp_path / "source.pdf"
    original = sf.simplex_document(1)
    source.write_bytes(original)
    doc, headers = open_session(app_client, source)
    endpoint = f"/api/documents/{doc['document_id']}/pdf-export"
    assert app_client.post(endpoint, json={"path": str(source.with_suffix('.out')),
        "overwrite": True}, headers=headers).status_code == 400
    assert source.read_bytes() == original
    existing = tmp_path / "exists.pdf"
    existing.write_bytes(b"keep")
    assert app_client.post(endpoint, json={"path": str(existing)}, headers=headers).status_code == 409
    assert existing.read_bytes() == b"keep"


def test_pdf_omissions_require_explicit_approval(app_client, tmp_path):
    source = tmp_path / "missing.afp"
    source.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(210, 297)
        + sf.structured_field(0xD3AF5F, sf.encode_name("MISSING") + bytes(6)) + sf.epg() + sf.edt())
    doc, headers = open_session(app_client, source)
    endpoint = f"/api/documents/{doc['document_id']}/pdf-export"
    output = tmp_path / "missing.pdf"
    body = {"path": str(output)}
    job = wait_job(app_client, app_client.post(endpoint, json=body, headers=headers))
    assert job["state"] == "failed"
    assert job["error_code"] == "incomplete_preview"
    assert job["error_details"]["page"] == 1
    assert not output.exists()
    body["allow_incomplete"] = True
    job = wait_job(app_client, app_client.post(endpoint, json=body, headers=headers))
    assert job["state"] == "completed", job
    assert job["result"]["status"] == "completed_with_warnings"


def test_pdf_actions_only_accept_completed_unchanged_output(app_client, tmp_path, monkeypatch):
    import afpstudio.api.pdf as pdf_api
    called = []
    monkeypatch.setattr(pdf_api, "_launch_output", lambda path, action: called.append((path, action)))
    source = tmp_path / "job.afp"
    source.write_bytes(clean_document(1))
    doc, headers = open_session(app_client, source)
    output = tmp_path / "job.pdf"
    job = wait_job(app_client, app_client.post(f"/api/documents/{doc['document_id']}/pdf-export",
        json={"path": str(output)}, headers=headers))
    endpoint = f"/api/jobs/{job['id']}/output-action"
    assert app_client.post(endpoint, json={"action": "open"}).status_code == 403
    assert app_client.post(endpoint, json={"action": "open"}, headers=headers).status_code == 200
    assert called == [(output, "open")]
    output.write_bytes(b"changed")
    assert app_client.post(endpoint, json={"action": "open"}, headers=headers).status_code == 409


def test_closing_source_cancels_export_and_keeps_previous_pdf(app_client, tmp_path, monkeypatch):
    import afpstudio.engineering.pdf_export as engine
    original_export = engine.export_pdf
    started, release = threading.Event(), threading.Event()
    def paused(*args, **kwargs):
        progress = kwargs["progress"]
        def pause(fraction):
            started.set()
            assert release.wait(4)
            progress(fraction)
        kwargs["progress"] = pause
        return original_export(*args, **kwargs)
    monkeypatch.setattr(engine, "export_pdf", paused)
    source = tmp_path / "closing.afp"
    source.write_bytes(clean_document(2))
    doc, headers = open_session(app_client, source)
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"keep previous output")
    response = app_client.post(f"/api/documents/{doc['document_id']}/pdf-export",
        json={"path": str(output), "overwrite": True}, headers=headers)
    assert response.status_code == 200
    assert started.wait(3)
    try:
        assert app_client.delete(f"/api/documents/{doc['document_id']}", headers=headers).status_code == 200
    finally:
        release.set()
    for _ in range(200):
        if not list(tmp_path.glob(".afp-pdf-*.part")):
            break
        time.sleep(.01)
    assert not list(tmp_path.glob(".afp-pdf-*.part"))
    assert output.read_bytes() == b"keep previous output"
