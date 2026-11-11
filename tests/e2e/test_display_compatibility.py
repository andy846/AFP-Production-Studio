"""Preview omissions are visible to the operator, using synthetic AFP only."""
import pytest

from afpstudio.testing import synthetic as sf


def test_desktop_bridge_waits_for_methods_before_opening_startup_file(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "startup.afp"
    source.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(210, 297) + sf.epg() + sf.edt())
    server = LocalServer(Config())
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page()
            page.add_init_script("window.pywebview = {api: {}};")
            page.goto(server.start())
            page.wait_for_timeout(300)
            assert "Could not open startup file" not in page.locator("#open-error").inner_text()
            page.evaluate("""path => {
                window.startupCalls = 0;
                window.pywebview.api.consume_startup_file = async () => {
                    window.startupCalls++; return path;
                };
                window.dispatchEvent(new Event('pywebviewready'));
            }""", str(source))
            playwright.expect(page.locator("#page-holder svg")).to_be_visible()
            page.wait_for_timeout(200)
            assert page.evaluate("window.startupCalls") == 1
            browser.close()
    finally:
        server.stop()


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
            playwright.expect(page.locator("#render-warning-summary")).to_contain_text("Incomplete preview")
            playwright.expect(panel).to_have_attribute("open", "")
            playwright.expect(panel).to_contain_text("page segment")
            playwright.expect(panel).to_contain_text("field offset")
            panel.get_by_role("button", name="Inspect field").first.click()
            playwright.expect(page.locator("#structure-table tr.is-selected")).to_have_count(1)
            page.locator('[data-tab="viewer"]').click()
            page.locator("#page-next").click()
            playwright.expect(page.locator("#page-number")).to_have_value("2")
            playwright.expect(page.locator("#page-holder svg")).to_be_visible()
            playwright.expect(panel).to_be_hidden()
            browser.close()
    finally:
        server.stop()


def test_warning_offset_can_locate_a_global_resource_field(app_client, tmp_path):
    from test_engineering import open_session
    source = tmp_path / "resource.afp"
    name = sf.encode_name("SEG01")
    source.write_bytes(sf.bdt() + sf.structured_field(0xD3A85F, name)
                       + sf.structured_field(0xD3A95F) + sf.bpg() + sf.pgd(210, 297) + sf.epg() + sf.edt())
    result, _ = open_session(app_client, source)
    endpoint = "/api/documents/" + result["document_id"] + "/fields"
    response = app_client.get(endpoint + "?at_offset=9&limit=3")
    assert response.status_code == 200
    assert any(row["offset"] == 9 for row in response.get_json()["fields"])
    assert response.get_json()["focused_offset"] == 9
    assert app_client.get(endpoint + "?at_offset=1").status_code == 400


def test_browser_image_decode_failure_is_visible(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "bad-jpeg.afp"
    image = (b"\x94\x09\x00\x02\xd0\x02\xd0\x00\x08\x00\x08"
             b"\x95\x02\x83\x01\xfe\x92\x00\x06\xff\xd8BAD!")
    source.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(210, 297)
                      + sf.structured_field(0xD3A8FB) + sf.structured_field(0xD3EEFB, image)
                      + sf.structured_field(0xD3A9FB) + sf.epg() + sf.edt())
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
            playwright.expect(panel).to_contain_text("browser could not display")
            playwright.expect(panel).to_have_attribute("open", "")
            browser.close()
    finally:
        server.stop()
