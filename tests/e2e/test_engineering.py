import time

from afpstudio.security.csrf import HEADER_NAME
from afpstudio.testing import synthetic


def open_session(client, path):
    token = client.get("/api/csrf").get_json()["token"]
    headers = {HEADER_NAME: token}
    response = client.post("/api/documents/open", json={"path": str(path)}, headers=headers)
    return wait_job(client, response)["result"], headers


def wait_job(client, response):
    assert response.status_code == 200, response.get_json()
    job_id = response.get_json()["job"]["id"]
    for _ in range(500):
        job = client.get("/api/jobs/" + job_id).get_json()
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(.01)
    raise AssertionError("Job did not terminate")


def test_engineering_copy_and_preview_export(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.demo_job())
    document, headers = open_session(app_client, path)
    endpoint = f"/api/documents/{document['document_id']}/engineering"
    copy = tmp_path / "copy.afp"
    result = wait_job(app_client, app_client.post(endpoint, json={"operation": "copy", "path": str(copy)}, headers=headers))
    assert result["state"] == "completed", result
    assert result["result"]["byte_identical"] is True
    png = tmp_path / "page.png"
    result = wait_job(app_client, app_client.post(endpoint, json={"operation": "png", "path": str(png), "pages": [0]}, headers=headers))
    assert result["state"] == "completed", result
    assert png.read_bytes().startswith(b"\x89PNG")
    assert app_client.post(endpoint, json={"operation": "copy", "path": str(path)}, headers=headers).status_code == 400
    assert app_client.post(endpoint, json={"operation": "copy", "path": str(copy)}, headers=headers).status_code == 409


def test_engineering_requires_csrf_and_protects_source_after_suffix(app_client, tmp_path):
    path = tmp_path / "job.png"
    path.write_bytes(synthetic.demo_job())
    document, headers = open_session(app_client, path)
    endpoint = f"/api/documents/{document['document_id']}/engineering"
    assert app_client.post(endpoint, json={}).status_code == 403
    assert app_client.post(endpoint, json={"operation": "png", "path": str(path.with_suffix('.out'))}, headers=headers).status_code == 400


def test_outputs_cannot_replace_another_open_document(app_client, tmp_path):
    first, other = tmp_path / "first.afp", tmp_path / "other.afp"
    first.write_bytes(synthetic.demo_job())
    other.write_bytes(synthetic.demo_job())
    original = other.read_bytes()
    doc, headers = open_session(app_client, first)
    open_session(app_client, other)
    endpoint = f"/api/documents/{doc['document_id']}/engineering"
    result = app_client.post(endpoint, json={"operation": "copy", "path": str(other), "overwrite": True}, headers=headers)
    assert result.status_code == 400
    assert other.read_bytes() == original
