"""Workflow shell contracts; PDF job execution belongs to its own controller."""

import hashlib

import pytest


@pytest.fixture
def workflow_page(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing import synthetic

    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "workflow.afp"
    source.write_bytes(synthetic.demo_job())
    original = hashlib.sha256(source.read_bytes()).digest()
    server = LocalServer(Config())
    try:
        url = server.start()
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel="msedge")
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(url)
                page.locator("#path-input").fill(str(source))
                page.locator("#open-button").click()
                page.locator("#page-holder svg").wait_for()
                yield page, playwright.expect
            finally:
                browser.close()
    finally:
        server.stop()
    assert hashlib.sha256(source.read_bytes()).digest() == original


def test_primary_navigation_and_tools_keep_legacy_clicks(workflow_page):
    page, expect = workflow_page
    assert page.locator("#tabs > [data-tab]").evaluate_all(
        "buttons => buttons.map(b => b.dataset.tab)"
    ) == ["viewer", "preflight", "export", "report"]
    tools = page.locator("#tools-menu")
    assert tools.locator("[data-tab]").evaluate_all(
        "buttons => buttons.map(b => b.dataset.tab)"
    ) == ["compare", "structure", "document-index", "resources", "engineering", "batch", "settings"]
    expect(tools).to_be_hidden()
    for tab in ("structure", "document-index", "resources", "engineering", "batch", "settings", "compare"):
        # Existing warnings, reference links and document restore use .click().
        page.locator(f'[data-tab="{tab}"]').evaluate("button => button.click()")
        expect(page.locator(f'[data-panel="{tab}"]')).to_be_visible()
        expect(page.locator("#tools-toggle")).to_have_class("tools-toggle is-active")
        expect(page.locator("#tools-active-label")).to_have_text(
            page.locator(f'[data-tab="{tab}"]').inner_text()
        )
        expect(tools).to_be_hidden()
    page.locator('[data-tab="viewer"]').click()
    expect(page.locator("#tools-active-label")).to_have_text("")
    expect(page.locator("#tools-toggle")).not_to_have_class("tools-toggle is-active")


def test_tools_keyboard_escape_outside_click_and_focus(workflow_page):
    page, expect = workflow_page
    toggle = page.locator("#tools-toggle")
    toggle.focus()
    toggle.press("ArrowDown")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator('[data-tab="compare"]')).to_be_focused()
    page.keyboard.press("End")
    expect(page.locator('[data-tab="settings"]')).to_be_focused()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    expect(page.locator('[data-tab="structure"]')).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator('[data-panel="structure"]')).to_be_visible()
    expect(toggle).to_be_focused()
    toggle.press("ArrowUp")
    expect(page.locator('[data-tab="settings"]')).to_be_focused()
    page.keyboard.press("Escape")
    expect(toggle).to_be_focused()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    page.locator('[data-tab="export"]').click()
    expect(page.locator("#tools-menu")).to_be_hidden()
    toggle.click()
    page.locator('[data-tab="settings"]').focus()
    page.keyboard.press("Tab")
    expect(page.locator("#tools-menu")).to_be_hidden()


