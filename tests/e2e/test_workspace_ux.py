"""Desktop-style workbench behaviour with synthetic local documents."""
import hashlib
import pytest


def test_multiple_documents_and_keyboard_navigation(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing import synthetic
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    first, second = tmp_path / "first.afp", tmp_path / "second.afp"
    for path in (first, second):
        path.write_bytes(synthetic.demo_job())
    before = [hashlib.sha256(p.read_bytes()).digest() for p in (first, second)]
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url)
            splitter = page.locator("#tree-resize")
            splitter.focus()
            splitter.press("ArrowRight")
            assert splitter.get_attribute("aria-valuenow") == "256"
            for path in (first, second):
                if path == second:
                    page.locator("#open-new").click()
                page.locator("#path-input").fill(str(path))
                page.locator("#open-button").click()
                page.locator("#doc-name").get_by_text(path.name).wait_for()
                page.locator("#page-holder svg").wait_for()
                if path == first:
                    page.locator("#page-number").fill("3")
                    page.locator("#page-number").dispatch_event("change")
                    page.wait_for_function("() => document.querySelector('#page-number').value === '3'")
            assert page.locator(".document-tab").count() == 2
            page.locator(".document-tab").filter(has_text="first.afp").click()
            page.wait_for_function("() => document.querySelector('#page-number').value === '3'")
            page.locator("#viewer-stage").focus()
            page.keyboard.press("PageDown")
            page.wait_for_function("() => document.querySelector('#page-number').value === '4'")
            page.keyboard.press("Control+g")
            assert page.locator("#page-number").evaluate("(e) => document.activeElement === e")
            page.locator("#close-document").click()
            page.locator("#doc-name").get_by_text("second.afp").wait_for()
            assert page.locator(".document-tab").count() == 1
            page.locator("#close-document").click()
            page.locator("#recent-list").get_by_text("first.afp", exact=False).click()
            page.locator("#doc-name").get_by_text("first.afp").wait_for()
            page.wait_for_function("() => document.querySelector('#page-number').value === '4'")
            page.locator("#close-document").click()
            page.locator("#recent-clear").click()
            page.locator("#recent-list").get_by_text("No recent files.").wait_for()
            browser.close()
    finally:
        server.stop()
    assert before == [hashlib.sha256(p.read_bytes()).digest() for p in (first, second)]
