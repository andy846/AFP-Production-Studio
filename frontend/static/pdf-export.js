/* Local PDF task controller. Document snapshots keep background jobs independent
 * of the currently selected document, page and tool. */
(function () {
  "use strict";
  window.initPdfExport = function (options) {
    const $ = id => document.getElementById(id);
    const form = $("pdf-export-form");
    if (!form) return;
    let active = null, choosing = null, retry = null, completed = null;
    const status = message => {
      $("pdf-status").textContent = message;
      if ($("pdf-task-tray")) {
        $("pdf-task-tray").textContent = message;
        $("pdf-task-tray").hidden = false;
      }
    };
    const busy = value => {
      $("pdf-start").disabled = value;
      $("pdf-cancel").disabled = !value;
      form.setAttribute("aria-busy", String(value));
      form.querySelectorAll("input, select").forEach(node => { node.disabled = value; });
      $("pdf-browse").disabled = value;
    };
    const source = () => {
      const doc = options.getDocument();
      const task = active || choosing;
      $("pdf-source").textContent = task ? "Exporting " + task.filename :
        doc ? doc.filename + " · " + doc.page_count + " pages" : "Open an AFP document first.";
      $("export-pdf").disabled = !doc;
      if (!active && !choosing) $("pdf-start").disabled = !doc;
    };
    const selectedMode = () => {
      const range = $("pdf-pages-mode").value === "range";
      $("pdf-range").disabled = Boolean(active || choosing) || !range;
      $("pdf-range").required = range;
    };
    const showWarnings = (items, snapshot) => {
      const list = $("pdf-warning-list");
      list.replaceChildren();
      (items || []).slice(0, 200).forEach(item => {
        const line = document.createElement("li");
        const page = item.page || 1;
        line.textContent = "Page " + page + ": " + (item.message || item.code || "Preview limitation");
        const inspect = document.createElement("button");
        inspect.type = "button";
        inspect.className = "ghost small";
        inspect.textContent = Number.isInteger(item.offset) ? "Inspect field" : "Inspect page";
        inspect.addEventListener("click", () => options.locate(snapshot.id, page, item.offset));
        line.appendChild(inspect);
        list.appendChild(line);
      });
    };
    const savePath = async doc => {
      if (!doc) return null;
      const name = doc.filename.replace(/\.[^.]+$/, "") + ".pdf";
      const chosen = await options.pickSavePath(name, "pdf");
      return chosen;
    };
    const requestCancellation = async snapshot => {
      if (active !== snapshot) return;
      snapshot.cancelRequested = true;
      $("pdf-cancel").disabled = true;
      status("Cancellation requested — waiting for the current rendering step.");
      if (!snapshot.job) return;
      try {
        await options.api("/api/jobs/" + snapshot.job + "/cancel", {method: "POST"});
      } catch (err) {
        if (active === snapshot) status(err.message);
      }
    };
    const begin = async snapshot => {
      if (active) return;
      active = snapshot;
      snapshot.job = null;
      snapshot.cancelRequested = false;
      source();
      retry = null; completed = null;
      busy(true);
      $("pdf-open").hidden = $("pdf-reveal").hidden = true;
      $("pdf-accept-limitations").hidden = true;
      $("pdf-result").textContent = "";
      $("pdf-warning-list").replaceChildren();
      $("pdf-progress").value = 0;
      status("Preparing PDF — " + snapshot.filename);
      try {
        let response;
        const endpoint = "/api/documents/" + snapshot.id + "/pdf-export";
        try { response = await options.api(endpoint, {method: "POST", body: snapshot.body}); }
        catch (err) {
          if (active !== snapshot) return;
          if (err.code !== "overwrite_required") throw err;
          if (!await options.confirmOverwrite(snapshot.body.path.replace(/\.[^./\\]+$/, "") + ".pdf")) {
            if (active === snapshot) status("Export cancelled. Existing output kept."); return;
          }
          if (active !== snapshot) return;
          if (snapshot.cancelRequested) { status("Export cancelled before generation."); return; }
          snapshot.body.overwrite = true;
          response = await options.api(endpoint, {method: "POST", body: snapshot.body});
        }
        if (active !== snapshot) return;
        snapshot.job = response.job.id;
        if (snapshot.cancelRequested) await requestCancellation(snapshot);
        while (active === snapshot) {
          const job = await options.api("/api/jobs/" + snapshot.job);
          if (active !== snapshot) return;
          $("pdf-progress").value = job.progress || 0;
          status(snapshot.filename + " — " + job.state + " · " + Math.round((job.progress || 0) * 100) + "% · " + (job.message || ""));
          if (job.state === "completed") {
            completed = snapshot;
            const result = job.result;
            const warningCount = result.warning_count || 0;
            status(warningCount ? "Completed with preview warnings — " + snapshot.filename : "PDF completed — " + snapshot.filename);
            $("pdf-result").textContent = (result.page_count || result.pages.length) + " pages · " +
              (result.bytes / 1048576).toFixed(2) + " MB · " + result.dpi + " dpi · " +
              Number(result.elapsed_seconds || 0).toFixed(1) + " s\n" + result.path +
              "\nPreview fidelity · not production validated" +
              (warningCount ? "\n" + warningCount + " warnings on pages " + (result.warning_pages || []).join(", ") : "");
            showWarnings(result.warnings, snapshot);
            $("pdf-open").hidden = $("pdf-reveal").hidden = !options.nativeApi();
            break;
          }
          if (job.state === "failed") {
            const details = job.error_details || {};
            status(job.error_code === "incomplete_preview" ?
              "Export stopped — incomplete preview on page " + details.page + ". No PDF was published." :
              "Export failed — " + (job.error || job.message));
            showWarnings((details.issues || []).map(item => ({...item, page: details.page})), snapshot);
            if (job.error_code === "incomplete_preview") {
              retry = snapshot;
              $("pdf-accept-limitations").hidden = false;
            }
            break;
          }
          if (job.state === "cancelled") { status("Export cancelled. No PDF was published."); break; }
          await new Promise(resolve => setTimeout(resolve, 350));
        }
      } catch (err) {
        if (active === snapshot) status("Export failed — " + err.message);
      } finally {
        if (active === snapshot) { active = null; busy(false); source(); selectedMode(); }
      }
    };
    $("export-pdf").addEventListener("click", () => {
      document.querySelector('[data-tab="export"]').click(); source();
      $("pdf-pages-mode").focus();
    });
    $("pdf-pages-mode").addEventListener("change", selectedMode);
    $("pdf-browse").addEventListener("click", async () => {
      const doc = options.getDocument();
      if (!doc || active || choosing) return;
      const path = await savePath(doc);
      if (path && options.getDocument()?.document_id === doc.document_id && !active && !choosing) $("pdf-path").value = path;
    });
    options.whenNativeReady(() => { $("pdf-browse").hidden = false; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const doc = options.getDocument();
      if (!doc || active || choosing) return;
      // Freeze document, page and settings before any native dialog await.
      const snapshot = {id: doc.document_id, filename: doc.filename, body: {
        path: $("pdf-path").value.trim(), selection: $("pdf-pages-mode").value,
        page_range: $("pdf-range").value, current_page: options.getPage(),
        dpi: Number($("pdf-dpi").value), allow_incomplete: false, overwrite: false
      }};
      if (!snapshot.body.path && options.nativeApi()) {
        choosing = snapshot; busy(true);
        try {
          const path = await savePath(doc);
          if (choosing !== snapshot) return;
          if (!path) return;
          snapshot.body.path = path;
          $("pdf-path").value = path;
        } finally {
          if (choosing === snapshot) { choosing = null; busy(false); source(); selectedMode(); }
        }
      }
      if (!snapshot.body.path) { status("Enter a full output PDF path."); $("pdf-path").focus(); return; }
      begin(snapshot);
    });
    $("pdf-cancel").addEventListener("click", async () => {
      const snapshot = active;
      if (snapshot) await requestCancellation(snapshot);
    });
    $("pdf-accept-limitations").addEventListener("click", () => {
      if (!retry || active) return;
      begin({...retry, body: {...retry.body, allow_incomplete: true}});
    });
    [["pdf-open", "open"], ["pdf-reveal", "reveal"]].forEach(([id, action]) => {
      $(id).addEventListener("click", async () => {
        const snapshot = completed;
        if (!snapshot) return;
        try { await options.api("/api/jobs/" + snapshot.job + "/output-action", {method: "POST", body: {action}}); }
        catch (err) { if (completed === snapshot && !active) status(err.message); }
      });
    });
    $("pdf-task-tray")?.addEventListener("click", () => document.querySelector('[data-tab="export"]').click());
    document.addEventListener("documents-changed", source);
    document.addEventListener("document-forgotten", event => {
      if (choosing?.id === event.detail) {
        choosing = null; busy(false); selectedMode();
        status("Export cancelled because the source document was closed.");
      }
      if (active?.id === event.detail) {
        active = null; retry = null; completed = null;
        busy(false); selectedMode();
        status("Export cancelled because the source document was closed.");
      }
      if (retry?.id === event.detail) { retry = null; $("pdf-accept-limitations").hidden = true; }
      if (completed?.id === event.detail) {
        completed = null; $("pdf-open").hidden = $("pdf-reveal").hidden = true;
      }
    });
    source(); selectedMode();
    return {open: () => $("export-pdf").click()};
  };
})();
