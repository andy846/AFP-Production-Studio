"""Run the real PDF controller in a browser with explicitly released API promises.

No server, AFP input, native dialog, or PDF output is needed. The harness only
replaces the controller's injected services; form events and DOM are real.
"""
from pathlib import Path

import pytest


CONTROLLER = (Path(__file__).resolve().parents[2] /
              "frontend/static/pdf-export.js").read_text(encoding="utf-8")

HTML = """
<button data-tab="export">Export</button><button id="export-pdf">Export PDF</button>
<span id="pdf-source"></span><span id="pdf-task-tray"></span>
<form id="pdf-export-form">
  <select id="pdf-pages-mode"><option>all</option><option>current</option><option>range</option></select>
  <input id="pdf-range" value="1-3"><input id="pdf-path" value="C:/Output/A.pdf">
  <select id="pdf-dpi"><option>150</option><option selected>200</option><option>300</option></select>
  <button type="button" id="pdf-browse">Save as</button>
  <button type="submit" id="pdf-start">Start</button>
  <button type="button" id="pdf-cancel">Cancel</button>
</form>
<button id="pdf-accept-limitations" hidden>Accept</button>
<button id="pdf-open" hidden>Open</button><button id="pdf-reveal" hidden>Reveal</button>
<progress id="pdf-progress" max="1"></progress><p id="pdf-status"></p>
<div id="pdf-result"></div><ul id="pdf-warning-list"></ul>
"""

HARNESS = """() => {
  const h = window.h = {
    doc: {document_id: 'A', filename: 'A.afp', page_count: 10}, page: 2,
    native: false, calls: [], saves: [], states: {}, gates: {}, settled: [],
    setDocument(id, page) {
      this.doc = {document_id: id, filename: id + '.afp', page_count: 10};
      this.page = page;
      document.dispatchEvent(new Event('documents-changed'));
    },
    defer(key) {
      return new Promise(resolve => { this.gates[key] = resolve; }).then(value => {
        this.settled.push(key);
        return value;
      });
    },
    release(key, value) { this.gates[key](value); }
  };
  window.initPdfExport({
    getDocument: () => h.doc, getPage: () => h.page,
    nativeApi: () => h.native ? {} : null,
    whenNativeReady() {}, locate() {}, confirmOverwrite: async () => true,
    pickSavePath: async (name, type) => {
      h.saves.push({name, type});
      return h.defer('save');
    },
    api: async (path, options) => {
      h.calls.push({path, body: options?.body ? structuredClone(options.body) : null});
      if (path.endsWith('/pdf-export')) {
        const id = path.split('/')[3];
        return h.defer('create:' + id);
      }
      if (path.endsWith('/cancel')) return h.defer('cancel:' + path.split('/')[3]);
      const id = path.split('/')[3];
      return {id, state: h.states[id] || 'running', progress: 0.25, message: 'Rendering'};
    }
  });
}"""


@pytest.fixture(scope="module")
def controller_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel="msedge")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def controller_page(controller_browser):
    page = controller_browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(HTML)
    page.add_script_tag(content=CONTROLLER)
    page.evaluate(HARNESS)
    try:
        yield page
    finally:
        page.close()
    assert not errors, errors


def release_create(page, document_id):
    page.wait_for_function("id => Boolean(h.gates['create:' + id])", arg=document_id)
    page.evaluate("id => h.release('create:' + id, {job: {id: id + '-job'}})", document_id)


def test_cancel_during_pending_post_is_sent_when_job_id_arrives(controller_page):
    page = controller_page
    page.locator("#pdf-start").click()
    page.wait_for_function("Boolean(h.gates['create:A'])")
    page.locator("#pdf-cancel").click()
    release_create(page, "A")
    page.wait_for_function("h.calls.some(call => call.path === '/api/jobs/A-job/cancel')", timeout=2000)
    page.evaluate("h.states['A-job'] = 'cancelled'; h.release('cancel:A-job', {ok: true})")
    page.wait_for_function("document.getElementById('pdf-start').disabled === false")


def test_old_cancel_reply_does_not_disable_or_relabel_new_task(controller_page):
    page = controller_page
    page.locator("#pdf-start").click()
    release_create(page, "A")
    page.wait_for_function("h.calls.some(call => call.path === '/api/jobs/A-job')")
    page.locator("#pdf-cancel").click()
    page.wait_for_function("Boolean(h.gates['cancel:A-job'])")
    # Polling can observe cancellation before the cancellation POST replies.
    page.evaluate("h.states['A-job'] = 'cancelled'")
    page.wait_for_function("document.getElementById('pdf-start').disabled === false")
    page.evaluate("h.setDocument('B', 7)")
    page.locator("#pdf-start").click()
    release_create(page, "B")
    page.wait_for_function("document.getElementById('pdf-status').textContent.includes('B.afp — running')")
    status = page.locator("#pdf-status").inner_text()
    # Drain the promise chain, without a timing-dependent sleep.
    page.evaluate("""async () => {
      h.release('cancel:A-job', {ok: true});
      for (let i = 0; i < 8; i++) await Promise.resolve();
    }""")
    assert page.evaluate("h.settled.includes('cancel:A-job')")
    assert page.locator("#pdf-cancel").is_enabled(), "A's late reply disabled B's cancellation"
    assert page.locator("#pdf-status").inner_text() == status, "A's late reply relabelled B"
    page.evaluate("h.states['B-job'] = 'cancelled'")


def test_save_as_freezes_document_page_and_settings_before_await(controller_page):
    page = controller_page
    page.evaluate("h.native = true")
    page.locator("#pdf-path").fill("")
    page.locator("#pdf-pages-mode").select_option("current")
    page.locator("#pdf-start").click()
    page.wait_for_function("Boolean(h.gates.save)")
    assert page.evaluate("h.saves") == [{"name": "A.pdf", "type": "pdf"}]
    # A background document activation may finish while the native picker waits.
    page.evaluate("""() => {
      h.setDocument('B', 7);
      document.getElementById('pdf-pages-mode').value = 'range';
      document.getElementById('pdf-range').value = '4-6';
      document.getElementById('pdf-dpi').value = '300';
      h.release('save', 'C:/Output/A.pdf');
    }""")
    page.wait_for_function("h.calls.some(call => call.path.endsWith('/pdf-export'))")
    submission = page.evaluate("h.calls.find(call => call.path.endsWith('/pdf-export'))")
    assert submission == {
        "path": "/api/documents/A/pdf-export",
        "body": {"path": "C:/Output/A.pdf", "selection": "current", "page_range": "1-3",
                 "current_page": 2, "dpi": 200, "allow_incomplete": False, "overwrite": False},
    }
