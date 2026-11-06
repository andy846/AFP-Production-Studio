from afpstudio.document_model.model import DocumentStore
from afpstudio.testing import synthetic


def test_document_preview_search_returns_decoded_matches_and_original_offsets(app_client, tmp_path, monkeypatch):
    import time
    from threading import Event
    from afpstudio.viewer.renderer import PageRenderer

    path = tmp_path / "search.afp"
    path.write_bytes(synthetic.demo_job())
    token = app_client.get("/api/csrf").get_json()["token"]
    headers = {"X-CSRF-Token": token}

    def finish(response, expected="completed"):
        assert response.status_code == 200
        job_id = response.get_json()["job"]["id"]
        for _ in range(500):
            job = app_client.get("/api/jobs/" + job_id).get_json()
            if job["state"] in ("completed", "failed", "cancelled"):
                assert job["state"] == expected, job
                return job.get("result")
            time.sleep(.01)
        raise AssertionError("Job did not complete")

    opened = finish(app_client.post("/api/documents/open", json={"path": str(path)}, headers=headers))
    url = "/api/documents/" + opened["document_id"] + "/text-search"
    assert app_client.post(url, json={"q": "letterhead"}, headers=headers).status_code == 400
    result = finish(app_client.post(url, json={"q": "LETTERHEAD", "codepage": "cp500"}, headers=headers))
    assert result["scanned_pages"] == 4
    assert result["complete"] is True
    assert [hit["page"] for hit in result["items"]] == [3, 4]
    for hit in result["items"]:
        assert "letterhead" in hit["text"]
        assert path.read_bytes()[hit["offset"] + 3:hit["offset"] + 6] == b"\xd3\xee\x9b"

    started, release = Event(), Event()
    pages = []
    original = PageRenderer.text_runs

    def pause(renderer, page, **kwargs):
        pages.append(page)
        started.set()
        if not release.wait(5):
            raise AssertionError("Search did not resume")
        return original(renderer, page, **kwargs)

    monkeypatch.setattr(PageRenderer, "text_runs", pause)
    response = app_client.post(url, json={"q": "letterhead", "codepage": "cp500"}, headers=headers)
    try:
        assert started.wait(5)
        job_id = response.get_json()["job"]["id"]
        assert app_client.post("/api/jobs/" + job_id + "/cancel", headers=headers).status_code == 200
    finally:
        release.set()
    assert finish(response, "cancelled") is None
    assert pages == [0]


def test_positioned_runs_have_page_coordinates_and_original_ptx_offset(tmp_path):
    path = tmp_path / "text.afp"
    prefix = synthetic.bdt() + synthetic.bpg() + synthetic.pgd(210, 297)
    path.write_bytes(prefix + synthetic.ptx_text([("REFERENCE42", 480)]) + synthetic.epg() + synthetic.edt())
    document = DocumentStore().open(path)
    data = document.renderer.text_runs(0, codepage="cp500")
    run = next(row for row in data["items"] if "REFERENCE42" in row["text"])
    assert run["page"] == 1 and run["offset"] == len(prefix)
    assert run["offset_kind"] == "structured_field"
    assert type(run["x"]) is int and type(run["y"]) is int
    assert data["basis"] == "readAFP_preview"
    assert data["codepage_basis"] == "embedded_mapping_or_operator_fallback"


def test_text_runs_require_explicit_supported_fallback(tmp_path):
    import pytest
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.demo_job())
    renderer = DocumentStore().open(path).renderer
    with pytest.raises(ValueError):
        renderer.text_runs(0, codepage="automatic")
