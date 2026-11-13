"""Document-owned PDF export and narrowly scoped desktop output actions."""
import os
from pathlib import Path
import re
import sys

from flask import Blueprint, jsonify, request

from .routes import _config, _error, _jobs, _session_or_error, _store
from afpstudio.document_model.model import source_signature
from afpstudio.security.csrf import require_csrf
from afpstudio.security.paths import PathError, safe_output_path
from afpstudio.viewer.jobs import JobQueueFull

bp = Blueprint("pdf_export", __name__, url_prefix="/api")


def selected_pages(payload, total, *, legacy=False):
    if legacy:
        pages = payload.get("pages", [0])
        if (not isinstance(pages, list) or not pages or
            any(type(p) is not int or not 0 <= p < total for p in pages) or len(set(pages)) != len(pages)):
            raise ValueError("Select distinct pages within the document.")
        return sorted(pages)
    mode = payload.get("selection", "all")
    if total < 1:
        raise ValueError("The document contains no pages to export.")
    if mode == "all":
        return None
    if mode == "current":
        page = payload.get("current_page")
        if type(page) is not int or not 1 <= page <= total:
            raise ValueError("Select a page within the document.")
        return [page - 1]
    if mode != "range":
        raise ValueError("Select all pages, the current page or a page range.")
    raw = payload.get("page_range")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        raise ValueError("Enter a page range such as 1-5,8,12-15.")
    selected = set()
    for part in raw.split(","):
        match = re.fullmatch(r"\s*([0-9]{1,10})(?:\s*-\s*([0-9]{1,10}))?\s*", part)
        if not match:
            raise ValueError("Enter a page range such as 1-5,8,12-15.")
        start = int(match[1])
        end = int(match[2] or match[1])
        if not 1 <= start <= end <= total:
            raise ValueError("Page ranges must be ascending and within the document.")
        selected.update(range(start - 1, end))
    return sorted(selected)


def submit_pdf_export(document, payload, *, legacy=False):
    from afpstudio.engineering.pdf_export import export_pdf
    try:
        pages = selected_pages(payload, document.page_count, legacy=legacy)
        dpi = payload.get("dpi", 144 if legacy else 150)
        if type(dpi) is not int or not 72 <= dpi <= 300:
            raise ValueError("Select a PDF resolution between 72 and 300 dpi.")
        for flag in ("overwrite", "allow_incomplete"):
            if flag in payload and type(payload[flag]) is not bool:
                raise ValueError("Invalid PDF confirmation option.")
        raw = payload.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise PathError("Choose an output PDF path.")
        roots = _config().allowed_roots()
        safe_output_path(raw, roots)
        target = Path(raw.strip()).expanduser().with_suffix(".pdf")
        target = safe_output_path(target, roots)
        for session in _store().list():
            for source in session.source_signatures:
                safe_output_path(target, roots, source=source)
        for source in document.source_signatures:
            safe_output_path(target, roots, source=source)
    except (ValueError, OSError) as exc:
        return _error(str(exc), 400, "invalid_request")
    overwrite = payload.get("overwrite", False)
    if target.exists() and not overwrite:
        return _error("Output exists; confirm replacement explicitly.", 409, "overwrite_required")
    store = _store()
    document_id = document.document_id
    def work(context):
        publishing = False
        def check():
            context.check_cancelled()
            if not publishing and store.get(document_id) is not document:
                raise PathError("The source document was closed. PDF export was cancelled.")
        def publish(destination, action):
            nonlocal publishing
            if destination != target:
                raise PathError("Output path changed before publishing.")
            check()
            # The store verifies ownership under its publication lock. The
            # writer's final cancellation callback must not reacquire that lock.
            publishing = True
            try:
                return context.publish(lambda: store.publish_output(
                    destination, roots, action, sources=document.source_signatures,
                    expected_document=document))
            finally:
                publishing = False
        total = document.page_count if pages is None else len(pages)
        def progress(fraction):
            context.progress(fraction, f"Wrote page {round(fraction * total)} of {total}.")
        check()
        result = export_pdf(document, target, pages, dpi=dpi, overwrite=overwrite,
                            allow_incomplete=payload.get("allow_incomplete", False),
                            check_cancelled=check, progress=progress, publisher=publish)
        result["output_signature"] = source_signature(target)
        return result
    try:
        job = _jobs().submit("pdf-export", "Export PDF", work, document_id=document_id,
                             timeout_seconds=_config().pdf_timeout_seconds)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.post("/documents/<document_id>/pdf-export")
@require_csrf
def create_pdf_export(document_id):
    document, error = _session_or_error(document_id)
    if error:
        return error
    return submit_pdf_export(document, request.get_json(silent=True) or {})


def _launch_output(path, action):
    if sys.platform != "win32":
        raise OSError("Desktop output actions require Windows.")
    os.startfile(str(path if action == "open" else path.parent))


@bp.post("/jobs/<job_id>/output-action")
@require_csrf
def output_action(job_id):
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    if action not in ("open", "reveal"):
        return _error("Select Open PDF or Show in folder.")
    job = _jobs().get(job_id)
    if not job or job.state != "completed" or not isinstance(job.result, dict) or job.result.get("format") != "pdf":
        return _error("Completed PDF output is unavailable.", 404, "output_unavailable")
    try:
        path = Path(job.result["path"])
        if path.suffix.lower() != ".pdf" or list(source_signature(path)) != list(job.result.get("output_signature", [])):
            return _error("The PDF output has changed. Export it again.", 409, "output_changed")
        with path.open("rb") as stream:
            if not stream.read(8).startswith(b"%PDF-"):
                return _error("The output is not a PDF.", 409, "output_changed")
        _launch_output(path, action)
    except (OSError, ValueError, RuntimeError):
        return _error("The output is unavailable or could not be opened.", 409, "output_unavailable")
    return jsonify({"ok": True})
