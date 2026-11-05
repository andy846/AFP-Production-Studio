from afpstudio.security.csrf import HEADER_NAME
from afpstudio.testing import synthetic
from test_engineering import wait_job


def test_batch_preflight_is_bounded_and_does_not_keep_sessions(app_client, tmp_path):
    paths = []
    for name, raw in (("one.afp", synthetic.simplex_document(2)), ("two.afp", synthetic.duplex_document(2)), ("broken.afp", b"invalid")):
        path = tmp_path / name
        path.write_bytes(raw)
        paths.append(str(path))
    token = app_client.get("/api/csrf").get_json()["token"]
    response = app_client.post("/api/batch/preflight", json={"paths": paths}, headers={HEADER_NAME: token})
    job = wait_job(app_client, response)
    assert job["state"] == "completed", job
    result = job["result"]
    assert result["status"] == "fail"
    assert result["indexed_pages"] == 4
    assert len(result["files"]) == 3
    assert result["files"][2]["status"] == "fail"
    assert app_client.get("/api/documents").get_json()["documents"] == []
    assert app_client.post("/api/batch/preflight", json={"paths": paths * 8}, headers={HEADER_NAME: token}).status_code == 400
