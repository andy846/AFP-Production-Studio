import time

from afpstudio.security.csrf import HEADER_NAME
from afpstudio.testing.synthetic import demo_job


def test_recent_history_is_bound_to_open_session_and_can_be_disabled(app_client, tmp_path):
    path = tmp_path / "example.afp"
    path.write_bytes(demo_job())
    headers = {HEADER_NAME: app_client.get("/api/csrf").get_json()["token"]}
    assert app_client.post("/api/workspace/recent", json={"path": str(path)}, headers=headers).status_code == 400
    job_id = app_client.post("/api/documents/open", json={"path": str(path)}, headers=headers).get_json()["job"]["id"]
    for _ in range(500):
        job = app_client.get("/api/jobs/" + job_id).get_json()
        if job["state"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(.01)
    assert job["state"] == "completed"
    doc = job["result"]
    assert app_client.post("/api/workspace/recent", json={
        "document_id": doc["document_id"], "page": 3, "zoom": 1.25,
    }, headers=headers).status_code == 200
    result = app_client.get("/api/workspace").get_json()
    assert len(result["recent"]) == 1
    assert result["recent"][0]["page"] == 3
    assert result["recent"][0]["zoom"] == 1.25
    recent_id = result["recent"][0]["id"]
    reopened = app_client.post("/api/workspace/recent/open", json={"id": recent_id}, headers=headers)
    assert reopened.status_code == 200
    reopened_id = reopened.get_json()["job"]["id"]
    for _ in range(500):
        reopened_job = app_client.get("/api/jobs/" + reopened_id).get_json()
        if reopened_job["state"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(.01)
    assert reopened_job["state"] == "completed"
    assert reopened_job["result"]["path"] == str(path)
    assert app_client.post("/api/workspace/recent/open", json={"id": "../bad"}, headers=headers).status_code == 404
    assert app_client.post("/api/workspace", json={"remember": False}, headers=headers).status_code == 200
    assert app_client.get("/api/workspace").get_json()["recent"] == []
    assert app_client.post("/api/workspace/recent", json={"document_id": doc["document_id"]}, headers=headers).status_code == 200
    assert app_client.get("/api/workspace").get_json()["recent"] == []


def test_layout_validation_and_csrf(app_client):
    assert app_client.post("/api/workspace", json={"tree_width": 250}).status_code == 403
    headers = {HEADER_NAME: app_client.get("/api/csrf").get_json()["token"]}
    assert app_client.post("/api/workspace", json={"tree_width": 9999}, headers=headers).status_code == 400
    assert app_client.post("/api/workspace", json={"tree_width": 256, "details_width": 320}, headers=headers).status_code == 200
    values = app_client.get("/api/workspace").get_json()
    assert values["tree_width"] == 256 and values["details_width"] == 320


def test_theme_defaults_to_light_and_accepts_only_supported_values(app_client):
    headers = {HEADER_NAME: app_client.get("/api/csrf").get_json()["token"]}
    assert app_client.get("/api/workspace").get_json()["theme"] == "light"
    assert app_client.post("/api/workspace", json={"theme": "dark"}).status_code == 403
    assert app_client.post("/api/workspace", json={"theme": "dark"}, headers=headers).status_code == 200
    assert b'data-theme="dark"' in app_client.get("/").data
    assert app_client.post("/api/workspace", json={"theme": ["dark"]}, headers=headers).status_code == 400
    assert app_client.get("/api/workspace").get_json()["theme"] == "dark"


def test_settings_persist_and_invalid_file_recovers(tmp_path):
    from afpstudio.workspace_preferences import WorkspacePreferences
    path = tmp_path / "workspace.json"
    prefs = WorkspacePreferences(path)
    prefs.update({"tree_width": 320, "remember": False})
    restored = WorkspacePreferences(path).snapshot()
    assert restored["tree_width"] == 320 and restored["remember"] is False
    path.write_text("{", encoding="utf-8")
    assert WorkspacePreferences(path).snapshot()["tree_width"] == 240


def test_second_instance_cannot_restore_disabled_history(tmp_path):
    from afpstudio.document_model.model import DocumentStore
    from afpstudio.workspace_preferences import WorkspacePreferences
    source = tmp_path / "example.afp"
    source.write_bytes(demo_job())
    doc = DocumentStore().open(source)
    path = tmp_path / "workspace.json"
    first = WorkspacePreferences(path)
    entry = first.record(doc, 1, 1)["recent"][0]
    second = WorkspacePreferences(path)
    first.update({"remember": False})
    second.update({"tree_width": 320})
    second.record(doc, 2, 1)
    assert first.snapshot()["tree_width"] == 320
    assert first.snapshot()["remember"] is False
    assert second.find(entry["id"]) is None
