"""Preview omissions are visible to the operator, using synthetic AFP only."""
import pytest

from afpstudio.testing import synthetic as sf


def test_preview_warning_panel_follows_the_selected_page(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "missing-background.afp"
    source.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(210, 297)
        + sf.structured_field(0xD3AF5F, sf.encode_name("MISSING") + bytes(6))
        + sf.ptx_text([("Body", 480)]) + sf.epg()
        + sf.bpg() + sf.pgd(210, 297) + sf.epg() + sf.edt())
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page()
            page.goto(url)
            page.locator("#path-input").fill(str(source))
            page.locator("#open-button").click()
            panel = page.locator("#render-warning-panel")
            playwright.expect(panel).to_be_visible()
            page.locator("#render-warning-summary").click()
            playwright.expect(panel).to_contain_text("page segment")
            playwright.expect(panel).to_contain_text("field offset")
            page.locator("#page-next").click()
            playwright.expect(page.locator("#page-number")).to_have_value("2")
            playwright.expect(page.locator("#page-holder svg")).to_be_visible()
            playwright.expect(panel).to_be_hidden()
            browser.close()
    finally:
        server.stop()
