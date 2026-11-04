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
    zoomFitted: true,
    tab: "viewer",
    tree: null,
    jobTimer: null,
    openJobId: null
  };

  const $ = (id) => document.getElementById(id);

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
      return result.path || null;
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
      throw error;
    }
    return payload;
  }

  /* ------------------------------------------------------------------ */
  /* opener                                                              */
  /* ------------------------------------------------------------------ */

  function initOpener() {
    // The Browse buttons only work where the desktop shell provides a
    // native dialog. In a browser tab they are hidden, because there
    // is nothing behind them.
    whenNativeReady(() => {
      $("browse-button").hidden = false;
      $("browse-formdef-button").hidden = false;
    });

    $("browse-button").addEventListener("click", async () => {
      const path = await pickFile("pick_afp_file");
      if (path) {
        $("path-input").value = path;
        $("open-error").textContent = "";
      }
    });

    $("browse-formdef-button").addEventListener("click", async () => {
      const path = await pickFile("pick_form_definition");
      if (path) $("formdef-input").value = path;
    });

    $("open-form").addEventListener("submit", async (event) => {
      event.preventDefault();
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
          profile: $("profile-select").value || null,
          use_java_parser: $("java-parser").checked
        };
        const formDef = $("formdef-input").value.trim();
        if (formDef) body.form_defs = [formDef];
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
    state.document = summary;
    $("opener").hidden = true;
    $("tabs").hidden = false;
    $("workspace").hidden = false;
    $("doc-chip").hidden = false;
    $("doc-name").textContent = summary.filename;
    $("doc-status").textContent = summary.preflight_status;
    $("doc-status").dataset.status = summary.preflight_status;
    $("page-total").textContent = "/ " + summary.page_count;
    $("page-number").max = summary.page_count;
    setStatus(
      "Opened " + summary.filename + " — " + summary.page_count + " page(s)."
    );
    await refreshAll();
    showPage(1);
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
    if (tab === "preflight") renderPreflight();
    if (tab === "resources") renderResources();
    if (tab === "report") renderReport();
    if (tab === "settings") renderSettings();
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
  }

  function setZoom(value) {
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
  }

  function fitPage() {
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
    const scale = Math.min(
      available / size.width,
      availableHeight > 0 ? availableHeight / size.height : Infinity
    );
    setZoom(Math.min(2, Math.max(0.1, scale)));
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
      if (!response.ok) throw new Error("The page could not be rendered.");
      const svg = await response.text();
      if (requestId !== pageRequest || !state.document || state.document.document_id !== documentId) return;
      holder.innerHTML = svg;
      $("viewer-empty").hidden = true;
      // The first page is fitted to the stage; after that the operator's
      // own zoom is kept.
      if (state.zoomFitted) {
        fitPage();
        state.zoomFitted = false;
      } else {
        setZoom(state.zoom);
      }
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
    const url =
      "/api/documents/" +
      state.document.document_id +
      "/fields?page=" +
      (page - 1);
    try {
      const payload = await api(url);
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
    const url = "/api/documents/" + state.document.document_id + "/preflight";
    try {
      state.preflight = await api(url);
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
      (finding) => finding.code === "MISSING_RESOURCE"
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
      const td = el("td", "empty-note", "Every referenced resource is embedded.");
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
  }

  /* ------------------------------------------------------------------ */
  /* report                                                              */
  /* ------------------------------------------------------------------ */

  async function loadReport() {
    if (!state.document) return;
    const url = "/api/documents/" + state.document.document_id + "/report";
    try {
      state.report = await api(url);
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
        "Documents are read from disk, indexed in memory and rendered on " +
          "this machine. Nothing is uploaded, no telemetry is sent, and the " +
          "server binds to the loopback interface only."
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
    try {
      const payload = await api(
        "/api/documents/" +
          state.document.document_id +
          "/search?q=" +
          encodeURIComponent(query)
      );
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
          "/api/documents/" + state.document.document_id + "/export",
          { method: "POST", body: { format: format, path: target } }
        );
        setStatus("Report written to " + result.path);
      } catch (err) {
        if (err.code !== "overwrite_required") { setStatus(err.message); return; }
        const finalPath = target.replace(/\.[^./\\]+$/, "") + "." + format;
        if (!(await confirmOverwrite(finalPath))) { setStatus("Export cancelled."); return; }
        try {
          const result = await api("/api/documents/" + state.document.document_id + "/export", {
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
    initSearch();
    initExport();
    loadProfiles();
    $("close-document").addEventListener("click", async () => {
      if (!state.document) return;
      try {
        await api("/api/documents/" + state.document.document_id, {
          method: "DELETE"
        });
      } catch (err) {
        setStatus(err.message);
      }
      state.document = null;
      state.preflight = null;
      state.report = null;
      $("opener").hidden = false;
      $("tabs").hidden = true;
      $("workspace").hidden = true;
      $("doc-chip").hidden = true;
      setStatus("Document closed.");
    });
    setStatus("Ready. Enter the path of an AFP file to begin.");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
