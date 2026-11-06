import os

import pytest
from openpyxl import load_workbook

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


def test_folder_production_excel_counts_complete_and_unknown_inputs(app_client, tmp_path):
    folder = tmp_path / "input"
    folder.mkdir()
    grouped = synthetic.simplex_document(2).replace(synthetic.bdt(), synthetic.bdt() + synthetic.bng("JOB"), 1)
    grouped = grouped.replace(synthetic.edt(), synthetic.eng("JOB") + synthetic.edt())
    files = {"=one.AFP": grouped, "two.afp": synthetic.duplex_document(3),
             "unknown.afp": synthetic.no_form_def_document(2), "broken.afp": b"invalid"}
    for name, content in files.items():
        (folder / name).write_bytes(content)
    (folder / "nested").mkdir()
    (folder / "nested" / "ignored.afp").write_bytes(grouped)
    (folder / "notes.txt").write_text("ignored")
    target = tmp_path / "folder.xlsx"
    body = {"folder": str(folder), "output": str(target)}
    headers = {HEADER_NAME: app_client.get("/api/csrf").get_json()["token"]}
    route = "/api/reports/folder"
    assert app_client.post(route, json=body).status_code == 403
    job = wait_job(app_client, app_client.post(route, json=body, headers=headers))
    assert job["state"] == "completed", job
    assert job["result"]["files"] == 4
    workbook = load_workbook(target)
    summary = {row[0]: row[1] for row in workbook["Summary"].values if row[0]}
    assert summary["AFP pages"] == "Unknown"
    assert summary["Known AFP pages subtotal"] == 7
    assert summary["Estimated sheets"] == "Unknown"
    assert summary["Actual mailpieces"] == "Unknown"
    names, *details = workbook["Files"].values
    rows = {row[0]: dict(zip(names, row)) for row in details}
    assert rows["'=one.AFP"]["Named page groups (BNG)"] == 1
    assert rows["two.afp"]["Estimated sheets"] == 2
    assert rows["unknown.afp"]["Estimated sheets"] == "Unknown"
    assert rows["broken.afp"]["AFP pages"] == "Unknown"
    assert workbook["Files"]["A2"].data_type == "s"
    workbook.close()
    assert app_client.post(route, json=body, headers=headers).status_code == 409
    assert app_client.get("/api/documents").get_json()["documents"] == []
    assert all((folder / name).read_bytes() == content for name, content in files.items())


def test_folder_report_rejects_an_output_alias_of_an_afp(app_client, tmp_path):
    folder = tmp_path / "input"
    folder.mkdir()
    source = folder / "example.afp"
    source.write_bytes(synthetic.simplex_document(2))
    target = tmp_path / "report.xlsx"
    os.link(source, target)
    headers = {HEADER_NAME: app_client.get("/api/csrf").get_json()["token"]}
    body = {"folder": str(folder), "output": str(target), "overwrite": True}
    result = wait_job(app_client, app_client.post("/api/reports/folder", json=body, headers=headers))
    assert result["state"] == "failed"
    assert source.read_bytes() == target.read_bytes() == synthetic.simplex_document(2)
    assert not list(tmp_path.glob("*.part"))


def test_generate_folder_report_from_production_report_tab(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "example.afp"
    source.write_bytes(synthetic.simplex_document(2))
    target = tmp_path / "report.xlsx"
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 900, "height": 680})
            page.goto(url)
            page.locator('[data-tab="report"]').click()
            page.locator("#folder-report-open").click()
            page.locator("#folder-report-source").fill(str(tmp_path))
            page.locator("#folder-report-output").fill(str(target))
            page.locator("#folder-report-run").click()
            status = page.locator("#folder-report-status")
            playwright.expect(status).to_contain_text("Excel report saved for 1 AFP files")
            original = target.read_bytes()
            page.locator("#folder-report-run").click()
            page.locator("#overwrite-cancel").click()
            playwright.expect(status).to_have_text("Existing report kept.")
            assert target.read_bytes() == original
            browser.close()
    finally:
        server.stop()
