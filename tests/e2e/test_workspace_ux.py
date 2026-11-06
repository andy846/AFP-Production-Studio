"""Desktop-style workbench behaviour with synthetic local documents."""
import hashlib
import json
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
            assert not splitter.is_visible()
            page.locator("#open-button").click()
            assert page.locator("#path-input").get_attribute("aria-invalid") == "true"
            assert page.locator("#path-input").evaluate("(input) => input === document.activeElement")
            for path in (first, second):
                if path == second:
                    page.locator("#open-new").click()
                page.locator("#path-input").fill(str(path))
                page.locator("#open-button").click()
                page.locator("#doc-name").get_by_text(path.name).wait_for()
                page.locator("#page-holder svg").wait_for()
                if path == first:
                    splitter.press("ArrowRight")
                    assert splitter.get_attribute("aria-valuenow") == "256"
                    page.locator("#page-number").fill("3")
                    page.locator("#page-number").dispatch_event("change")
                    page.wait_for_function("() => document.querySelector('#page-number').value === '3'")
            assert page.locator(".document-tab").count() == 2
            page.locator('[data-tab="compare"]').click()
            page.locator("#recent-open").click()
            page.locator("#opener-back").click()
            assert page.locator('[data-tab="compare"]').get_attribute("aria-current") == "page"
            page.locator('[data-tab="viewer"]').click()
            page.locator(".document-tab").filter(has_text="first.afp").click()
            page.wait_for_function("() => document.querySelector('#page-number').value === '3'")
            preview = page.locator("#page-holder svg")
            preview.wait_for()
            paper = preview.evaluate("svg => getComputedStyle(svg).backgroundColor")
            theme = page.get_by_role("button", name="Dark mode")
            with page.expect_response(lambda res: res.url.endswith("/api/workspace") and res.request.method == "POST") as saved:
                theme.click()
            assert saved.value.ok
            playwright.expect(theme).to_have_attribute("aria-pressed", "true")
            assert page.locator("html").evaluate("el => getComputedStyle(el).colorScheme") == "dark"
            assert preview.evaluate("svg => getComputedStyle(svg).backgroundColor") == paper == "rgb(255, 255, 255)"
            another = browser.new_page()
            another.goto(url)
            playwright.expect(another.locator("html")).to_have_attribute("data-theme", "dark")
            another.close()
            page.bring_to_front()
            page.keyboard.press("ArrowLeft")
            playwright.expect(page.locator("#page-number")).to_have_value("2", timeout=2000)
            page.locator("#search-input").fill("hello")
            page.locator("#search-input").press("Home")
            page.keyboard.press("ArrowRight")
            assert page.locator("#search-input").evaluate("el => el.selectionStart") == 1
            assert page.locator("#page-number").input_value() == "2"
            page.locator("#viewer-stage").focus()
            page.keyboard.press("ArrowRight")
            playwright.expect(page.locator("#page-number")).to_have_value("3")
            page.keyboard.press("PageDown")
            page.wait_for_function("() => document.querySelector('#page-number').value === '4'")
            page.keyboard.press("Control+g")
            assert page.locator("#page-number").evaluate("(e) => document.activeElement === e")
            page.evaluate("""() => {
                const original = window.fetch;
                window.fetch = async function(url, options) {
                    if (options?.method === 'DELETE') {
                        window.fetch = original;
                        await new Promise(resolve => { window.releaseClose = resolve; });
                    }
                    return original.apply(this, arguments);
                };
            }""")
            page.locator("#close-document").click()
            page.wait_for_function("() => typeof window.releaseClose === 'function'")
            page.locator(".document-tab").filter(has_text="second.afp").click()
            page.locator("#doc-name").get_by_text("second.afp").wait_for()
            page.locator("#page-holder svg").wait_for()
            page.locator("#page-next").click()
            page.wait_for_function("() => document.querySelector('#page-number').value === '2'")
            page.evaluate("() => window.releaseClose()")
            page.wait_for_function("() => document.querySelectorAll('.document-tab').length === 1")
            assert page.locator("#page-number").input_value() == "2"
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


