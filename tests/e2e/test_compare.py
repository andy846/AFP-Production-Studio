import csv
import time
import pytest

from openpyxl import load_workbook

from afpstudio.comparison import PageEvidence, align, collect, compare_rows
from afpstudio.document_model.model import DocumentStore
from afpstudio.security.csrf import HEADER_NAME
from afpstudio.testing import synthetic as sf


def _afp(texts):
    return sf.bdt() + b"".join(sf.bpg() + sf.pgd(210, 297) +
                              sf.ptx_text([(text, 480)]) + sf.epg() for text in texts) + sf.edt()


def test_alignment_ignores_crossings_and_preserves_unmatched_ranges():
    def page(fingerprint):
        return PageEvidence(fingerprint, None, (210, 297), {})
    old = [page(value) for value in ("a", "b", "c", "d", "d")]
    new = [page(value) for value in ("a", "c", "b", "insert", "d", "d")]
    pairs = align(old, new)
    assert pairs[0] == (1, 1, "original")
    assert not any(basis == "original" for _, _, basis in pairs[1:])
    assert (4, 5, "manual") in align(old, new, [(4, 5)])
    deleted = align([page(value) for value in ("a", "b", "c")],
                    [page(value) for value in ("a", "c")])
    assert deleted == [(1, 1, "original"), (2, None, "left_only"), (3, 2, "original")]
    blank = PageEvidence("unique blank", None, (210, 297), {}, text="empty")
    assert align([blank], [blank]) == [(1, 1, "review")]


def test_identical_text_can_align_pages_with_position_size_and_media_changes(tmp_path):
    def job(width, y, medium):
        return (sf.bdt() + sf.bpg() + sf.pgd(width, 297) +
                sf.ptx_text([("Invoice reference 123", y)]) + sf.imm(medium) + sf.epg() + sf.edt())
    old, new = tmp_path / "old.afp", tmp_path / "new.afp"
    old.write_bytes(job(210, 480, "MAP01"))
    new.write_bytes(job(216, 540, "MAP02"))
    store = DocumentStore()
    a, b = [collect(store.open(path), "cp500", lambda *_: None) for path in (old, new)]
    row, = compare_rows(a, b, [])
    assert row["basis"] == "text"
    assert set(row["changes"]) >= {"position", "size", "requested.medium_map"}


