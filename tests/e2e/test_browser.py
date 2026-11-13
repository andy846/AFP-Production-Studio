"""Real Edge workbench smoke test, using synthetic data only."""
import hashlib
import os
from pathlib import Path

import pytest


def test_workbench_index_and_engineering_in_real_browser(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing import synthetic
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "demo.afp"
    source.write_bytes(synthetic.demo_job())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    server = LocalServer(Config())
    url = server.start()
    errors, requests = [], []
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge")
            except Exception:
                try:
                    browser = p.chromium.launch()
                except Exception:
                    if os.getenv("AFP_STUDIO_REQUIRE_UI") == "1":
                        pytest.fail("No browser available for required workbench smoke test")
                    pytest.skip("No Edge or Playwright Chromium available")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.goto(url)
            page.locator("#path-input").fill(str(source))
            page.locator("#open-button").click()
            page.locator("#page-holder svg").wait_for(timeout=30000)
            assert page.locator("#page-total").inner_text() == "/ 4"
            for tab in ("structure", "preflight", "resources", "report", "settings", "document-index"):
                if tab not in ("preflight", "report"):
                    page.locator("#tools-toggle").click()
                page.locator(f'[data-tab="{tab}"]').click()
                assert page.locator(f'[data-panel="{tab}"]').is_visible()
            page.locator("#index-kind").select_option("text_runs")
            page.locator("#index-codepage").select_option("cp500")
            page.wait_for_function("() => document.querySelector('#document-index-table').textContent.includes('AFP Production Studio')")
            page.locator("#tools-toggle").click()
            page.locator('[data-tab="engineering"]').click()
            target = tmp_path / "copy.afp"
            page.locator("#engineering-path").fill(str(target))
            page.locator("#engineering-run").click()
            page.wait_for_function("() => document.querySelector('#engineering-result').textContent.includes('byte_identical')")
            assert target.read_bytes() == source.read_bytes()
            page.locator('[data-tab="viewer"]').click()
            page.locator("#page-number").fill("4")
            page.locator("#page-number").dispatch_event("change")
            page.wait_for_function("() => document.querySelector('#page-holder').textContent.includes('Page 4 of 4')")
            captures = Path(__file__).resolve().parents[2] / ".measure"
            captures.mkdir(exist_ok=True)
            page.screenshot(path=str(captures / "v03-workbench-wide.png"))
            page.set_viewport_size({"width": 800, "height": 700})
            page.screenshot(path=str(captures / "v03-workbench-narrow.png"))
            assert page.locator("#page-number").is_visible()
            assert not errors, errors
            assert all(request.startswith(url) or request.startswith(("data:", "blob:")) for request in requests), requests
            browser.close()
    finally:
        server.stop()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
