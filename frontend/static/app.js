/* AFP Production Studio — local UI.
 *
 * No framework, no CDN: the application must work with the network
 * unplugged, so every asset is served from this machine.
 */
(function () {
  "use strict";

  // The CSRF token rides in a meta tag rather than an inline script, so
  // the strict Content-Security-Policy (no 'unsafe-inline') still lets
  // the page read it.
  const CSRF_META = document.querySelector('meta[name="csrf-token"]');
  const CSRF = CSRF_META ? CSRF_META.content : "";

  const state = {
    document: null,
    preflight: null,
    report: null,
    page: 1,
    zoom: 1,
    fitMode: "page",
    tab: "viewer",
    tree: null,
    jobTimer: null,
    openJobId: null,
    pathGrant: null,
    formDefGrant: null
  };

  const $ = (id) => document.getElementById(id);
  const documents = new Map();
  let recentRestore = null;
  let recentSaveTimer = null;
  let recentSavePending = Promise.resolve();
  let multiOpenRunning = false;
  let multiOpenCancelled = false;

  function renderRecent(data) {
    $("remember-recent").checked = data.remember;
    const list = $("recent-list");
    list.textContent = "";
    if (!data.recent.length) list.appendChild(el("p", "empty-note", "No recent files."));
    data.recent.forEach((item) => {
      const button = el("button", "recent-file", item.path + "  ·  Page " + item.page);
      button.type = "button";
      button.addEventListener("click", async () => {
        if (multiOpenRunning || state.openJobId || documents.size >= 8) {
          setStatus("Wait for the current open or close a document first.");
          return;
        }
        $("open-error").textContent = "";
        $("open-button").disabled = true;
        $("opener-progress").hidden = false;
        $("opener-status").textContent = "Loading — preparing the document.";
        try {
          const result = await api("/api/workspace/recent/open", {method: "POST", body: {id: item.id}});
          recentRestore = {page: item.page, zoom: item.zoom};
          state.openJobId = result.job.id;
          pollJob(result.job.id);
        } catch (err) {
          $("open-error").textContent = err.message;
          finishOpen();
        }
      });
      list.appendChild(button);
    });
  }

  function saveRecent(documentId, page, zoom) {
    recentSavePending = recentSavePending.then(async () => {
      try {
        const data = await api("/api/workspace/recent", {method: "POST",
          body: {document_id: documentId, page, zoom}});
        renderRecent(data);
      } catch (err) {
        if (err.code !== "source_changed") setStatus("Recent files: " + err.message);
      }
    });
    return recentSavePending;
  }

  function scheduleRecent() {
    if (!state.document) return;
    clearTimeout(recentSaveTimer);
    const id = state.document.document_id, page = state.page, zoom = state.zoom;
    recentSaveTimer = setTimeout(() => saveRecent(id, page, zoom), 500);
  }

  async function initWorkspacePreferences() {
    try {
      const data = await api("/api/workspace");
      renderRecent(data);
      window.dispatchEvent(new CustomEvent("workspace-preferences", {detail: data}));
    } catch (err) { setStatus("Workspace settings: " + err.message); }
    $("remember-recent").addEventListener("change", async (event) => {
      const remember = event.target.checked;
      clearTimeout(recentSaveTimer);
      await recentSavePending;
      try { renderRecent(await api("/api/workspace", {method: "POST", body: {remember}})); }
      catch (err) { setStatus(err.message); }
    });
    $("recent-clear").addEventListener("click", async () => {
      clearTimeout(recentSaveTimer);
      await recentSavePending;
      try { renderRecent(await api("/api/workspace", {method: "POST", body: {clear_recent: true}})); }
      catch (err) { setStatus(err.message); }
    });
  }

  function saveActiveDocument() {
    if (!state.document) return;
    const item = documents.get(state.document.document_id);
    if (item) Object.assign(item, {
      page: state.page, zoom: state.zoom, fitMode: state.fitMode,
      tab: state.tab, preflight: state.preflight, report: state.report,
      search: $("search-input").value
    });
    clearTimeout(recentSaveTimer);
    saveRecent(state.document.document_id, state.page, state.zoom);
  }

  function renderDocumentTabs() {
    const bar = $("document-tabs");
    bar.textContent = "";
    documents.forEach((item, id) => {
      const button = el("button", "document-tab", item.summary.filename);
      button.type = "button";
      button.title = item.summary.path || item.summary.filename;
      button.setAttribute("aria-current", String(state.document && state.document.document_id === id));
      button.addEventListener("click", () => activateDocument(id));
      bar.appendChild(button);
    });
    bar.hidden = documents.size === 0;
  }

  async function activateDocument(id) {
    const item = documents.get(id);
    if (!item) return;
    saveActiveDocument();
    state.document = item.summary;
    state.page = item.page;
    state.zoom = item.zoom;
    state.fitMode = item.fitMode;
    state.preflight = item.preflight;
    state.report = item.report;
    state.tree = null;
    updateThumbnails(true);
    $("search-input").value = item.search || "";
    $("search-results").textContent = "";
    $("search-results").hidden = true;
    $("opener").hidden = true;
    $("doc-chip").hidden = false;
    $("doc-name").textContent = item.summary.filename;
    $("doc-status").textContent = item.summary.preflight_status;
    $("doc-status").dataset.status = item.summary.preflight_status;
    $("page-total").textContent = "/ " + item.summary.page_count;
    $("page-number").max = item.summary.page_count;
    renderDocumentTabs();
    document.querySelector('[data-tab="' + item.tab + '"]').click();
    await refreshAll();
    if (state.document && state.document.document_id === id) showPage(item.page);
  }

  async function closeActiveDocument() {
    if (!state.document) return;
    const id = state.document.document_id;
    clearTimeout(recentSaveTimer);
    await saveRecent(id, state.page, state.zoom);
    try { await api("/api/documents/" + id, { method: "DELETE" }); }
    catch (err) { setStatus(err.message); return; }
    documents.delete(id);
    state.document = null;
    state.preflight = null;
    state.report = null;
    const next = documents.keys().next().value;
    if (next) { await activateDocument(next); return; }
    renderDocumentTabs();
    $("opener").hidden = false;
    $("page-holder").textContent = "";
    $("tree").textContent = "";
    updateThumbnails(true);
    $("details").textContent = "";
    $("viewer-empty").hidden = true;
    $("doc-chip").hidden = true;
    setStatus("Document closed.");
  }

  // The desktop shell exposes native file dialogs through a small
  // bridge. In a browser tab there is no bridge, so the code falls
  // back to the plain path field. The bridge is only used to pick a
  // path; opening and exporting still go through the ordinary HTTP
  // API, where the path checks apply.
  function nativeApi() {
    if (window.pywebview && window.pywebview.api) return window.pywebview.api;
    return null;
  }

  // The desktop shell injects its bridge after the page has loaded, so
  // the Browse buttons may not be usable at the moment the page runs.
  // The bridge announces itself with the "pywebviewready" event; if
  // that has already fired, the bridge is checked directly.
  function whenNativeReady(callback) {
    if (nativeApi()) {
      callback();
      return;
    }
    window.addEventListener("pywebviewready", () => callback(), { once: true });
    // Some versions install the bridge without announcing it, so the
    // check is repeated for a short while.
    let attempts = 0;
    const poll = setInterval(() => {
      attempts += 1;
      if (nativeApi()) {
        clearInterval(poll);
        callback();
      } else if (attempts > 40) {
        clearInterval(poll);
      }
    }, 100);
  }

  async function pickFile(method) {
    const api = nativeApi();
    if (!api) return null;
    try {
      const result = await api[method]();
      if (!result || result.cancelled) return null;
      return result.path ? result : null;
    } catch (err) {
      setStatus("The file dialog could not be opened: " + err.message);
      return null;
    }
  }

  async function pickSavePath(defaultName, fileType) {
    const api = nativeApi();
    if (!api) return null;
    try {
      const result = await api.save_report_file(defaultName, fileType);
      if (!result || result.cancelled) return null;
      return result.path || null;
    } catch (err) {
      setStatus("The save dialog could not be opened: " + err.message);
      return null;
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setStatus(message) {
    $("status-message").textContent = message;
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.method && opts.method !== "GET") {
      opts.headers["X-CSRF-Token"] = CSRF;
    }
    const response = await fetch(path, opts);
    let payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      payload = null;
    }
    if (!response.ok) {
      const message =
        (payload && (payload.error || payload.message)) ||
        "Request failed (" + response.status + ").";
      const error = new Error(message);
      error.code = payload && payload.code;
      if (error.code === "source_changed" && state.document &&
          path.startsWith("/api/documents/" + state.document.document_id)) invalidateDocument(message);
      throw error;
    }
    return payload;
  }

  /* ------------------------------------------------------------------ */
  /* opener                                                              */
  /* ------------------------------------------------------------------ */

  function initOpener() {
    $("viewer-stage").prepend($("opener"));
    $("viewer-empty").hidden = true;
    // The Browse buttons only work where the desktop shell provides a
    // native dialog. In a browser tab they are hidden, because there
    // is nothing behind them.
    let startupHandled = false;
    whenNativeReady(async () => {
      $("browse-button").hidden = false;
      $("browse-multiple").hidden = false;
      $("browse-formdef-button").hidden = false;
      if (startupHandled) return;
      startupHandled = true;
      try {
        const path = await nativeApi().consume_startup_file();
        if (path) {
          $("path-input").value = path;
          $("open-form").requestSubmit();
        }
      } catch (err) {
        $("open-error").textContent = "Could not open startup file: " + err.message;
        setStatus($("open-error").textContent);
      }
    });

    $("browse-button").addEventListener("click", async () => {
      const selected = await pickFile("pick_afp_file");
      if (selected) {
        $("path-input").value = selected.path;
        state.pathGrant = selected.grant || null;
        $("open-error").textContent = "";
        $("open-form").requestSubmit();
      }
    });

    $("browse-multiple").addEventListener("click", async () => {
      if (multiOpenRunning || state.openJobId) return;
      if ($("formdef-input").value.trim()) {
        $("open-error").textContent = "Open files individually when using an external FormDef.";
        return;
      }
      try {
        const selected = await nativeApi().pick_afp_file(true);
        if (!selected || selected.cancelled) return;
        if (!Array.isArray(selected.files) || !selected.files.length ||
            selected.files.length > 8 - documents.size) {
          $("open-error").textContent = "Close documents or select fewer files (maximum 8 open).";
          return;
        }
        await openMultiple(selected.files);
      } catch (err) {
        $("open-error").textContent = "Could not select files: " + err.message;
      }
    });

    $("browse-formdef-button").addEventListener("click", async () => {
      const selected = await pickFile("pick_form_definition");
      if (selected) { $("formdef-input").value = selected.path; state.formDefGrant = selected.grant || null; }
    });

    $("path-input").addEventListener("input", () => { state.pathGrant = null; });
    $("formdef-input").addEventListener("input", () => { state.formDefGrant = null; });
    $("open-new").addEventListener("click", () => {
      document.querySelector('[data-tab="viewer"]').click();
      $("opener").hidden = false;
      $("viewer-empty").hidden = true;
      if (nativeApi()) $("browse-button").click();
      else $("path-input").focus();
    });
    $("recent-open").addEventListener("click", () => {
      document.querySelector('[data-tab="viewer"]').click();
      $("opener").hidden = false;
      $("viewer-empty").hidden = true;
      $("recent-list").querySelector("button")?.focus();
    });

    $("open-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (documents.size >= 8) {
        $("open-error").textContent = "Close a document before opening another (maximum 8).";
        return;
      }
      const path = $("path-input").value.trim();
      if (!path) {
        $("open-error").textContent = "Enter the path of an AFP file.";
        return;
      }
      $("open-error").textContent = "";
      $("open-button").disabled = true;
      $("opener-progress").hidden = false;
      $("opener-status").textContent = "Loading — preparing the document.";
      try {
        const body = {
          path: path,
          input_format: $("input-format").value,
          path_grant: state.pathGrant,
          profile: $("profile-select").value || null,
          use_java_parser: $("java-parser").checked
        };
        const formDef = $("formdef-input").value.trim();
        if (formDef) { body.form_defs = [formDef]; body.form_def_grants = [state.formDefGrant]; }
        state.pathGrant = null;
        state.formDefGrant = null;
        const result = await api("/api/documents/open", {
          method: "POST",
          body: body
        });
        state.openJobId = result.job.id;
        pollJob(result.job.id);
      } catch (err) {
        $("open-error").textContent = err.message;
        $("open-button").disabled = false;
        $("opener-progress").hidden = true;
      }
    });

    $("cancel-open").addEventListener("click", async () => {
      if (multiOpenRunning) { multiOpenCancelled = true; return; }
      if (!state.openJobId) return;
      try {
        await api("/api/jobs/" + state.openJobId + "/cancel", {
          method: "POST"
        });
      } catch (err) {
        setStatus(err.message);
      }
    });
  }

  async function openMultiple(files) {
    multiOpenRunning = true;
    multiOpenCancelled = false;
    $("open-error").textContent = "";
    $("open-button").disabled = true;
    $("open-new").disabled = true;
    $("recent-open").disabled = true;
    $("browse-button").disabled = true;
    $("browse-multiple").disabled = true;
    const optionControls = ["input-format", "profile-select", "java-parser", "formdef-input", "browse-formdef-button"];
    const previousDisabled = optionControls.map((id) => $(id).disabled);
    optionControls.forEach((id) => { $(id).disabled = true; });
    $("opener-progress").hidden = false;
    let completed = 0;
    const options = {input_format: $("input-format").value,
      profile: $("profile-select").value || null,
      use_java_parser: $("java-parser").checked};
    try {
      for (const item of files) {
        if (multiOpenCancelled) break;
        $("opener-status").textContent = "Opening file " + (completed + 1) + " of " + files.length;
        const result = await api("/api/documents/open", {method: "POST", body: {
          path: item.path, path_grant: item.grant || null,
          ...options
        }});
        state.openJobId = result.job.id;
        let job;
        let cancelSent = false;
        do {
          if (multiOpenCancelled && !cancelSent) {
            cancelSent = true;
            try { await api("/api/jobs/" + state.openJobId + "/cancel", {method: "POST"}); }
            catch (err) { if (err.code !== "not_cancellable") throw err; }
          }
          job = await api("/api/jobs/" + state.openJobId);
          $("opener-bar").style.width = Math.round(((completed + job.progress) / files.length) * 100) + "%";
          $("opener-status").textContent = "File " + (completed + 1) + " of " + files.length + " — " + job.state;
          if (!["completed", "failed", "cancelled"].includes(job.state))
            await new Promise((resolve) => setTimeout(resolve, 350));
        } while (!["completed", "failed", "cancelled"].includes(job.state));
        state.openJobId = null;
        if (job.state === "cancelled") { multiOpenCancelled = true; break; }
        if (job.state === "failed") throw new Error(job.error || job.message || "Open failed.");
        await afterOpen(job.result);
        completed += 1;
        $("opener").hidden = false;
      }
      setStatus((multiOpenCancelled ? "Cancelled. " : "") + completed + " of " + files.length + " files opened.");
    } catch (err) {
      $("open-error").textContent = "Opened " + completed + " of " + files.length + ". " + err.message;
      setStatus($("open-error").textContent);
    } finally {
      state.openJobId = null;
      multiOpenRunning = false;
      $("open-new").disabled = false;
      $("recent-open").disabled = false;
      $("browse-button").disabled = false;
      $("browse-multiple").disabled = false;
      optionControls.forEach((id, index) => { $(id).disabled = previousDisabled[index]; });
      finishOpen();
      if (!$("open-error").textContent && documents.size) $("opener").hidden = true;
    }
  }

  function invalidateDocument(message) {
    if (state.document) documents.delete(state.document.document_id);
    state.document = null;
    state.preflight = null;
    state.report = null;
    $("doc-chip").hidden = true;
    $("opener").hidden = false;
    $("page-holder").textContent = "";
    $("tree").textContent = "";
    updateThumbnails(true);
    $("details").textContent = "";
    $("structure-table").textContent = "";
    $("document-index-table").textContent = "";
    $("preflight-table").textContent = "";
    $("media-timeline").textContent = "";
    $("report-body").textContent = "";
    $("resources-table").textContent = "";
    $("open-error").textContent = message;
    renderDocumentTabs();
    document.querySelector('[data-tab="viewer"]').click();
    setStatus(message);
  }

  function pollJob(jobId) {
    if (state.jobTimer) clearTimeout(state.jobTimer);
    const tick = async () => {
      let job;
      try {
        job = await api("/api/jobs/" + jobId);
      } catch (err) {
        $("open-error").textContent = err.message;
        finishOpen();
        return;
      }
      $("opener-bar").style.width = Math.round(job.progress * 100) + "%";
      $("opener-status").textContent = job.state + " — " + (job.message || "");
      if (job.state === "completed") {
        finishOpen();
        await afterOpen(job.result);
        return;
      }
      if (job.state === "failed" || job.state === "cancelled") {
        recentRestore = null;
        $("open-error").textContent =
          job.error || job.message || "The job did not finish.";
        finishOpen();
        return;
      }
      state.jobTimer = setTimeout(tick, 350);
    };
    tick();
  }

  function finishOpen() {
    $("open-button").disabled = false;
    $("opener-progress").hidden = true;
    $("opener-bar").style.width = "0";
    state.openJobId = null;
  }

  async function afterOpen(summary) {
    saveActiveDocument();
    state.document = null;
    const restore = recentRestore;
    recentRestore = null;
    documents.set(summary.document_id, {summary, page: restore ? restore.page : 1, zoom: restore ? restore.zoom : 1,
      fitMode: restore ? "manual" : "page", tab: "viewer", preflight: null, report: null});
    documentIndexOffset = 0;
    await activateDocument(summary.document_id);
    scheduleRecent();
    setStatus("Opened " + summary.filename + " — " + summary.page_count + " page(s).");
  }

  async function refreshAll() {
    await Promise.all([loadPreflight(), loadTree(), loadReport()]);
    renderTab(state.tab);
  }

  /* ------------------------------------------------------------------ */
  /* tabs                                                                */
  /* ------------------------------------------------------------------ */

  function initTabs() {
    [["toggle-tree", "tree-collapsed"], ["toggle-details", "details-collapsed"]].forEach(([id, cls]) => {
      $(id).addEventListener("click", () => {
        const collapsed = $("workspace").classList.toggle(cls);
        $(id).setAttribute("aria-expanded", String(!collapsed));
        if (id === "toggle-tree") updateThumbnails(true);
      });
    });
    document.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => {
        state.tab = button.dataset.tab;
        document.querySelectorAll(".tab").forEach((other) => {
          other.classList.toggle("is-active", other === button);
        });
        document.querySelectorAll("[data-panel]").forEach((panel) => {
          panel.hidden = panel.dataset.panel !== state.tab;
        });
        renderTab(state.tab);
      });
    });
  }

  function renderTab(tab) {
    if (tab === "structure") loadStructurePage(state.page || 1);
    if (tab === "document-index") loadDocumentIndex();
    if (tab === "preflight") renderPreflight();
    if (tab === "resources") renderResources();
    if (tab === "report") renderReport();
    if (tab === "settings") renderSettings();
  }

  let documentIndexOffset = 0;
  let documentIndexRequest = 0;
  let documentIndexTotal = 0;

  async function loadDocumentIndex() {
    const table = $("document-index-table");
    table.textContent = "";
    if (!state.document) { table.appendChild(el("p", "empty-note", "Open an AFP document first.")); return; }
    const requestId = ++documentIndexRequest;
    const documentId = state.document.document_id;
    const kind = $("index-kind").value;
    const codec = $("index-codepage").value;
    const query = $("index-query").value.trim();
    if (kind === "text_runs" && !codec) {
      $("document-index-note").textContent = "Select EBCDIC 037 or 500 as the preview fallback before extracting positioned text.";
      return;
    }
    $("document-index-note").textContent = "Loading index...";
    try {
      const result = await api("/api/documents/" + documentId + (kind === "text_runs" ? "/text-runs" : "/structure-index"), {
        method: "POST", body: kind === "text_runs" ? { page: state.page - 1, codepage: codec, q: query, limit: 100, offset: documentIndexOffset } :
          { kind: kind, preview_codec: codec || null, q: kind === "tags" ? query : "", limit: 100, offset: documentIndexOffset }
      });
      if (requestId !== documentIndexRequest || !state.document || state.document.document_id !== documentId) return;
      documentIndexTotal = result.total;
      const basis = kind === "text_runs" ? "Page " + state.page + " positioned preview text; coordinates use page logical units. Offset identifies the PTX field. Fallback " + codec + " is operator-selected. " :
        kind === "mailpieces" ? "Candidate boundaries require an operator-attested customer rule. Actual mailpieces are unknown. " :
        kind === "tags" ? (codec ? "Operator-selected " + codec + " preview is unverified. " : "Raw TLE bytes; text not decoded. ") : "Parsed document and group structure. ";
      $("document-index-note").textContent = basis +
        (result.complete ? "Scanned fields without index warnings." : "Incomplete or limited index.") +
        " " + (result.warnings || 0) + " warning(s); " + result.total + " items." +
        ((result.warning_details || []).filter((item) => typeof item === "string").map((item) => " " + item).join(""));
      const list = el("table");
      const head = el("thead");
      const header = el("tr");
      ["Offset", "Page", "Details"].forEach((name) => header.appendChild(el("th", null, name)));
      head.appendChild(header); list.appendChild(head);
      const body = el("tbody");
      result.items.forEach((item) => {
        const row = el("tr");
        const offsetCell = el("td", "mono");
        const jump = el("button", "finding-link", item.offset == null ? "Unknown" : item.offset);
        jump.type = "button";
        jump.disabled = item.offset == null;
        jump.setAttribute("aria-label", "Locate structured field at offset " + item.offset);
        jump.addEventListener("click", async () => {
          const page = item.page || item.first_page;
          if (page) await showPage(page);
          document.querySelector('[data-tab="structure"]').click();
          await loadStructurePage(page || state.page);
          const field = Array.from($("structure-table").querySelectorAll("tr[data-offset]"))
            .find((node) => node.dataset.offset === String(item.offset));
          if (field) { field.classList.add("is-selected"); field.scrollIntoView({ block: "nearest" }); field.focus(); }
          else setStatus("Structured field offset " + item.offset + " is outside the current page's displayed fields.");
        });
        offsetCell.appendChild(jump); row.appendChild(offsetCell);
        row.appendChild(el("td", "num", item.page || item.first_page || "—"));
        const previews = (item.triplets || []).filter((part) => part.preview !== undefined).map((part) => part.preview);
        const detail = (previews.length ? "Unverified preview: " + previews.join(" | ") + " — " : "") + JSON.stringify(item);
        row.appendChild(el("td", "mono", detail.length > 512 ? detail.slice(0, 512) + "… (truncated)" : detail));
        body.appendChild(row);
      });
      list.appendChild(body); table.appendChild(list);
    } catch (err) { $("document-index-note").textContent = err.message; }
  }

  /* ------------------------------------------------------------------ */
  /* tree                                                                */
  /* ------------------------------------------------------------------ */

  async function loadTree() {
    const tree = $("tree");
    tree.textContent = "";
    const summary = state.document;

    tree.appendChild(el("div", "tree-group", "Document"));
    const docItem = el("div", "tree-item");
    docItem.appendChild(el("span", "label", summary.filename));
    docItem.appendChild(el("span", "meta", summary.page_count + "p"));
    tree.appendChild(docItem);

    if (summary.page_groups && summary.page_groups.length) {
      tree.appendChild(el("div", "tree-group", "Page groups"));
      summary.page_groups.forEach((name) => {
        const item = el("div", "tree-item");
        item.appendChild(el("span", "label", name));
        tree.appendChild(item);
      });
    }

    if (summary.medium_maps && summary.medium_maps.length) {
      tree.appendChild(el("div", "tree-group", "Medium maps"));
      summary.medium_maps.forEach((name) => {
        const item = el("div", "tree-item");
        item.appendChild(el("span", "label", name));
        tree.appendChild(item);
      });
    }

    tree.appendChild(el("div", "tree-group", "Pages"));
    const pages = summary.page_count || 0;
    const limit = Math.min(pages, 500);
    for (let page = 1; page <= limit; page += 1) {
      const item = el("div", "tree-item");
      item.dataset.page = String(page);
      const media = state.preflight
        ? state.preflight.pages[page - 1]
        : null;
      item.appendChild(el("span", "label", "Page " + page));
      if (media && media.medium_map) {
        item.appendChild(el("span", "meta", media.medium_map));
      }
      item.addEventListener("click", () => showPage(page));
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.click(); } });
      tree.appendChild(item);
    }
    if (pages > limit) {
      tree.appendChild(
        el("div", "tree-group", "+ " + (pages - limit) + " more pages")
      );
    }
    state.tree = tree;
  }

  /* ------------------------------------------------------------------ */
  /* viewer                                                              */
  /* ------------------------------------------------------------------ */

  function initViewer() {
    $("toggle-thumbnails").addEventListener("click", () => {
      thumbnailsEnabled = !thumbnailsEnabled;
      updateThumbnails(true);
    });
    $("thumbnail-prev").addEventListener("click", () => updateThumbnails(true, thumbnailStart - thumbnailBatch));
    $("thumbnail-next").addEventListener("click", () => updateThumbnails(true, thumbnailStart + thumbnailBatch));
    $("page-prev").addEventListener("click", () => showPage((state.page || 1) - 1));
    $("page-next").addEventListener("click", () => showPage((state.page || 1) + 1));
    $("page-first").addEventListener("click", () => showPage(1));
    $("page-last").addEventListener("click", () => {
      showPage(state.document ? state.document.page_count : 1);
    });
    $("page-number").addEventListener("change", (event) => {
      showPage(parseInt(event.target.value, 10) || 1);
    });
    $("zoom-in").addEventListener("click", () => setZoom(state.zoom * 1.25));
    $("zoom-out").addEventListener("click", () => setZoom(state.zoom / 1.25));
    $("zoom-fit").addEventListener("click", fitPage);
    $("zoom-width").addEventListener("click", () => setFitMode("width"));
    $("zoom-actual").addEventListener("click", () => setZoom(1));
    initViewerPan();
    $("viewer-stage").addEventListener("wheel", (event) => {
      if (!event.ctrlKey || !state.document) return;
      event.preventDefault();
      setZoom(state.zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15));
    }, {passive: false});
    if (typeof ResizeObserver !== "undefined") {
      let frame = null;
      new ResizeObserver(() => {
        if (frame !== null) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          frame = null;
          if (state.fitMode !== "manual") applyFit();
        });
      }).observe($("viewer-stage"));
    }
  }

  function initViewerPan() {
    const stage = $("viewer-stage");
    let armed = false;
    let pan = null;
    const stop = () => {
      const pointerId = pan?.id;
      pan = null;
      stage.classList.remove("is-panning");
      if (pointerId !== undefined && stage.hasPointerCapture(pointerId))
        stage.releasePointerCapture(pointerId);
    };
    const reset = () => {
      armed = false;
      stage.classList.remove("can-pan");
      stop();
    };
    stage.addEventListener("keydown", (event) => {
      if (event.target !== stage || event.code !== "Space" || event.isComposing ||
          event.altKey || event.ctrlKey || event.metaKey || !$("page-holder").querySelector("svg") ||
          !$("opener").hidden) return;
      event.preventDefault();
      armed = true;
      stage.classList.add("can-pan");
    });
    window.addEventListener("keyup", (event) => { if (event.code === "Space") reset(); });
    window.addEventListener("blur", reset);
    stage.addEventListener("blur", reset);
    stage.addEventListener("pointerdown", (event) => {
      if (!armed || event.button !== 0 || !$("opener").hidden) return;
      event.preventDefault();
      pan = {id: event.pointerId, x: event.clientX, y: event.clientY,
        left: stage.scrollLeft, top: stage.scrollTop};
      stage.setPointerCapture(event.pointerId);
      stage.classList.add("is-panning");
    });
    stage.addEventListener("pointermove", (event) => {
      if (!pan || event.pointerId !== pan.id) return;
      stage.scrollLeft = pan.left + pan.x - event.clientX;
      stage.scrollTop = pan.top + pan.y - event.clientY;
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      stage.addEventListener(type, (event) => { if (pan?.id === event.pointerId) stop(); });
    }
  }

  function initPanelResizing() {
    const workspace = $("workspace");
    [["tree-resize", "--tree-width", 160, 240, 1],
     ["details-resize", "--details-width", 200, 300, -1]].forEach(([id, prop, min, initial, direction]) => {
      const handle = $(id);
      let width = initial;
      const update = (value) => {
        width = Math.max(min, Math.min(480, value));
        workspace.style.setProperty(prop, width + "px");
        handle.setAttribute("aria-valuenow", String(width));
      };
      window.addEventListener("workspace-preferences", (event) => {
        update(event.detail[prop === "--tree-width" ? "tree_width" : "details_width"]);
      });
      let persistTimer;
      const persist = () => {
        clearTimeout(persistTimer);
        persistTimer = setTimeout(() => {
          const key = prop === "--tree-width" ? "tree_width" : "details_width";
          api("/api/workspace", {method: "POST", body: {[key]: width}}).catch((err) => setStatus(err.message));
        }, 300);
      };
      handle.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        update(width + (event.key === "ArrowRight" ? 16 : -16) * direction);
        persist();
      });
      handle.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        const start = event.clientX, original = width;
        handle.setPointerCapture(event.pointerId);
        const move = (e) => update(original + (e.clientX - start) * direction);
        const end = () => {
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", end);
          handle.removeEventListener("pointercancel", end);
          persist();
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", end);
        handle.addEventListener("pointercancel", end);
      });
    });
  }

  function setZoom(value, keepMode = false) {
    if (!keepMode) state.fitMode = "manual";
    $("zoom-fit").setAttribute("aria-pressed", String(state.fitMode === "page"));
    $("zoom-width").setAttribute("aria-pressed", String(state.fitMode === "width"));
    state.zoom = Math.min(8, Math.max(0.1, value));
    $("zoom-readout").textContent = Math.round(state.zoom * 100) + "%";
    const holder = $("page-holder");
    const svg = holder.querySelector("svg");
    if (svg) {
      // Scaling the width rather than using a CSS transform keeps the
      // stage scrollable, so a zoomed-in page can still be panned.
      const size = svgSize(svg);
      if (size) {
        svg.style.setProperty(
          "--page-width",
          Math.round(size.width * state.zoom) + "px"
        );
      }
    }
    scheduleRecent();
  }

  function fitPage() { setFitMode("page"); }

  function setFitMode(mode) {
    state.fitMode = mode;
    applyFit();
  }

  function applyFit() {
    const stage = $("viewer-stage");
    const svg = $("page-holder").querySelector("svg");
    if (!svg) return;
    const size = svgSize(svg);
    if (!size) {
      setZoom(1);
      return;
    }
    const available = stage.clientWidth - 60;
    const availableHeight = stage.clientHeight - 60;
    const widthScale = available / size.width;
    const scale = state.fitMode === "width" ? widthScale : Math.min(
      widthScale, availableHeight > 0 ? availableHeight / size.height : Infinity
    );
    setZoom(Math.min(8, Math.max(0.1, scale)), true);
  }

  /* The page SVG carries a viewBox but no width or height, so the
   * stylesheet sizes it from a custom property. Both the zoom and the
   * "Fit" button work off the viewBox, which is the page's real size
   * in units. */
  function svgSize(svg) {
    const viewBox = svg.getAttribute("viewBox");
    if (!viewBox) return null;
    const parts = viewBox.split(/\s+/).map(Number);
    const width = parts[2];
    const height = parts[3];
    if (!width || !height) return null;
    return { width: width, height: height };
  }

  const thumbnailBatch = 12;
  let thumbnailsEnabled = false;
  let thumbnailStart = 1;
  let thumbnailKey = "";
  let thumbnailObserver = null;

  function updateThumbnails(reset = false, requestedStart = null) {
    const documentId = state.document?.document_id;
    const total = state.document?.page_count || 0;
    const enabled = thumbnailsEnabled && total > 0;
    const visible = enabled && !$("workspace").classList.contains("tree-collapsed");
    $("toggle-thumbnails").disabled = !total;
    $("toggle-thumbnails").setAttribute("aria-pressed", String(enabled));
    $("thumbnail-panel").hidden = !enabled;
    $("tree").hidden = enabled;
    const list = $("page-thumbnails");
    const activeStart = Math.floor((Math.max(1, state.page) - 1) / thumbnailBatch) * thumbnailBatch + 1;
    const start = Math.max(1, Math.min(requestedStart ?? activeStart, Math.max(1, Math.ceil(total / thumbnailBatch) * thumbnailBatch - thumbnailBatch + 1)));
    const key = visible ? documentId + ":" + start : "";
    if (reset || key !== thumbnailKey) {
      thumbnailObserver?.disconnect();
      thumbnailObserver = null;
      list.replaceChildren();
      thumbnailKey = key;
      if (!visible) return;
      thumbnailStart = start;
      const end = Math.min(total, start + thumbnailBatch - 1);
      $("thumbnail-range").textContent = start + "–" + end + " / " + total;
      $("thumbnail-prev").disabled = start === 1;
      $("thumbnail-next").disabled = end === total;
      const loadPreview = (holder) => {
        const img = el("img");
        img.alt = "";
        img.decoding = "async";
        img.addEventListener("error", () => { holder.textContent = "Preview unavailable"; }, {once: true});
        holder.replaceChildren(img);
        img.src = "/api/documents/" + documentId + "/page/" + (Number(holder.dataset.page) - 1) + ".svg";
      };
      if (typeof IntersectionObserver !== "undefined") {
        thumbnailObserver = new IntersectionObserver((entries, observer) => {
          if (thumbnailObserver !== observer) return;
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            observer.unobserve(entry.target);
            loadPreview(entry.target);
          }
        }, {root: list});
      }
      for (let page = start; page <= end; page += 1) {
        const button = el("button", "page-thumbnail");
        button.type = "button";
        button.dataset.page = String(page);
        button.setAttribute("aria-label", "Show page " + page);
        const holder = el("span", "thumbnail-preview", "Page preview");
        holder.setAttribute("aria-hidden", "true");
        holder.dataset.page = String(page);
        button.append(holder, el("span", null, "Page " + page));
        button.addEventListener("click", () => {
          if (state.tab !== "viewer") document.querySelector('[data-tab="viewer"]').click();
          showPage(page);
        });
        list.appendChild(button);
        if (thumbnailObserver) thumbnailObserver.observe(holder);
        else loadPreview(holder);
      }
      list.scrollTop = 0;
    }
    list.querySelectorAll("button").forEach((button) => {
      if (Number(button.dataset.page) === state.page) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (visible && requestedStart === null) {
      const current = list.querySelector('[aria-current="page"]');
      if (current) {
        const row = current.getBoundingClientRect();
        const bounds = list.getBoundingClientRect();
        if (row.top < bounds.top) list.scrollTop += row.top - bounds.top;
        else if (row.bottom > bounds.bottom) list.scrollTop += row.bottom - bounds.bottom;
      }
    }
  }

  let pageRequest = 0;
  async function showPage(page) {
    if (!state.document) return;
    if (!state.document.page_count) {
      $("viewer-empty").hidden = false;
      $("viewer-empty").textContent = "No pages were found in this document.";
      return;
    }
    const requestId = ++pageRequest;
    const documentId = state.document.document_id;
    const total = state.document.page_count || 1;
    const clamped = Math.min(Math.max(1, page), total);
    state.page = clamped;
    updateThumbnails();
    scheduleRecent();
    $("page-number").value = String(clamped);
    $("viewer-empty").hidden = true;
    const holder = $("page-holder");
    holder.textContent = "";
    $("viewer-empty").hidden = false;
    $("viewer-empty").textContent = "Loading page " + clamped + "...";
    const url =
      "/api/documents/" +
      state.document.document_id +
      "/page/" +
      (clamped - 1) +
      ".svg";
    try {
      const response = await fetch(url);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        if (data.code === "source_changed" && requestId === pageRequest &&
            state.document && state.document.document_id === documentId) invalidateDocument(data.error);
        throw new Error(data.error || "The page could not be rendered.");
      }
      const svg = await response.text();
      if (requestId !== pageRequest || !state.document || state.document.document_id !== documentId) return;
      holder.innerHTML = svg;
      $("viewer-empty").hidden = true;
      // The first page is fitted to the stage; after that the operator's
      // own zoom is kept.
      if (state.fitMode !== "manual") applyFit();
      else setZoom(state.zoom, true);
      highlightTreePage(clamped);
      renderDetails(clamped);
      if (state.tab === "structure") loadStructurePage(clamped);
    } catch (err) {
      if (requestId !== pageRequest || !state.document || state.document.document_id !== documentId) return;
      holder.textContent = "";
      $("viewer-empty").hidden = false;
      $("viewer-empty").textContent = err.message;
    }
  }

  function highlightTreePage(page) {
    if (!state.tree) return;
    state.tree.querySelectorAll(".tree-item[data-page]").forEach((item) => {
      item.classList.toggle(
        "is-active",
        parseInt(item.dataset.page, 10) === page
      );
    });
  }

  /* ------------------------------------------------------------------ */
  /* structure                                                           */
  /* ------------------------------------------------------------------ */

  async function loadStructurePage(page) {
    if (!state.document) return;
    const documentId = state.document.document_id;
    const url =
      "/api/documents/" +
      state.document.document_id +
      "/fields?page=" +
      (page - 1);
    try {
      const payload = await api(url);
      if (!state.document || state.document.document_id !== documentId || state.page !== page) return;
      renderStructure(payload.fields, payload.total);
    } catch (err) {
      setStatus(err.message);
    }
  }

  function renderStructure(fields, total) {
    const wrap = $("structure-table");
    wrap.textContent = "";
    $("structure-note").textContent = fields
      ? fields.length + " of " + (total || fields.length) + " fields"
      : "";
    if (!fields || !fields.length) {
      wrap.appendChild(el("p", "empty-note", "No structured fields."));
      return;
    }
    const table = el("table");
    const head = el("thead");
    const headRow = el("tr");
    ["#", "Offset", "Length", "Structured field", "Object", "Page"].forEach(
      (title) => headRow.appendChild(el("th", null, title))
    );
    head.appendChild(headRow);
    table.appendChild(head);
    const body = el("tbody");
    fields.forEach((row) => {
      const tr = el("tr", "is-clickable");
      tr.dataset.offset = String(row.offset);
      tr.appendChild(el("td", "num", row.index));
      tr.appendChild(el("td", "mono", row.offset));
      tr.appendChild(el("td", "num", row.length));
      tr.appendChild(el("td", "mono", row.name));
      tr.appendChild(el("td", "mono", row.token || ""));
      tr.appendChild(
        el("td", "num", row.page === null ? "-" : row.page + 1)
      );
      tr.addEventListener("click", () => {
        if (row.page !== null && row.page !== undefined) {
          wrap.querySelectorAll("tr.is-selected").forEach((selected) => selected.classList.remove("is-selected"));
          tr.classList.add("is-selected");
          showPage(row.page + 1);
          setStatus("Structured field " + row.name + " at byte offset " + row.offset + ".");
        }
      });
      tr.tabIndex = 0;
      tr.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); tr.click(); } });
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
  }

  /* ------------------------------------------------------------------ */
  /* preflight                                                           */
  /* ------------------------------------------------------------------ */

  async function loadPreflight() {
    if (!state.document) return;
    const documentId = state.document.document_id;
    const url = "/api/documents/" + documentId + "/preflight";
    try {
      const result = await api(url);
      if (state.document && state.document.document_id === documentId) state.preflight = result;
    } catch (err) {
      setStatus(err.message);
    }
  }

  function renderPreflight() {
    const wrap = $("preflight-table");
    wrap.textContent = "";
    const timeline = $("media-timeline");
    timeline.textContent = "";
    if (!state.preflight) {
      $("preflight-note").textContent = "";
      return;
    }
    const preflight = state.preflight;
    $("preflight-note").textContent =
      preflight.filename + " — " + preflight.status;
    const comparison = $("preflight-global-findings");
    comparison.textContent = "";
    const comparisonStatus = state.document.comparison_status || "not_requested";
    comparison.appendChild(el("p", "empty-note", "Parser comparison: " + comparisonStatus +
      (comparisonStatus === "partial" ? " — page counts, page offsets, map names and IMM names/offsets only." : "")));
    preflight.findings.filter((finding) => ["PARSER_DISAGREEMENT", "PARSER_FAILURE", "INCOMPLETE_PARSE"].includes(finding.code)).forEach((finding) => {
      const button = el("button", "finding-link", finding.message + (finding.offset !== null && finding.offset !== undefined ? " (offset " + finding.offset + ")" : ""));
      button.type = "button";
      button.addEventListener("click", () => {
        if (finding.page) showPage(finding.page);
        setStatus(finding.message + (finding.offset !== null && finding.offset !== undefined ? " Offset: " + finding.offset : ""));
      });
      comparison.appendChild(button);
    });

    preflight.pages.forEach((page) => {
      const run = el("button", "timeline-run", page.medium_map || "—");
      run.type = "button";
      run.dataset.status = page.status;
      run.title =
        "Page " + page.page_number + " — " + (page.medium_map || "no map");
      run.addEventListener("click", () => showPage(page.page_number));
      timeline.appendChild(run);
    });

    const table = el("table");
    const head = el("thead");
    const headRow = el("tr");
    [
      "Page",
      "Offset",
      "Medium map",
      "Requested",
      "Resolved stock",
      "Tray",
      "Duplex",
      "Status",
      "Findings"
    ].forEach((title) => headRow.appendChild(el("th", null, title)));
    head.appendChild(headRow);
    table.appendChild(head);
    const body = el("tbody");
    preflight.pages.forEach((page) => {
      const tr = el("tr", "is-clickable");
      tr.appendChild(el("td", "num", page.page_number));
      tr.appendChild(el("td", "mono", page.offset === null ? "-" : page.offset));
      tr.appendChild(el("td", "mono", page.medium_map || "—"));
      tr.appendChild(
        el(
          "td",
          "finding-cell",
          describeRequested(page.requested)
        )
      );
      tr.appendChild(
        el("td", "mono", page.resolved.stock_name || "unresolved")
      );
      tr.appendChild(el("td", "mono", page.resolved.tray || "—"));
      tr.appendChild(el("td", "mono", page.requested.duplex || "—"));
      const statusCell = el("td");
      const pill = el("span", "status-pill", page.status);
      pill.dataset.status = page.status;
      statusCell.appendChild(pill);
      tr.appendChild(statusCell);

      const findings = el("td", "finding-cell");
      if (page.findings.length) {
        const list = el("ul", "finding-list");
        page.findings.forEach((finding) => {
          const item = el("li");
          const link = el("button", "finding-link", finding.code + ": " + finding.message);
          link.type = "button";
          link.addEventListener("click", async (event) => {
            event.stopPropagation();
            const selectedPage = finding.page || page.page_number;
            await showPage(selectedPage);
            if (finding.offset !== null && finding.offset !== undefined) {
              document.querySelector('[data-tab="structure"]').click();
              await loadStructurePage(selectedPage);
              const field = Array.from($("structure-table").querySelectorAll("tr[data-offset]"))
                .find((row) => row.dataset.offset === String(finding.offset));
              if (field) { field.classList.add("is-selected"); field.scrollIntoView({ block: "nearest" }); field.focus(); }
              else setStatus("Field at offset " + finding.offset + " is outside the current page's displayed fields.");
            }
          });
          item.appendChild(link);
          list.appendChild(item);
        });
        findings.appendChild(list);
      } else {
        findings.textContent = "—";
      }
      tr.appendChild(findings);
      tr.addEventListener("click", () => showPage(page.page_number));
      tr.tabIndex = 0;
      tr.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); tr.click(); } });
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
  }

  function describeRequested(requested) {
    const bits = [];
    if (requested.media_type) bits.push(requested.media_type);
    if (requested.media_destination) bits.push(requested.media_destination);
    if (requested.width_mm && requested.height_mm) {
      bits.push(
        Math.round(requested.width_mm) + "x" + Math.round(requested.height_mm) + " mm"
      );
    }
    return bits.length ? bits.join(" · ") : "—";
  }

  /* ------------------------------------------------------------------ */
  /* details panel                                                       */
  /* ------------------------------------------------------------------ */

  function renderDetails(page) {
    const box = $("details");
    box.textContent = "";
    const media =
      state.preflight && state.preflight.pages[page - 1]
        ? state.preflight.pages[page - 1]
        : null;

    box.appendChild(el("h3", null, "Page " + page));
    const list = el("dl");
    const add = (key, value) => {
      list.appendChild(el("dt", null, key));
      list.appendChild(el("dd", null, value === null || value === undefined ? "—" : value));
    };
    add("Offset", media && media.offset !== null ? media.offset : null);
    add("Document", media ? media.document_id : null);
    add("Page group", media ? media.page_group : null);
    box.appendChild(list);

    if (!media) {
      box.appendChild(el("p", "empty-note", "No preflight data for this page."));
      return;
    }

    box.appendChild(el("div", "section-title", "Requested media"));
    const requested = el("dl");
    const addReq = (key, value) => {
      requested.appendChild(el("dt", null, key));
      requested.appendChild(
        el("dd", null, value === null || value === undefined ? "—" : value)
      );
    };
    addReq("Medium map", media.medium_map);
    addReq("Form map", media.requested.form_map);
    addReq("Media type", media.requested.media_type);
    addReq("Destination", media.requested.media_destination);
    addReq(
      "Sheet",
      media.requested.width_mm
        ? Math.round(media.requested.width_mm) +
            " x " +
            Math.round(media.requested.height_mm) +
            " mm"
        : null
    );
    addReq("Duplex", media.requested.duplex);
    addReq("Copies", media.requested.copies);
    addReq("Map source", media.medium_map_source);
    box.appendChild(requested);

    box.appendChild(el("div", "section-title", "Resolved media"));
    const resolved = el("dl");
    const addRes = (key, value) => {
      resolved.appendChild(el("dt", null, key));
      resolved.appendChild(
        el("dd", null, value === null || value === undefined ? "—" : value)
      );
    };
    addRes("Paper stock", media.resolved.stock_name);
    addRes("Tray", media.resolved.tray);
    addRes("Duplex", media.resolved.duplex);
    addRes("Source", media.resolved.source);
    addRes("Verified", media.resolved.verified ? "yes" : "no");
    box.appendChild(resolved);

    box.appendChild(el("div", "section-title", "Actual media"));
    box.appendChild(el("p", "empty-note", "Unknown — no printer or RIP evidence available."));

    box.appendChild(el("div", "section-title", "Status"));
    const pill = el("span", "status-pill", media.status);
    pill.dataset.status = media.status;
    box.appendChild(pill);

    if (media.findings.length) {
      box.appendChild(el("div", "section-title", "Findings"));
      const list2 = el("ul", "finding-list");
      media.findings.forEach((finding) => {
        const item = el("li", null, finding.code + " — " + finding.message);
        list2.appendChild(item);
      });
      box.appendChild(list2);
    }
  }

  /* ------------------------------------------------------------------ */
  /* resources                                                           */
  /* ------------------------------------------------------------------ */

  function renderResources() {
    const wrap = $("resources-table");
    wrap.textContent = "";
    const summary = state.document || {};
    const missing = (state.preflight && state.preflight.findings) || [];
    const resourceFindings = missing.filter(
      (finding) => finding.code === "MISSING_RESOURCE" || finding.code === "EXTERNAL_RESOURCE_ATTESTED" || finding.code === "RESOURCE_SCOPE_UNVERIFIED"
    );
    $("resources-note").textContent =
      summary.medium_maps ? summary.medium_maps.length + " medium maps" : "";

    const table = el("table");
    const head = el("thead");
    const headRow = el("tr");
    ["Kind", "Name", "Detail"].forEach((title) =>
      headRow.appendChild(el("th", null, title))
    );
    head.appendChild(headRow);
    table.appendChild(head);
    const body = el("tbody");
    if (!resourceFindings.length) {
      const tr = el("tr");
      const td = el("td", "empty-note", "No missing resources detected by the supported font checks. Other resources and scopes may be unverified.");
      td.colSpan = 3;
      tr.appendChild(td);
      body.appendChild(tr);
    } else {
      resourceFindings.forEach((finding) => {
        const tr = el("tr");
        tr.appendChild(el("td", "mono", finding.detail.kind || ""));
        tr.appendChild(el("td", "mono", finding.detail.name || ""));
        tr.appendChild(el("td", "finding-cell", finding.message));
        body.appendChild(tr);
      });
    }
    table.appendChild(body);
    wrap.appendChild(table);

    if (summary.medium_maps && summary.medium_maps.length) {
      wrap.appendChild(el("div", "section-title", "Medium maps in this file"));
      const list = el("ul", "finding-list");
      summary.medium_maps.forEach((name) =>
        list.appendChild(el("li", "mono", name))
      );
      wrap.appendChild(list);
    }
    if (state.document) {
      const documentId = state.document.document_id;
      const section = el("div", "resource-references");
      section.appendChild(el("h3", null, "Resource dependency observations"));
      const note = el("p", "empty-note", "Loading...");
      section.appendChild(note);
      wrap.appendChild(section);
      api("/api/documents/" + documentId + "/resource-dependencies?limit=100")
        .then((data) => {
          if (!state.document || state.document.document_id !== documentId || !section.isConnected) return;
          note.textContent = "Partial font and named-include coverage; scope remains unverified. Inventory entries are operator attestations, not observed printer results. Showing " + data.items.length + " of " + data.total + "." + (data.complete ? "" : " Index incomplete.");
          const refs = el("ul", "finding-list");
          data.items.forEach((item) => {
            const text = item.kind + " " + item.name + " — offset " + item.offset +
              (item.declared_on_page ? ", declared on page " + item.declared_on_page : ", page not determined") + " — " + item.status +
              (item.evidence ? " — evidence: " + item.evidence.evidence_ref + ", " + item.evidence.verified_by + ", " + item.evidence.verified_at : "");
            const row = el("li", null, text);
            refs.appendChild(row);
          });
          section.appendChild(refs);
        }).catch((err) => { if (section.isConnected) note.textContent = err.message; });
    }
  }

  /* ------------------------------------------------------------------ */
  /* report                                                              */
  /* ------------------------------------------------------------------ */

  async function loadReport() {
    if (!state.document) return;
    const documentId = state.document.document_id;
    const url = "/api/documents/" + documentId + "/report";
    try {
      const result = await api(url);
      if (state.document && state.document.document_id === documentId) state.report = result;
    } catch (err) {
      setStatus(err.message);
    }
  }

  function renderReport() {
    const box = $("report-body");
    box.textContent = "";
    if (!state.report) {
      box.appendChild(el("p", "empty-note", "No report has been built yet."));
      return;
    }
    const stats = state.report.statistics;
    const grid = el("div", "report-grid");
    [
      ["Total AFP pages", stats.total_pages],
      ["Total documents", stats.total_documents],
      ["Named page groups", stats.total_page_groups],
      ["Estimated sheets", stats.estimated_sheets],
      ["Mailpieces", stats.mailpieces],
      ["Dispatched", stats.dispatched]
    ].forEach(([label, quantity]) => {
      const metric = el("div", "metric");
      metric.appendChild(el("div", "label", label));
      const value = el(
        "div",
        "value" + (quantity.value === null ? " unknown" : ""),
        quantity.value === null ? "unknown" : quantity.value
      );
      metric.appendChild(value);
      metric.appendChild(el("div", "basis " + quantity.basis, quantity.basis));
      if (quantity.note) metric.appendChild(el("div", "note", quantity.note));
      grid.appendChild(metric);
    });
    box.appendChild(grid);

    box.appendChild(el("h3", null, "Medium map usage"));
    const usageTable = el("table");
    const head = el("thead");
    const headRow = el("tr");
    ["Medium map", "Pages", "Paper stock", "Tray", "Copies"].forEach((title) =>
      headRow.appendChild(el("th", null, title))
    );
    head.appendChild(headRow);
    usageTable.appendChild(head);
    const body = el("tbody");
    stats.medium_map_usage.forEach((usage) => {
      const tr = el("tr");
      tr.appendChild(el("td", "mono", usage.medium_map));
      tr.appendChild(el("td", "num", usage.pages));
      tr.appendChild(el("td", "mono", usage.stock_name || "unresolved"));
      tr.appendChild(el("td", "mono", usage.tray || "—"));
      tr.appendChild(el("td", "num", usage.copies === null ? "—" : usage.copies));
      body.appendChild(tr);
    });
    usageTable.appendChild(body);
    box.appendChild(usageTable);

    box.appendChild(el("h3", null, "Media transitions"));
    const transitionTable = el("table");
    const thead = el("thead");
    const thr = el("tr");
    ["Page", "From", "To", "Offset"].forEach((title) =>
      thr.appendChild(el("th", null, title))
    );
    thead.appendChild(thr);
    transitionTable.appendChild(thead);
    const tbody = el("tbody");
    stats.transitions.forEach((transition) => {
      const tr = el("tr");
      tr.appendChild(el("td", "num", transition.page_number));
      tr.appendChild(el("td", "mono", transition.from_map || "—"));
      tr.appendChild(el("td", "mono", transition.to_map || "—"));
      tr.appendChild(el("td", "mono", transition.offset === null ? "—" : transition.offset));
      tbody.appendChild(tr);
    });
    transitionTable.appendChild(tbody);
    box.appendChild(transitionTable);

    box.appendChild(el("h3", null, "Production control report template"));
    box.appendChild(el("p", "empty-note", "Template only. External production evidence and sign-off are not recorded."));
    const control = state.report.control_report;
    const controlTable = el("table");
    const cthead = el("thead");
    const cthr = el("tr");
    ["Stage", "Status", "Value", "Basis", "Note"].forEach((title) =>
      cthr.appendChild(el("th", null, title))
    );
    cthead.appendChild(cthr);
    controlTable.appendChild(cthead);
    const ctbody = el("tbody");
    control.reconciliation.forEach((row) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, row.label));
      tr.appendChild(el("td", "mono", row.status));
      tr.appendChild(
        el("td", "num", row.value === null ? "unknown" : row.value)
      );
      tr.appendChild(el("td", "mono", row.basis));
      tr.appendChild(el("td", "finding-cell", row.note));
      ctbody.appendChild(tr);
    });
    controlTable.appendChild(ctbody);
    box.appendChild(controlTable);
    const journal = state.report.production_journal;
    $("journal-result").textContent = journal ? JSON.stringify(journal, null, 2) : "No journal data.";
    $("journal-status").textContent = journal ? "Sign-off: " + journal.signoff_status + ". " +
      (journal.release_gate.eligible ? "Reported quantities reconcile; eligible to record an operator sign-off after review." : journal.release_gate.reasons.join(" ")) : "";
  }

  /* ------------------------------------------------------------------ */
  /* settings                                                            */
  /* ------------------------------------------------------------------ */

  async function renderSettings() {
    const box = $("settings-body");
    box.textContent = "";

    const parserCard = el("div", "card");
    parserCard.appendChild(el("h3", null, "Java parser (optional)"));
    try {
      const parser = await api("/api/parser");
      parserCard.appendChild(
        el(
          "p",
          "empty-note",
          parser.available
            ? "Available: " + parser.jar
            : parser.reason
        )
      );
    } catch (err) {
      parserCard.appendChild(el("p", "empty-note", err.message));
    }
    box.appendChild(parserCard);

    const profilesCard = el("div", "card");
    profilesCard.appendChild(el("h3", null, "Printer profiles"));
    try {
      const profiles = await api("/api/profiles");
      if (!profiles.profiles.length) {
        profilesCard.appendChild(
          el("p", "empty-note", "No printer profiles have been defined.")
        );
      }
      for (const profile of profiles.profiles) {
        const row = el("div", "tree-item");
        row.appendChild(el("span", "label", profile.printer_profile));
        row.appendChild(
          el("span", "meta", profile.media_maps.length + " maps")
        );
        profilesCard.appendChild(row);
        const pre = el("pre");
        pre.textContent = JSON.stringify(profile, null, 2);
        profilesCard.appendChild(pre);
      }
    } catch (err) {
      profilesCard.appendChild(el("p", "empty-note", err.message));
    }
    box.appendChild(profilesCard);

    const privacyCard = el("div", "card");
    privacyCard.appendChild(el("h3", null, "Privacy"));
    privacyCard.appendChild(
      el(
        "p",
        "empty-note",
        "Documents may be read from local storage or a selected network " +
          "share, and are processed on this computer. No documents are " +
          "uploaded to external services; the server uses loopback only."
      )
    );
    box.appendChild(privacyCard);
  }

  /* ------------------------------------------------------------------ */
  /* search                                                              */
  /* ------------------------------------------------------------------ */

  function initSearch() {
    const input = $("search-input");
    let timer = null;
    input.addEventListener("input", () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(runSearch, 260);
    });
  }

  async function runSearch() {
    const query = $("search-input").value.trim();
    const box = $("search-results");
    if (!query || !state.document) {
      box.hidden = true;
      box.textContent = "";
      return;
    }
    const documentId = state.document.document_id;
    try {
      const payload = await api(
        "/api/documents/" +
          state.document.document_id +
          "/search", { method: "POST", body: { q: query } }
      );
      if (!state.document || state.document.document_id !== documentId ||
          $("search-input").value.trim() !== query) return;
      box.textContent = "";
      const hits = [];
      payload.structure.forEach((row) => {
        hits.push({
          kind: "field",
          label: row.name + (row.token ? " " + row.token : ""),
          page: row.page === null ? null : row.page + 1,
          offset: row.offset
        });
      });
      payload.text.forEach((hit) => {
        hits.push({
          kind: "text",
          label: hit.match,
          page: hit.page === null ? null : hit.page + 1,
          offset: hit.offset
        });
      });
      if (!hits.length) {
        box.appendChild(el("div", "tree-group", "No matches"));
        box.hidden = false;
        return;
      }
      hits.slice(0, 100).forEach((hit) => {
        const row = el("div", "search-hit");
        row.appendChild(el("span", "kind", hit.kind));
        row.appendChild(el("span", "label", hit.label));
        row.appendChild(
          el(
            "span",
            "where",
            (hit.page === null ? "-" : "p" + hit.page) + " @" + hit.offset
          )
        );
        row.addEventListener("click", () => {
          if (hit.page !== null) showPage(hit.page);
        });
        box.appendChild(row);
      });
      box.hidden = false;
    } catch (err) {
      setStatus(err.message);
    }
  }

  /* ------------------------------------------------------------------ */
  /* export                                                              */
  /* ------------------------------------------------------------------ */

  function initExport() {
    function confirmOverwrite(path) {
      const dialog = $("overwrite-dialog");
      $("overwrite-path").textContent = path;
      return new Promise((resolve) => {
        const finish = (accepted) => { dialog.close(); resolve(accepted); };
        $("overwrite-cancel").onclick = () => finish(false);
        $("overwrite-confirm").onclick = () => finish(true);
        dialog.oncancel = (event) => { event.preventDefault(); finish(false); };
        dialog.showModal();
      });
    }
    const doExport = async (format) => {
      if (!state.document) return;
      const documentId = state.document.document_id;
      const suggested =
        state.document.path.replace(/\.[^.]+$/, "") + "." + format;

      // The desktop shell has a native save dialog. A browser tab
      // falls back to a prompt. The two are not interchangeable: the
      // embedded browser engine has no prompt implementation, so
      // calling one there would hang the window.
      let target = null;
      if (nativeApi()) {
        target = await pickSavePath(suggested, format);
      } else {
        target = window.prompt(
          "Save the " + format.toUpperCase() + " report to (full path):",
          suggested
        );
      }
      if (!target) return;
      try {
        const result = await api(
          "/api/documents/" + documentId + "/export",
          { method: "POST", body: { format: format, path: target } }
        );
        setStatus("Report written to " + result.path);
      } catch (err) {
        if (err.code !== "overwrite_required") { setStatus(err.message); return; }
        const finalPath = target.replace(/\.[^./\\]+$/, "") + "." + format;
        if (!(await confirmOverwrite(finalPath))) { setStatus("Export cancelled."); return; }
        try {
          const result = await api("/api/documents/" + documentId + "/export", {
            method: "POST", body: { format: format, path: target, overwrite: true }
          });
          setStatus("Report written to " + result.path);
        } catch (retryError) { setStatus(retryError.message); }
      }
    };
    $("export-csv").addEventListener("click", () => doExport("csv"));
    $("export-excel").addEventListener("click", () => doExport("xlsx"));
  }

  /* ------------------------------------------------------------------ */
  /* boot                                                                */
  /* ------------------------------------------------------------------ */

  function initEngineering() {
    let jobId = null;
    whenNativeReady(() => { $("engineering-browse").hidden = false; });
    $("engineering-browse").addEventListener("click", async () => {
      const operation = $("engineering-operation").value;
      const suffix = ["copy", "edit", "extract", "merge"].includes(operation) ? "afp" : operation;
      const path = await pickSavePath("output." + suffix, suffix);
      if (path) $("engineering-path").value = path;
    });
    $("engineering-cancel").addEventListener("click", async () => {
      if (!jobId) return;
      try { await api("/api/jobs/" + jobId + "/cancel", { method: "POST" }); }
      catch (err) { $("engineering-status").textContent = err.message; }
    });
    $("engineering-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!state.document || jobId) { setStatus("Open an AFP document first."); return; }
      const documentId = state.document.document_id;
      const operation = $("engineering-operation").value;
      const rawPages = $("engineering-pages").value.trim();
      const pages = rawPages ? rawPages.split(",").map((value) => Number(value.trim()) - 1) : [state.page - 1];
      let patches;
      try { patches = JSON.parse($("engineering-patches").value); }
      catch (err) { $("engineering-status").textContent = "Edit instructions must be valid JSON."; return; }
      const body = { operation: operation, path: $("engineering-path").value.trim(), pages: pages, patches: patches,
        document_number: Number($("engineering-document").value),
        merge_paths: $("engineering-merge").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean) };
      const endpoint = "/api/documents/" + documentId + "/engineering";
      $("engineering-run").disabled = true;
      $("engineering-result").textContent = "";
      try {
        let result;
        try { result = await api(endpoint, { method: "POST", body: body }); }
        catch (err) {
          if (err.code !== "overwrite_required") throw err;
          const accepted = await new Promise((resolve) => {
            const dialog = $("overwrite-dialog");
            const suffix = ["copy", "edit", "extract", "merge"].includes(operation) ? "afp" : operation;
            $("overwrite-path").textContent = body.path.replace(/\.[^./\\]+$/, "") + "." + suffix;
            const finish = (value) => { dialog.close(); resolve(value); };
            $("overwrite-cancel").onclick = () => finish(false);
            $("overwrite-confirm").onclick = () => finish(true);
            dialog.oncancel = (ev) => { ev.preventDefault(); finish(false); };
            dialog.showModal();
          });
          if (!accepted) { $("engineering-status").textContent = "Cancelled before generation."; return; }
          body.overwrite = true;
          result = await api(endpoint, { method: "POST", body: body });
        }
        jobId = result.job.id;
        $("engineering-cancel").disabled = false;
        while (jobId) {
          const job = await api("/api/jobs/" + jobId);
          $("engineering-status").textContent = job.state + " — " + (job.error || job.message || "");
          if (["completed", "failed", "cancelled"].includes(job.state)) {
            if (job.result) $("engineering-result").textContent = JSON.stringify(job.result, null, 2);
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 350));
        }
      } catch (err) { $("engineering-status").textContent = err.message; }
      finally { jobId = null; $("engineering-run").disabled = false; $("engineering-cancel").disabled = true; }
    });
  }

  function initBatch() {
    let jobId = null;
    $("batch-cancel").addEventListener("click", async () => {
      if (!jobId) return;
      try { await api("/api/jobs/" + jobId + "/cancel", { method: "POST" }); }
      catch (err) { $("batch-status").textContent = err.message; }
    });
    $("batch-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (jobId) return;
      $("batch-run").disabled = true;
      $("batch-result").textContent = "";
      try {
        const result = await api("/api/batch/preflight", { method: "POST", body: {
          paths: $("batch-paths").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
          profile: $("profile-select").value || null, input_format: $("input-format").value
        } });
        jobId = result.job.id;
        $("batch-cancel").disabled = false;
        while (jobId) {
          const job = await api("/api/jobs/" + jobId);
          $("batch-status").textContent = job.state + " — " + Math.round(job.progress * 100) + "% — " + (job.error || job.message || "");
          if (["completed", "failed", "cancelled"].includes(job.state)) {
            if (job.result) $("batch-result").textContent = JSON.stringify(job.result, null, 2);
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 350));
        }
      } catch (err) { $("batch-status").textContent = err.message; }
      finally { jobId = null; $("batch-run").disabled = false; $("batch-cancel").disabled = true; }
    });
  }

  function initJournal() {
    $("journal-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!state.document) { setStatus("Open an AFP document first."); return; }
      const documentId = state.document.document_id;
      const stage = $("journal-stage").value;
      const signoff = stage === "signoff";
      if (signoff && !$("journal-confirm").checked) {
        $("journal-status").textContent = "Review the evidence and confirm your intent before recording sign-off.";
        return;
      }
      if (!signoff && stage !== "reconciliation" && !$("journal-value").value.trim()) {
        $("journal-status").textContent = "Enter an evidenced quantity; blank is unknown, not zero.";
        return;
      }
      const body = { action: signoff ? "signoff" : "evidence", stage: stage,
        unit: signoff || stage === "reconciliation" ? "unknown" : $("journal-unit").value,
        value: signoff || stage === "reconciliation" ? null : Number($("journal-value").value),
        source: $("journal-source").value, reference: $("journal-reference").value,
        recorded_by: $("journal-operator").value, note: $("journal-note").value };
      $("journal-save").disabled = true;
      try {
        await api("/api/documents/" + documentId + "/production-journal", { method: "POST", body: body });
        if (state.document && state.document.document_id === documentId) { await loadReport(); renderReport(); }
        $("journal-confirm").checked = false;
      } catch (err) { $("journal-status").textContent = err.message; }
      finally { $("journal-save").disabled = false; }
    });
  }

  async function loadProfiles() {
    try {
      const payload = await api("/api/profiles");
      const select = $("profile-select");
      payload.profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = profile.name;
        option.textContent = profile.printer_profile;
        select.appendChild(option);
      });
    } catch (err) {
      setStatus(err.message);
    }
  }

  function init() {
    initTabs();
    initOpener();
    initViewer();
    initPanelResizing();
    initWorkspacePreferences();
    initSearch();
    initExport();
    initEngineering();
    initBatch();
    initJournal();
    $("shortcut-help").addEventListener("click", () => $("shortcuts-dialog").showModal());
    $("shortcut-close").addEventListener("click", () => $("shortcuts-dialog").close());
    $("index-kind").addEventListener("change", () => { documentIndexOffset = 0; loadDocumentIndex(); });
    $("index-codepage").addEventListener("change", () => { documentIndexOffset = 0; loadDocumentIndex(); });
    let indexSearchTimer;
    $("index-query").addEventListener("input", () => { clearTimeout(indexSearchTimer); indexSearchTimer = setTimeout(() => { documentIndexOffset = 0; loadDocumentIndex(); }, 350); });
    $("index-prev").addEventListener("click", () => { documentIndexOffset = Math.max(0, documentIndexOffset - 100); loadDocumentIndex(); });
    $("index-next").addEventListener("click", () => { if (documentIndexOffset + 100 < documentIndexTotal) { documentIndexOffset += 100; loadDocumentIndex(); } });
    loadProfiles();
    setInterval(async () => {
      if (!state.document) return;
      try { await api("/api/documents/" + state.document.document_id); }
      catch (err) { if (err.code !== "source_changed") setStatus(err.message); }
    }, 10000);
    $("close-document").addEventListener("click", closeActiveDocument);
    document.addEventListener("keydown", (event) => {
      if (event.isComposing || event.defaultPrevented || document.querySelector("dialog[open]")) return;
      const editable = event.target.closest("input, textarea, select, [contenteditable=true]");
      const key = event.key.toLowerCase();
      if (event.ctrlKey && event.shiftKey && !event.altKey && key === "o") {
        event.preventDefault(); $("recent-open").click(); return;
      }
      if (event.ctrlKey && !event.shiftKey && !event.altKey && key === "o") {
        event.preventDefault(); $("open-new").click(); return;
      }
      if (event.ctrlKey && !event.altKey && key === "g" && state.document) {
        event.preventDefault(); $("page-number").focus(); $("page-number").select(); return;
      }
      if (event.ctrlKey && !event.altKey && key === "f" && state.document) {
        event.preventDefault();
        if ($("workspace").classList.contains("tree-collapsed")) $("toggle-tree").click();
        $("search-input").focus(); return;
      }
      if (!editable && state.document && !multiOpenRunning) {
        const desktopCycle = nativeApi() && event.ctrlKey && !event.altKey && key === "tab";
        const alternateCycle = event.altKey && event.shiftKey && !event.ctrlKey &&
          (event.code === "BracketLeft" || event.code === "BracketRight");
        if (desktopCycle || alternateCycle) {
          event.preventDefault();
          const ids = Array.from(documents.keys());
          const step = desktopCycle ? (event.shiftKey ? -1 : 1) :
            (event.code === "BracketLeft" ? -1 : 1);
          const index = ids.indexOf(state.document.document_id);
          if (ids.length && index >= 0) activateDocument(ids[(index + step + ids.length) % ids.length]);
          return;
        }
        if (nativeApi() && event.ctrlKey && !event.shiftKey && !event.altKey && key === "w") {
          event.preventDefault(); closeActiveDocument(); return;
        }
      }
      if (editable || !state.document || state.tab !== "viewer" ||
          !$("viewer-stage").contains(document.activeElement)) return;
      if (key === "pagedown") { event.preventDefault(); showPage(state.page + 1); }
      else if (key === "pageup") { event.preventDefault(); showPage(state.page - 1); }
      else if (event.ctrlKey && key === "home") { event.preventDefault(); showPage(1); }
      else if (event.ctrlKey && key === "end") { event.preventDefault(); showPage(state.document.page_count); }
      else if (event.ctrlKey && (key === "+" || key === "=")) { event.preventDefault(); setZoom(state.zoom * 1.25); }
      else if (event.ctrlKey && key === "-") { event.preventDefault(); setZoom(state.zoom / 1.25); }
      else if (!event.ctrlKey && !event.altKey && key === "f") { event.preventDefault(); fitPage(); }
      else if (!event.ctrlKey && !event.altKey && key === "w") { event.preventDefault(); setFitMode("width"); }
    });
    setStatus("Ready. Enter the path of an AFP file to begin.");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
