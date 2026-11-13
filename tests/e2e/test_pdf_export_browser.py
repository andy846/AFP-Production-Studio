import hashlib
import threading

import pytest
from pypdf import PdfReader

from afpstudio.testing import synthetic as sf


@pytest.fixture
def pdf_browser(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "clean.afp"
    source.write_bytes(sf.bdt() + (sf.bpg() + sf.pgd(25.4, 25.4) + sf.epg()) * 24 + sf.edt())
    digest = hashlib.sha256(source.read_bytes()).digest()
    server = LocalServer(Config())
    errors, requests = [], []
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.goto(url)
            page.locator("#path-input").fill(str(source))
            page.locator("#open-button").click()
            playwright.expect(page.locator("#page-holder svg")).to_be_visible()
            yield page, playwright.expect, source
            browser.close()
    finally:
        server.stop()
    assert not errors, errors
    assert all(request.startswith(url) or request.startswith(("data:", "blob:")) for request in requests)
    assert hashlib.sha256(source.read_bytes()).digest() == digest


def test_export_all_range_and_explicit_overwrite(pdf_browser, tmp_path):
    page, expect, _ = pdf_browser
    page.keyboard.press("Control+Shift+e")
    expect(page.locator('[data-panel="export"]')).to_be_visible()
    output = tmp_path / "all.pdf"
    page.locator("#pdf-path").fill(str(output))
    page.locator("#pdf-start").click()
    expect(page.locator("#pdf-status")).to_contain_text("PDF completed", timeout=15000)
    assert len(PdfReader(output).pages) == 24
    page.locator("#pdf-pages-mode").select_option("range")
    page.locator("#pdf-range").fill("8,1-3,2")
    page.locator("#pdf-start").click()
    expect(page.locator("#overwrite-dialog")).to_be_visible()
    page.locator("#overwrite-cancel").click()
    expect(page.locator("#pdf-status")).to_contain_text("Existing output kept")
    assert len(PdfReader(output).pages) == 24
    page.locator("#pdf-start").click()
    page.locator("#overwrite-confirm").click()
    expect(page.locator("#pdf-result")).to_contain_text("4 pages", timeout=15000)
    assert len(PdfReader(output).pages) == 4


def test_missing_content_stops_then_explicitly_exports_with_warning(pdf_browser, tmp_path):
    page, expect, _ = pdf_browser
    missing = tmp_path / "missing.afp"
    missing.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(25.4, 25.4)
        + sf.structured_field(0xD3AF5F, sf.encode_name("ABSENT") + bytes(6)) + sf.epg() + sf.edt())
    page.locator("#open-new").click()
    page.locator("#path-input").fill(str(missing))
    page.locator("#open-button").click()
    expect(page.locator("#doc-name")).to_have_text("missing.afp")
    expect(page.locator("#render-warning-panel")).to_be_visible()
    page.locator("#export-pdf").click()
    output = tmp_path / "missing.pdf"
    page.locator("#pdf-path").fill(str(output))
    page.locator("#pdf-start").click()
    expect(page.locator("#pdf-status")).to_contain_text("Export stopped", timeout=15000)
    assert not output.exists()
    page.locator("#pdf-warning-list").get_by_role("button", name="Inspect field").first.click()
    expect(page.locator("#structure-table tr.is-selected")).to_have_count(1)
    page.locator('[data-tab="export"]').click()
    page.locator("#pdf-accept-limitations").click()
    expect(page.locator("#pdf-status")).to_contain_text("Completed with preview warnings", timeout=15000)
    assert len(PdfReader(output).pages) == 1


def test_export_can_cancel_after_switching_tools(pdf_browser, tmp_path, monkeypatch):
    import afpstudio.engineering.pdf_export as engine
    page, expect, _ = pdf_browser
    original = engine.export_pdf
    release = threading.Event()
    def pause(*args, **kwargs):
        progress = kwargs["progress"]
        def wait(fraction):
            progress(fraction)
            assert release.wait(5)
        kwargs["progress"] = wait
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, "export_pdf", pause)
    output = tmp_path / "cancel.pdf"
    page.locator("#export-pdf").click()
    page.locator("#pdf-path").fill(str(output))
    page.locator("#pdf-start").click()
    expect(page.locator("#pdf-status")).to_contain_text("Wrote page", timeout=15000)
    try:
        page.locator('[data-tab="viewer"]').click()
        expect(page.locator("#pdf-task-tray")).to_contain_text("clean.afp")
        page.locator("#task-tray-open").click()
        page.locator("#pdf-cancel").click()
    finally:
        release.set()
    expect(page.locator("#pdf-status")).to_contain_text("Export cancelled", timeout=15000)
    assert not output.exists()
