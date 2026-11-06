"use strict";

window.initCompareWorkbench = function ({api, nativeApi, pickSavePath, documents, locate}) {
  const $ = id => document.getElementById(id);
  const el = (tag, text) => Object.assign(document.createElement(tag), {textContent: text});
  const pair = ["left", "right"];
  const limit = 50;
  const status = text => { $("compare-status").textContent = text; };
  const stage = side => $("compare-stage-" + side);
  let id = null, jobId = null, busy = false, revision = 0, offset = 0, selected = null;
  let sources = [], anchors = [], items = [], workSources = [];
  let generation = 0, refreshRequest = 0;
  const zoom = {left: 1, right: 1};
  const widths = {left: null, right: null};
  const syncedScroll = new Map();
  const url = () => "/api/comparisons/" + id;
  const post = (path, body) => api(path, {method: "POST", body});

  function syncFiles() {
    const files = documents();
    const ids = new Set(files.map(doc => doc.document_id));
    for (const [index, side] of pair.entries()) {
      const field = $("compare-" + side);
      const previous = field.value;
      field.replaceChildren(...files.map(doc => {
        const option = el("option", doc.filename);
        option.value = doc.document_id;
        option.title = doc.path;
        return option;
      }));
      field.value = ids.has(previous) ? previous : files[index]?.document_id || "";
    }
    $("compare-run").disabled = busy || files.length < 2;
    if ([...workSources, ...sources.map(doc => doc.id)].some(key => !ids.has(key))) {
      invalidate("A source file was closed. Open both files to compare again.");
    }
  }

  function invalidate(message) {
    generation++;
    id = null;
    workSources = [];
    sources = [];
    selected = null;
    items = [];
    $("compare-results").hidden = true;
    $("compare-rows").replaceChildren();
    for (const side of pair) stage(side).replaceChildren();
    clearOverlay();
    status(message);
  }

  function clearOverlay() {
    $("compare-overlay").hidden = true;
    $("compare-overlay-image").removeAttribute("src");
  }

  function adjustZoom() {
    const known = Object.values(widths).filter(width => width > 0);
    const maxWidth = Math.max(...known);
    const synced = $("compare-sync").checked;
    const base = Math.max(1, Math.min(...pair.map(side => stage(side).clientWidth - 20)));
    for (const side of pair) {
      const holder = stage(side);
      const img = holder.querySelector("img");
      const ratio = widths[side] > 0 && synced ? widths[side] / maxWidth : 1;
      if (img) img.style.width = Math.round((synced ? base : Math.max(1, holder.clientWidth - 20)) *
                                          zoom[side] * ratio) + "px";
    }
  }

  function showSide(side, page, size) {
    const holder = stage(side);
    const source = sources[side === "left" ? 0 : 1];
    $("compare-page-" + side).value = page ?? "";
    $("compare-page-" + side).max = source.pages;
    widths[side] = size?.[0] ?? null;
    if (!page) { holder.replaceChildren(el("p", "No paired page")); return; }
    const img = el("img", "");
    img.alt = (side === "left" ? "Old" : "New") + " page " + page;
    img.addEventListener("error", () => {
      if (img.isConnected) holder.replaceChildren(el("p", "Preview unavailable. Check the source file."));
    }, {once: true});
    holder.replaceChildren(img);
    img.src = "/api/documents/" + source.id + "/page/" + (page - 1) + ".svg";
    holder.scrollTop = holder.scrollLeft = 0;
    adjustZoom();
  }

  function details(row) {
    const root = $("compare-row-detail");
    const lines = [];
    for (const change of row.changes.filter(value => value.includes("."))) {
      const [kind, key] = change.split(".");
      lines.push(kind + " " + key + ": " + (row.left_media[kind]?.[key] ?? "Unknown") +
                 " → " + (row.right_media[kind]?.[key] ?? "Unknown"));
    }
    if (row.changes.includes("size")) lines.push("Page size: " + row.left_size.join(" × ") + " → " + row.right_size.join(" × ") + " mm");
    if (row.changes.includes("text")) lines.push("Preview excerpts: " + row.left_excerpt + " → " + row.right_excerpt);
    if (row.unknown.length) lines.push("Unverified: " + row.unknown.join(", "));
    if (!row.visual) lines.push("Preview pixels have not been compared for this pair.");
    root.replaceChildren(el("p", lines.join("\n") || "No differences in the checked fields."));
    for (const [index, side] of pair.entries()) {
      if (row[side] == null) continue;
      const button = el("button", "Locate " + (index ? "new" : "old") + " page" +
                        (row[side + "_offset"] == null ? "" : " · offset " + row[side + "_offset"]));
      button.type = "button";
      button.addEventListener("click", () => locate(sources[index].id, row[side], row[side + "_offset"]));
      root.append(button);
    }
  }

  function choose(row) {
    selected = row;
    clearOverlay();
    for (const side of pair) {
      showSide(side, row[side], row[side + "_size"]);
      $("compare-anchor-" + side).value = row[side] ?? "";
    }
    $("compare-visual").disabled = !row.left || !row.right ||
      !row.left_size?.every(value => value > 0) ||
      JSON.stringify(row.left_size) !== JSON.stringify(row.right_size);
    $("compare-rows").querySelectorAll("button").forEach(button =>
      button.setAttribute("aria-current", String(Number(button.dataset.row) === row.id)));
    details(row);
    if (row.image_ready) showVisual(row.id, row.visual);
  }

  function showVisual(rowId, data) {
    $("compare-overlay").hidden = false;
    $("compare-overlay-caption").textContent = data.changed_pixels + " of " + data.total_pixels +
      " preview pixels changed at " + data.dpi + " dpi.";
    $("compare-overlay-image").src = url() + "/visual/" + revision + "/" + rowId + ".png";
  }

  async function refresh(preferred) {
    if (!id) return;
    const epoch = generation;
    const request = ++refreshRequest;
    const data = await api(url() + "?limit=" + limit + "&offset=" + offset +
                           "&filter=" + encodeURIComponent($("compare-filter").value));
    if (epoch !== generation || request !== refreshRequest) return;
    sources = data.sources;
    revision = data.revision;
    anchors = data.anchors;
    items = data.items;
    const list = $("compare-rows");
    list.replaceChildren();
    const basis = {original: "Original page", text: "Decoded text", manual: "Manual", review: "Review pairing",
                   left_only: "Old only", right_only: "New only"};
    for (const row of items) {
      const summary = [...row.changes, ...(row.visual?.changed_pixels ? ["preview pixels"] : []), ...(row.unknown.length ? ["unverified"] : [])];
      const button = el("button", (row.left ?? "—") + " ↔ " + (row.right ?? "—") + " · " +
                        basis[row.basis] + (summary.length ? " · " + summary.join(", ") : ""));
      button.type = "button";
      button.dataset.row = row.id;
      button.addEventListener("click", () => choose(row));
      list.append(button);
    }
    $("compare-range").textContent = (data.total ? offset + 1 : 0) + "–" +
      Math.min(offset + limit, data.total) + " of " + data.total;
    $("compare-prev").disabled = offset === 0;
    $("compare-next").disabled = offset + limit >= data.total;
    $("compare-results").hidden = false;
    $("compare-anchors").replaceChildren(...anchors.map(([a, b]) => {
      const button = el("button", "Remove " + a + " ↔ " + b);
      button.type = "button";
      button.addEventListener("click", () => applyAnchors(anchors.filter(pair => pair[0] !== a)));
      return button;
    }));
    const incomplete = sources.some(doc => !doc.indexed_complete || doc.pages > doc.covered_pages);
    status(data.total_rows + " comparison rows." + (incomplete ? " At least one source has incomplete page coverage." : ""));
    if (items.length) choose(items.find(row => row.id === preferred) || items[0]);
    else {
      selected = null;
      clearOverlay();
      $("compare-visual").disabled = true;
      $("compare-row-detail").textContent = "No rows match this filter.";
      for (const side of pair) showSide(side, null, null);
    }
  }

  async function runJob(path, body, done) {
    if (busy) return;
    busy = true;
    $("compare-run").disabled = true;
    const epoch = generation;
    status("Preparing comparison…");
    try {
      const result = await post(path, body);
      jobId = result.job.id;
      $("compare-cancel").disabled = false;
      while (epoch === generation) {
        const job = await api("/api/jobs/" + jobId);
        if (epoch !== generation) break;
        status(job.error || job.message || "Working…");
        if (job.state === "completed") { await done(job.result); break; }
        if (job.state === "failed" || job.state === "cancelled") break;
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    } catch (err) { if (epoch === generation) showError(err); }
    finally {
      jobId = null;
      workSources = [];
      busy = false;
      $("compare-cancel").disabled = true;
      $("compare-run").disabled = documents().length < 2;
    }
  }

  function showError(err) {
    if (["source_changed", "comparison_not_found"].includes(err.code)) invalidate(err.message);
    else status(err.message);
  }

  async function applyAnchors(next) {
    if (!id || busy) return;
    try {
      await post(url() + "/anchors", {anchors: next, revision});
      offset = 0;
      await refresh();
    } catch (err) { showError(err); }
  }

  function confirmOverwrite(path) {
    const dialog = $("overwrite-dialog");
    $("overwrite-path").textContent = path;
    return new Promise(resolve => {
      const finish = allowed => { dialog.close(); resolve(allowed); };
      $("overwrite-confirm").onclick = () => finish(true);
      $("overwrite-cancel").onclick = () => finish(false);
      dialog.oncancel = event => { event.preventDefault(); finish(false); };
      dialog.showModal();
    });
  }

  async function exportReport(format) {
    if (!id || busy) return;
    const destination = url() + "/export";
    const reportRevision = revision;
    let path = $("compare-output").value.trim();
    if (!path && nativeApi()) path = await pickSavePath("AFP comparison." + format, format);
    if (!path) { status("Choose a report output path."); return; }
    const body = {path, format, revision: reportRevision, include_text: $("compare-include-text").checked};
    try {
      let result;
      try { result = await post(destination, body); }
      catch (err) {
        if (err.code !== "overwrite_required") throw err;
        if (!await confirmOverwrite(path.replace(/\.[^./\\]+$/, "") + "." + format)) return;
        result = await post(destination, {...body, overwrite: true});
      }
      status("Report written to " + result.path);
    } catch (err) { showError(err); }
  }

  $("compare-run").addEventListener("click", () => {
    const [left, right] = pair.map(side => $("compare-" + side).value);
    if (!left || !right || left === right) { status("Choose two different AFP files."); return; }
    invalidate("");
    workSources = [left, right];
    offset = 0;
    $("compare-filter").value = "all";
    runJob("/api/comparisons", {left, right, codepage: $("compare-codepage").value}, async result => {
      id = result.comparison_id;
      await refresh();
    });
  });
  $("compare-cancel").addEventListener("click", async () => {
    if (!jobId) return;
    try { await post("/api/jobs/" + jobId + "/cancel", {}); }
    catch (err) { showError(err); }
  });
  for (const [control, delta] of [["compare-prev", -limit], ["compare-next", limit]]) {
    $(control).addEventListener("click", () => { offset = Math.max(0, offset + delta); refresh().catch(showError); });
  }
  $("compare-filter").addEventListener("change", () => { offset = 0; refresh().catch(showError); });
  $("compare-anchor-add").addEventListener("click", () => {
    const [a, b] = pair.map(side => Number($("compare-anchor-" + side).value));
    if (a < 1 || b < 1) { status("Enter a page number on each side."); return; }
    applyAnchors([...anchors.filter(([x, y]) => x !== a && y !== b), [a, b]]);
  });
  $("compare-visual").addEventListener("click", () => {
    if (!selected || !id || busy) return;
    const row = selected.id, at = revision;
    runJob(url() + "/visual", {row, revision: at}, async () => {
      await refresh(selected?.id);
    });
  });
  $("compare-csv").addEventListener("click", () => exportReport("csv"));
  $("compare-xlsx").addEventListener("click", () => exportReport("xlsx"));
  $("compare-sync").addEventListener("change", event => {
    for (const side of pair) $("compare-page-" + side).disabled = event.target.checked;
    if (event.target.checked) {
      zoom.right = zoom.left;
      if (selected) choose(selected);
    }
    adjustZoom();
  });
  for (const [index, side] of pair.entries()) {
    $("compare-page-" + side).addEventListener("change", event => {
      const page = Number(event.target.value);
      if (Number.isInteger(page) && page >= 1 && page <= sources[index]?.pages) {
        clearOverlay();
        showSide(side, page, null);
      }
    });
    for (const [kind, scale] of [["in", 1.25], ["out", .8]]) {
      $("compare-zoom-" + kind + "-" + side).addEventListener("click", () => {
        zoom[side] = Math.min(6, Math.max(.25, zoom[side] * scale));
        if ($("compare-sync").checked) zoom[pair[1 - index]] = zoom[side];
        adjustZoom();
      });
    }
    const holder = stage(side);
    holder.addEventListener("scroll", () => {
      if (!$("compare-sync").checked) return;
      const previous = syncedScroll.get(holder);
      syncedScroll.delete(holder);
      if (previous && previous[0] === holder.scrollLeft && previous[1] === holder.scrollTop) return;
      const other = stage(pair[1 - index]);
      other.scrollLeft = holder.scrollLeft;
      other.scrollTop = holder.scrollTop;
      syncedScroll.set(other, [other.scrollLeft, other.scrollTop]);
    }, {passive: true});
  }
  const observer = new ResizeObserver(adjustZoom);
  pair.forEach(side => observer.observe(stage(side)));
  document.addEventListener("documents-changed", syncFiles);
  setInterval(async () => {
    if (!id || document.querySelector('[data-panel="compare"]').hidden) return;
    const epoch = generation;
    try { await api(url() + "?limit=1"); }
    catch (err) { if (epoch === generation) showError(err); }
  }, 10000);
  syncFiles();
};