def test_compare_api_alignment_visual_export_and_invalidation(app_client, tmp_path):
    headers = {HEADER_NAME: app_client.get("/api/csrf").get_json()["token"]}

    def post(path, body):
        return app_client.post(path, json=body, headers=headers)

    def finish(response):
        assert response.status_code == 200, response.get_json()
        job_id = response.get_json()["job"]["id"]
        for _ in range(800):
            job = app_client.get("/api/jobs/" + job_id).get_json()
            if job["state"] in ("completed", "failed", "cancelled"):
                assert job["state"] == "completed", job
                return job["result"]
            time.sleep(.01)
        raise AssertionError("Comparison timed out")

    left, right = tmp_path / "old.afp", tmp_path / "new.afp"
    left_data = _afp(["Invoice reference A", "Invoice reference B", "Invoice reference C"])
    right_data = _afp(["Invoice reference A", "Inserted sheet", "Invoice reference B", "Changed reference C"])
    left.write_bytes(left_data)
    right.write_bytes(right_data)
    old, new = [finish(post("/api/documents/open", {"path": str(path)}))["document_id"] for path in (left, right)]
    cid = finish(post("/api/comparisons", {"left": old, "right": new}))["comparison_id"]
    endpoint = "/api/comparisons/" + cid
    result = app_client.get(endpoint).get_json()
    assert [(r["left"], r["right"], r["basis"]) for r in result["items"]] == [
        (1, 1, "original"), (None, 2, "right_only"), (2, 3, "original"), (3, 4, "review")]
    assert result["actual_media"] == "unknown"
    assert app_client.get(endpoint + "?filter=unmatched").get_json()["total"] == 1
    assert app_client.get(endpoint + "?filter=text").get_json()["total"] == 1
    assert post(endpoint + "/anchors", {"anchors": [[3, 4], [2, 4]], "revision": 1}).status_code == 400
    assert post(endpoint + "/anchors", {"anchors": [[3, 4]], "revision": 1}).status_code == 200
    assert post(endpoint + "/anchors", {"anchors": [], "revision": 1}).status_code == 409
    assert app_client.get(endpoint).get_json()["items"][3]["basis"] == "manual"
    assert finish(post(endpoint + "/visual", {"row": 0, "revision": 2}))["changed_pixels"] == 0
    assert finish(post(endpoint + "/visual", {"row": 3, "revision": 2}))["changed_pixels"] > 0
    assert app_client.get(endpoint + "/visual/2/3.png").data.startswith(b"\x89PNG")
    assert app_client.get(endpoint + "?filter=visual").get_json()["total"] == 1
    output = tmp_path / "diff.csv"
    assert post(endpoint + "/export", {"format": "csv", "path": str(output), "revision": 2}).status_code == 200
    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[3]["Pairing"] == "manual" and rows[2]["Visual preview"] == "Not compared"
    assert "Old preview excerpt" not in rows[3]
    assert post(endpoint + "/export", {"format": "csv", "path": str(output), "revision": 2}).status_code == 409
    xlsx = output.with_suffix(".xlsx")
    assert post(endpoint + "/export", {"format": "xlsx", "path": str(xlsx),
                                       "revision": 2, "include_text": True}).status_code == 200
    workbook = load_workbook(xlsx, read_only=True)
    assert workbook.sheetnames == ["Summary", "Differences"]
    assert list(workbook["Differences"].values)[4][-1] == "Changed reference C"
    workbook.close()
    assert post(endpoint + "/export", {"format": "csv", "path": str(left),
                                       "revision": 2, "overwrite": True}).status_code == 400
    assert left.read_bytes() == left_data and right.read_bytes() == right_data
    assert post(endpoint + "/anchors", {"anchors": [], "revision": 2}).status_code == 200
    assert app_client.get(endpoint).get_json()["items"][3]["basis"] == "review"
    assert app_client.get(endpoint + "/visual/2/3.png").status_code == 404
    second = finish(post("/api/comparisons", {"left": old, "right": new}))["comparison_id"]
    right.write_bytes(right_data + b"changed")
    assert app_client.get("/api/comparisons/" + second).status_code == 409
    assert app_client.delete("/api/documents/" + new, headers=headers).status_code == 200
    assert app_client.get(endpoint).status_code == 404


def test_compare_workbench_in_edge(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    paths = [tmp_path / "old.afp", tmp_path / "new.afp"]
    paths[0].write_bytes(_afp(["Invoice reference A", "Invoice reference B"]))
    paths[1].write_bytes(_afp(["Invoice reference A", "Inserted sheet", "Invoice reference B"]))
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(url)
            for index, path in enumerate(paths):
                if index:
                    page.locator("#open-new").click()
                page.locator("#path-input").fill(str(path))
                page.locator("#open-button").click()
                page.locator("#doc-name").get_by_text(path.name).wait_for()
            page.locator('[data-tab="compare"]').click()
            page.locator("#compare-run").click()
            page.locator("#compare-results").wait_for()
            page.locator("#compare-filter").select_option("unmatched")
            page.locator("#compare-rows").get_by_text("New only", exact=False).wait_for()
            page.locator("#compare-filter").select_option("all")
            page.locator("#compare-rows").get_by_text("2 ↔ 3", exact=False).click()
            assert page.locator("#compare-page-left").input_value() == "2"
            assert page.locator("#compare-page-right").input_value() == "3"
            page.locator("#compare-sync").uncheck()
            page.locator("#compare-page-right").fill("1")
            page.locator("#compare-page-right").dispatch_event("change")
            assert page.locator("#compare-stage-right img").get_attribute("alt") == "New page 1"
            page.locator("#compare-sync").check()
            page.locator("#compare-visual").click()
            page.locator("#compare-overlay-caption").get_by_text("0 of", exact=False).wait_for()
            page.wait_for_function("() => document.querySelector('#compare-overlay-image').naturalWidth > 0")
            output = tmp_path / "report.csv"
            page.locator("#compare-output").fill(str(output))
            page.locator("#compare-csv").click()
            page.locator("#compare-status").get_by_text("Report written", exact=False).wait_for()
            assert output.exists()
            assert not errors
            browser.close()
    finally:
        server.stop()
