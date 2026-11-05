"""Persisted operator evidence through the local API."""
from afpstudio.testing import synthetic
from test_engineering import open_session


def test_evidence_survives_reopen_and_exports_with_units(app_client, tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.demo_job())
    document, headers = open_session(app_client, path)
    endpoint = f"/api/documents/{document['document_id']}/production-journal"
    event = {"stage": "printing", "unit": "sheets", "value": 3, "source": "Synthetic RIP log",
             "reference": "=FORMULA", "recorded_by": "Test operator"}
    response = app_client.post(endpoint, json=event, headers=headers)
    assert response.status_code == 200
    result = response.get_json()
    assert len(result["job_sha256"]) == 64
    assert result["events"][0]["unit"] == "sheets"
    event["action"] = "signoff"
    assert app_client.post(endpoint, json=event, headers=headers).status_code == 409
    reopened, headers = open_session(app_client, path)
    report = app_client.get(f"/api/documents/{reopened['document_id']}/report").get_json()
    assert report["production_journal"]["events"][0]["value"] == 3
    target = tmp_path / "report.csv"
    response = app_client.post(f"/api/documents/{reopened['document_id']}/export", json={"format": "csv", "path": str(target)}, headers=headers)
    assert response.status_code == 200
    assert "'=FORMULA" in target.read_text(encoding="utf-8-sig")
    assert "sheets" in target.read_text(encoding="utf-8-sig")