def test_pdf_ui_hooks_defaults_and_persistent_task_entry(workflow_page):
    page, expect = workflow_page
    page.locator("#export-pdf").click()
    expect(page.locator('[data-panel="export"]')).to_be_visible()
    expect(page.locator('[data-tab="export"]')).to_have_attribute("aria-current", "page")
    expect(page.locator("#pdf-pages-mode")).to_have_value("all")
    assert page.locator("#pdf-pages-mode option").evaluate_all(
        "options => options.map(o => o.value)"
    ) == ["all", "current", "range"]
    expect(page.locator("#pdf-dpi")).to_have_value("150")
    assert page.locator("#pdf-dpi option").evaluate_all(
        "options => options.map(o => o.value)"
    ) == ["150", "200", "300"]
    assert page.locator("#pdf-progress").evaluate("el => el.tagName") == "PROGRESS"
    expect(page.locator("#pdf-progress")).to_have_attribute("max", "1")
    assert not page.locator("#pdf-path").evaluate("el => el.required")
    expect(page.locator("#pdf-source")).to_contain_text("workflow.afp")
    for native_control in ("browse", "open", "reveal"):
        expect(page.locator(f"#pdf-{native_control}")).to_be_hidden()
    for control in ("form", "range", "path", "browse", "start", "cancel", "status", "result", "warning-list", "open", "reveal"):
        assert page.locator("#pdf-export-form" if control == "form" else f"#pdf-{control}").count() == 1
    expect(page.locator("#pdf-accept-limitations")).to_be_hidden()
    expect(page.locator("#pdf-cancel")).to_be_disabled()
    expect(page.locator("#task-tray")).to_be_visible()
    page.locator("#pdf-pages-mode").select_option("range")
    page.locator("#pdf-range").fill("1,3-4")
    page.locator("#pdf-path").fill("C:\\Output\\preview.pdf")
    page.locator('[data-tab="preflight"]').click()
    expect(page.locator("#task-tray")).to_be_visible()
    page.locator("#task-tray-open").click()
    expect(page.locator('[data-panel="export"]')).to_be_visible()
    expect(page.locator("#pdf-range")).to_have_value("1,3-4")
    expect(page.locator("#pdf-path")).to_have_value("C:\\Output\\preview.pdf")
    assert page.locator(".brand-mark").get_attribute("src").endswith("/studio-icon.png")


def test_desktop_layout_resize_collapse_and_reset_hook(workflow_page):
    page, expect = workflow_page
    page.locator("#tree-resize").press("ArrowRight")
    expect(page.locator("#tree-resize")).to_have_attribute("aria-valuenow", "256")
    page.locator("#toggle-details").click()
    expect(page.locator("#toggle-details")).to_have_attribute("aria-expanded", "false")
    page.locator("#toggle-details").click()
    page.set_viewport_size({"width": 1000, "height": 900})
    expect(page.locator("#tree-resize")).to_be_visible()
    expect(page.locator("#details-resize")).to_be_visible()
    assert page.locator("#tree-panel").bounding_box()["width"] == 256
    assert page.locator("#details-panel").bounding_box()["width"] == 300
    page.evaluate("window.addEventListener('studio-reset-layout', () => { window.resetRequested = true; })")
    page.locator("#reset-layout").click()
    page.wait_for_function("window.resetRequested === true")


@pytest.mark.parametrize("width", [800, 600, 375])
def test_narrow_layout_has_one_drawer_and_retains_themes(workflow_page, width):
    page, expect = workflow_page
    page.set_viewport_size({"width": width, "height": 900})
    expect(page.locator("#tree-panel")).to_be_hidden()
    expect(page.locator("#details-panel")).to_be_hidden()
    page.locator("#toggle-tree").click()
    expect(page.locator("#tree-panel")).to_be_visible()
    expect(page.locator("#details-panel")).to_be_hidden()
    page.locator("#toggle-details").click()
    expect(page.locator("#details-panel")).to_be_visible()
    expect(page.locator("#tree-panel")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator("#details-panel")).to_be_hidden()
    expect(page.locator("#toggle-details")).to_be_focused()
    page.locator("#export-pdf").click()
    expect(page.locator('[data-panel="export"]')).to_be_visible()
    page.locator("#tools-toggle").click()
    menu_bounds = page.locator("#tools-menu").bounding_box()
    assert menu_bounds["x"] >= 0
    assert menu_bounds["x"] + menu_bounds["width"] <= width
    page.keyboard.press("Escape")
    page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert page.locator("html").evaluate("e => getComputedStyle(e).colorScheme") == "dark"
    assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth")
    page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.locator("#toggle-tree").click()
    page.locator("#pdf-path").focus()
    expect(page.locator("#tree-panel")).to_be_hidden()
    page.locator("#reset-layout").click()
    expect(page.locator("#toggle-tree")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#toggle-details")).to_have_attribute("aria-expanded", "false")
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(page.locator("#tree-panel")).to_be_visible()
    expect(page.locator("#details-panel")).to_be_visible()