def test_native_multi_open_queues_selected_synthetic_files(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing import synthetic
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    paths = [tmp_path / "first.afp", tmp_path / "second.afp"]
    for path in paths:
        path.write_bytes(synthetic.demo_job())
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page()
            files = json.dumps([{"path": str(path)} for path in paths])
            page.add_init_script("""window.pywebview = {api: {
                consume_startup_file: async () => null,
                pick_afp_file: async (multiple) => multiple ?
                    ({cancelled: false, files: FILES}) : ({cancelled: true})
            }};""".replace("FILES", files))
            page.goto(url)
            page.locator("#browse-multiple").click()
            page.get_by_text("2 of 2 files opened.").wait_for()
            assert page.locator(".document-tab").count() == 2
            page.locator("#viewer-stage").focus()
            page.keyboard.press("Control+Tab")
            page.locator("#doc-name").get_by_text("first.afp").wait_for()
            page.keyboard.press("Control+w")
            page.locator("#doc-name").get_by_text("second.afp").wait_for()
            assert page.locator(".document-tab").count() == 1
            browser.close()
    finally:
        server.stop()


def test_viewer_fit_modes_shortcuts_and_resize(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing import synthetic
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "viewer.afp"
    source.write_bytes(synthetic.demo_job())
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url)
            page.locator("#path-input").fill(str(source))
            page.locator("#open-button").click()
            page.locator("#page-holder svg").wait_for()
            stage = page.locator("#viewer-stage")
            stage.focus()
            page.keyboard.press("w")
            assert page.locator("#zoom-width").get_attribute("aria-pressed") == "true"
            before = float(page.locator("#page-holder svg").evaluate("(e) => e.getBoundingClientRect().width"))
            page.set_viewport_size({"width": 1200, "height": 900})
            page.wait_for_function("(old) => document.querySelector('#page-holder svg').getBoundingClientRect().width < old - 20", arg=before)
            page.locator("#zoom-actual").click()
            assert page.locator("#zoom-readout").inner_text() == "100%"
            assert page.locator("#zoom-width").get_attribute("aria-pressed") == "false"
            page.set_viewport_size({"width": 1300, "height": 900})
            assert page.locator("#zoom-readout").inner_text() == "100%"
            page.locator("#page-next").click()
            page.wait_for_function("() => document.querySelector('#page-number').value === '2'")
            page.locator("#page-holder svg").wait_for()
            assert page.locator("#zoom-readout").inner_text() == "100%"
            page.locator("#viewer-stage").focus()
            stage.evaluate("(e) => { e.scrollTop = 0; }")
            bounds = stage.bounding_box()
            page.keyboard.down("Space")
            page.mouse.move(bounds["x"] + 150, bounds["y"] + 200)
            page.mouse.down()
            page.mouse.move(bounds["x"] + 150, bounds["y"] + 90, steps=5)
            page.mouse.up()
            page.keyboard.up("Space")
            assert stage.evaluate("(e) => e.scrollTop") >= 100
            assert "can-pan" not in stage.get_attribute("class")
            page.keyboard.press("f")
            assert page.locator("#zoom-fit").get_attribute("aria-pressed") == "true"
            page.locator("#shortcut-help").click()
            assert page.locator("#shortcuts-dialog").is_visible()
            page.keyboard.press("Escape")
            assert not page.locator("#shortcuts-dialog").is_visible()
            page.locator("#preview-search-open").click()
            page.locator("#preview-search-query").fill("LETTERHEAD")
            page.locator("#preview-search-start").click()
            page.locator("#preview-search-results").get_by_role("button", name="Page 4", exact=False).click()
            assert page.locator("#page-number").input_value() == "4"
            page.locator("#page-holder .preview-search-mark").first.wait_for()
            browser.close()
    finally:
        server.stop()


def test_page_thumbnails_load_on_demand_and_navigate(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing.synthetic import simplex_document
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    source = tmp_path / "thumbnails.afp"
    source.write_bytes(simplex_document(48))
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            previews = []
            page.on("request", lambda req: previews.append(req.url)
                    if req.resource_type == "image" and "/page/" in req.url else None)
            page.goto(url)
            page.locator("#path-input").fill(str(source))
            page.locator("#open-button").click()
            page.locator("#page-holder svg").wait_for()
            assert not previews
            page.locator("#toggle-thumbnails").click(timeout=3000)
            page.wait_for_function("() => document.querySelector('#page-thumbnails img')?.naturalWidth > 0")
            assert 0 < len(previews) < 12
            assert page.locator("#page-thumbnails button").count() == 12
            page.get_by_role("button", name="Show page 2", exact=True).click()
            assert page.locator("#page-number").input_value() == "2"
            page.locator("#page-last").click()
            assert page.get_by_role("button", name="Show page 48", exact=True).get_attribute("aria-current") == "page"
            page.locator("#thumbnail-prev").click()
            page.get_by_role("button", name="Show page 25", exact=True).click()
            assert page.locator("#page-number").input_value() == "25"
            browser.close()
    finally:
        server.stop()


def test_cancelling_multi_open_keeps_completed_file_and_stops_queue(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from afpstudio.config import Config
    from afpstudio.desktop import LocalServer
    from afpstudio.testing import synthetic
    monkeypatch.setenv("AFP_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AFP_STUDIO_ALLOWED_ROOTS", str(tmp_path))
    paths = [tmp_path / "first.afp", tmp_path / "second.afp"]
    for path in paths:
        path.write_bytes(synthetic.demo_job())
    server = LocalServer(Config())
    url = server.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page()
            files = json.dumps([{"path": str(path)} for path in paths])
            page.add_init_script("""window.pywebview = {api: {
                consume_startup_file: async () => null,
                pick_afp_file: async () => ({cancelled: false, files: FILES})
            }};
            const originalFetch = window.fetch;
            window.fetch = async function(url, options) {
                if (String(url).startsWith('/api/jobs/') && (!options || !options.method))
                    await new Promise(resolve => setTimeout(resolve, 1200));
                return originalFetch.apply(this, arguments);
            };""".replace("FILES", files))
            page.goto(url)
            page.locator("#browse-multiple").click()
            page.get_by_text("Opening file 1 of 2").wait_for()
            page.wait_for_timeout(500)
            page.locator("#cancel-open").click()
            page.get_by_text("Cancelled. 1 of 2 files opened.").wait_for(timeout=15000)
            assert page.locator(".document-tab").count() == 1
            browser.close()
    finally:
        server.stop()
