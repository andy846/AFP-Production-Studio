"""The HTTP API.

Every route here is local-only and every route that can change state
requires the CSRF token. File paths arrive from the browser, so they
are resolved and checked against the allow-list before anything is
opened, and an export can never be pointed at the AFP it is describing.
"""

from __future__ import annotations

import json
import hashlib
import uuid
from bisect import bisect_left
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request, session

from afpstudio.document_model.model import DocumentStore, SourceChangedError
from afpstudio.comparison import (Comparison, ComparisonError, collect, compare_rows,
                                  comparison_report, visual_difference)
from afpstudio.engineering.conversion import ConversionError
from afpstudio.document_model.structure import build_structure_index
from afpstudio.parser_adapters.alpheus import AlpheusAdapter
from afpstudio.parser_adapters.contract import AdapterError
from afpstudio.preflight.engine import STATUSES
from afpstudio.printer_profiles.schema import ProfileError
from afpstudio.reports.control_report import ControlReport
from afpstudio.reports.exports import export_csv, export_excel
from afpstudio.reports.statistics import build_statistics
from afpstudio.reports.folder import run_folder_report
from afpstudio.security.csrf import current_token, require_csrf
from afpstudio.security.paths import PathError, resolve_under_roots, resolve_selected_file, safe_output_path
from afpstudio.security.paths import atomic_write_bytes, resolve_report_folder
from afpstudio.viewer.resources import resource_dependencies
from afpstudio.viewer.jobs import JobQueueFull
from afpstudio.viewer.renderer import RenderError, TextSearchReplay
from afpstudio.engineering.writer import write_document
from afpstudio.engineering.conversion import convert_preview
from afpstudio.engineering.documents import combine_documents
from afpstudio.viewer.index import IndexingError
from afpstudio.reports.journal import JournalError
from afpstudio.document_model.mailpieces import build_mailpiece_index

bp = Blueprint("api", __name__, url_prefix="/api")


def _store() -> DocumentStore:
    return current_app.extensions["studio"]["documents"]


def _jobs():
    return current_app.extensions["studio"]["jobs"]


def _forget_document(document_id: str) -> bool:
    closed = _store().close(document_id)
    _jobs().discard_document(document_id)
    _comparisons().discard(document_id)
    return closed


def _comparisons():
    return current_app.extensions["studio"]["comparisons"]


def _profiles():
    return current_app.extensions["studio"]["profiles"]


def _config():
    return current_app.extensions["studio"]["config"]


def _adapter() -> AlpheusAdapter:
    return current_app.extensions["studio"]["alpheus"]


def _selected_profile(name: str | None):
    if not name:
        return None
    try:
        return _profiles().load(name)
    except ProfileError:
        return None


def _error(message: str, status: int = 400, code: str = "error"):
    return jsonify({"error": message, "code": code}), status


@bp.get("/health")
def health():
    """A tiny liveness probe that reveals nothing about the documents."""
    return jsonify(
        {
            "status": "ok",
            "documents": len(_store().list()),
            "jobs": len(_jobs().list()),
        }
    )


@bp.get("/csrf")
def csrf():
    """Hand the browser the CSRF token it must echo back."""
    token = current_token()
    if not token:
        from afpstudio.security.csrf import issue_token

        token = issue_token()
    return jsonify({"token": token})


@bp.get("/profiles")
def list_profiles():
    profiles = _profiles().load_all()
    return jsonify(
        {
            "profiles": [
                {
                    "name": key,
                    "printer_profile": profile.name,
                    "media_maps": sorted(profile.media_maps),
                    "rules": len(profile.rules),
                }
                for key, profile in sorted(profiles.items())
            ],
            "active": session.get("active_profile"),
        }
    )


@bp.get("/profiles/<name>")
def get_profile(name: str):
    try:
        profile = _profiles().load(name)
    except ProfileError as exc:
        return _error(str(exc), 404, "profile_not_found")
    return jsonify(profile.as_dict())


@bp.post("/profiles")
@require_csrf
def save_profile():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    if not name:
        return _error("A profile needs a name.", 400, "invalid_request")
    try:
        profile = _profiles().save(name, payload.get("profile", {}))
    except ProfileError as exc:
        return _error(str(exc), 400, "invalid_profile")
    return jsonify(profile.as_dict())


@bp.get("/parser")
def parser_status():
    """Is the optional Java parser available?"""
    return jsonify(_adapter().describe())


@bp.route("/workspace", methods=["GET", "POST"])
def workspace_settings():
    store = current_app.extensions["studio"]["workspace"]
    if request.method == "GET":
        try:
            return jsonify(store.snapshot())
        except OSError:
            return _error("Could not read workspace settings.", 500, "settings_failed")
    return _update_workspace()


@require_csrf
def _update_workspace():
    try:
        return jsonify(current_app.extensions["studio"]["workspace"].update(request.get_json(silent=True) or {}))
    except ValueError as exc:
        return _error(str(exc), 400, "invalid_request")
    except OSError:
        return _error("Could not save workspace settings.", 500, "settings_failed")


@bp.post("/workspace/recent")
@require_csrf
def workspace_recent_record():
    payload = request.get_json(silent=True) or {}
    if set(payload) - {"document_id", "page", "zoom"} or not isinstance(payload.get("document_id"), str):
        return _error("An open document is required.")
    doc = _store().get(payload["document_id"])
    if doc is None:
        return _error("Unknown document.", 404)
    try:
        return jsonify(current_app.extensions["studio"]["workspace"].record(
            doc, payload.get("page", 1), payload.get("zoom", 1)))
    except (ValueError, SourceChangedError) as exc:
        return _error(str(exc))
    except OSError:
        return _error("Could not save workspace settings.", 500, "settings_failed")


@bp.post("/documents/open")
@bp.post("/workspace/recent/open")
@require_csrf
def open_document():
    """Start indexing a local AFP file in the background."""
    payload = request.get_json(silent=True) or {}
    raw_path = payload.get("path")
    recent = None
    if request.path.endswith("/workspace/recent/open"):
        if set(payload) != {"id"}:
            return _error("A recent document ID is required.")
        try:
            recent = current_app.extensions["studio"]["workspace"].find(payload["id"])
        except OSError:
            return _error("Could not read recent files.", 500, "settings_failed")
        if recent is None:
            return _error("Recent document not found.", 404, "recent_not_found")
        raw_path = recent["path"]
    try:
        grant = payload.get("path_grant")
        if recent:
            path = resolve_selected_file(raw_path)
            if str(path) != raw_path:
                raise PathError("The selected path changed. Select the file again.")
        elif grant:
            path = current_app.extensions["studio"]["file_grants"].consume(grant, raw_path, "document")
        else:
            path = resolve_under_roots(raw_path, _config().allowed_roots())
    except PathError as exc:
        startup = current_app.extensions["studio"].get("startup_file")
        try:
            candidate = resolve_under_roots(raw_path, [startup.parent]) if startup and not payload.get("path_grant") else None
        except PathError:
            candidate = None
        if candidate is None or candidate != startup:
            return _error(str(exc), 400, "invalid_path")
        path = candidate

    config = _config()
    try:
        size = path.stat().st_size
    except OSError as exc:
        return _error(f"The file cannot be read: {exc}", 400, "invalid_path")
    if size > config.max_local_file_bytes:
        return _error(
            f"The file is larger than the {config.max_local_file_bytes} byte "
            "limit this build allows.",
            413,
            "file_too_large",
        )

    form_defs: list[Path] = []
    grants = payload.get("form_def_grants") or []
    form_inputs = payload.get("form_defs") or []
    if not isinstance(grants, list) or not isinstance(form_inputs, list):
        return _error("Form definitions and their grants must be lists.", 400, "invalid_request")
    for index, raw_form_def in enumerate(form_inputs):
        try:
            grant = grants[index] if index < len(grants) else None
            form_defs.append(
                current_app.extensions["studio"]["file_grants"].consume(grant, raw_form_def, "formdef")
                if grant else resolve_under_roots(raw_form_def, config.allowed_roots())
            )
        except PathError as exc:
            return _error(str(exc), 400, "invalid_path")

    profile = _selected_profile(payload.get("profile"))
    use_java = bool(payload.get("use_java_parser"))
    input_format = recent["input_format"] if recent else payload.get("input_format", "standard")
    if input_format not in ("standard", "rdw"):
        return _error("Unsupported input format.", 400, "invalid_request")
    if input_format == "rdw" and use_java:
        return _error("The current Java adapter cannot independently compare RDW input.", 400, "unsupported_comparison")
    # The job runs on a worker thread, outside the request's application
    # context, so everything it needs is captured here.
    store = _store()
    adapter = _adapter()

    def work(context):
        incomplete = False
        parser_name = "studio"
        adapter_result = None
        adapter_error: dict[str, Any] | None = None

        context.progress(0.05, "Reading the file structure.")
        if use_java:
            if adapter.available():
                try:
                    adapter_result = adapter.parse(
                        path, cancel=context._cancel
                    )
                    parser_name = "alpheus"
                except AdapterError as exc:
                    adapter_error = {
                        "kind": "adapter_error",
                        "code": exc.code,
                        "message": str(exc),
                    }
            else:
                adapter_error = {
                    "kind": "adapter_unavailable",
                    "code": "parser_unavailable",
                    "message": adapter.describe()["reason"],
                }

        session_doc = store.open(
            path,
            profile=profile,
            form_defs=form_defs,
            progress=lambda ratio: context.progress(
                0.1 + ratio * 0.85, "Indexing the document."
            ),
            adapter_result=adapter_result,
            adapter_error=adapter_error,
            incomplete=incomplete,
            parser=parser_name,
            check_cancelled=context.check_cancelled,
            input_format=input_format,
        )
        context.on_cancel(lambda: store.close(session_doc.document_id))
        return session_doc.summary()

    try:
        def protected_open(context):
            with store.reserve_sources((path, *form_defs)):
                return work(context)
        job = _jobs().submit("open-document", "Open document", protected_open)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.get("/jobs/<job_id>")
def job_status(job_id: str):
    job = _jobs().get(job_id)
    if job is None:
        return _error("Unknown job.", 404, "job_not_found")
    payload = job.as_dict()
    if job.state == "completed":
        payload["result"] = job.result
    return jsonify(payload)


@bp.post("/jobs/<job_id>/cancel")
@require_csrf
def cancel_job(job_id: str):
    if not _jobs().cancel(job_id):
        return _error("The job cannot be cancelled.", 409, "not_cancellable")
    return jsonify({"ok": True})


@bp.get("/documents")
def list_documents():
    return jsonify(
        {"documents": [doc.summary() for doc in _store().list()]}
    )


def _session_or_error(document_id: str):
    document = _store().get(document_id)
    if document is None:
        return None, _error(
            "Unknown document. Open it again.", 404, "document_not_found"
        )
    try:
        document.check_sources()
    except SourceChangedError as exc:
        _forget_document(document_id)
        return None, _error(str(exc), 409, "source_changed")
    return document, None


@bp.get("/documents/<document_id>")
def document_summary(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    return jsonify(document.summary())


@bp.delete("/documents/<document_id>")
@require_csrf
def close_document(document_id: str):
    if not _forget_document(document_id):
        return _error("Unknown document.", 404, "document_not_found")
    return jsonify({"ok": True})


@bp.get("/documents/<document_id>/page/<int:page>.svg")
def page_svg(document_id: str, page: int):
    document, error = _session_or_error(document_id)
    if error:
        return error
    if page < 0 or page >= document.page_count:
        return _error("No such page.", 404, "page_not_found")
    try:
        svg = document.page_svg(page, cache=_store().cache)
    except RenderError as exc:
        return _error(str(exc), 422, "render_failed")
    except (SourceChangedError, OSError) as exc:
        _forget_document(document_id)
        return _error(str(exc), 409, "source_changed")
    response = current_app.response_class(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/documents/<document_id>/text-runs")
@require_csrf
def positioned_text(document_id):
    document, error = _session_or_error(document_id)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    page, limit, offset = payload.get("page", 0), payload.get("limit", 100), payload.get("offset", 0)
    query = payload.get("q", "")
    if (type(page) is not int or not 0 <= page < document.index.page_count or
        type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0 or
        not isinstance(query, str) or len(query) > 256):
        return _error("Invalid page, limit, offset or query.")
    try:
        result = document.renderer.text_runs(page, codepage=payload.get("codepage"))
        document.check_sources()
    except ValueError as exc:
        return _error(str(exc))
    except RenderError as exc:
        return _error(str(exc), 422, "render_failed")
    except (SourceChangedError, OSError) as exc:
        _forget_document(document_id)
        return _error(str(exc), 409, "source_changed")
    rows = [item for item in result["items"] if query.casefold() in item["text"].casefold()]
    result.update(items=rows[offset:offset + limit], total=len(rows))
    return jsonify(result)


@bp.post("/documents/<document_id>/text-search")
@require_csrf
def search_preview_text(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    query = payload.get("q")
    codepage = payload.get("codepage")
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        return _error("Enter a search query of at most 256 characters.")
    if codepage not in ("cp037", "cp500"):
        return _error("Select a supported fallback code page.")
    needle = query.strip().casefold()
    total = document.page_count
    indexed = document.index.page_count
    store = _store()

    def work(context):
        items = []
        scanned = 0
        incomplete = total - indexed
        limited = False
        work_limited = False
        replay = TextSearchReplay(document.path, document.index)
        for page in range(indexed):
            context.progress(page / max(1, total), f"Searching page {page + 1} of {total}.")
            if store.get(document_id) is not document:
                raise SourceChangedError("The document was closed during search.")
            if not replay.allow_page(page):
                work_limited = True
                break
            try:
                result = document.renderer.text_runs(page, codepage=codepage, replay=replay)
            except RenderError:
                incomplete += 1
                continue
            scanned += 1
            if not result["complete"]:
                incomplete += 1
            for run in result["items"]:
                start = run["text"].casefold().find(needle)
                if start < 0:
                    continue
                if len(items) == 200:
                    limited = True
                    break
                snippet = run["text"][max(0, start - 60):start + len(query) + 60]
                items.append({"page": run["page"], "offset": run["offset"], "text": snippet})
            if limited:
                break
        document.check_sources()
        return {"items": items, "scanned_pages": scanned, "total_pages": total,
                "complete": not (incomplete or limited or work_limited or document.index.truncated),
                "incomplete_pages": incomplete, "limited": limited, "work_limited": work_limited}

    try:
        job = _jobs().submit("text-search", "Search preview text", work, document_id=document_id)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.get("/documents/<document_id>/fields")
def document_fields(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    page_arg = request.args.get("page")
    at = request.args.get("at_offset")
    focus = None
    try:
        page = int(page_arg) if page_arg not in (None, "") else None
        limit = min(int(request.args.get("limit", 2000)), 20000)
        offset = max(0, int(request.args.get("offset", 0)))
        if limit < 1 or (page is not None and page < 0):
            raise ValueError
        if at is not None:
            if page is not None or "offset" in request.args:
                raise ValueError
            focus = int(at)
            position = bisect_left(document.index.offsets, focus)
            if position >= document.index.field_count or document.index.offsets[position] != focus:
                raise ValueError
            offset = max(0, position - min(5, limit // 2))
    except ValueError:
        return _error("Invalid page, limit or offset.", 400, "invalid_request")
    rows = document.fields(page=page, limit=limit, offset=offset)
    payload = {"fields": [row.as_dict() for row in rows], "total": document.index.field_count}
    if focus is not None:
        payload["focused_offset"] = focus
    return jsonify(payload)


@bp.route("/documents/<document_id>/structure-index", methods=["GET", "POST"])
@require_csrf
def structure_index(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    params = request.get_json(silent=True) or {} if request.method == "POST" else request.args
    kind = params.get("kind", "tags")
    if kind not in ("documents", "page_groups", "tags", "warnings", "mailpieces"):
        return _error("Unknown index kind.", 400, "invalid_request")
    preview_codec = params.get("preview_codec") or None
    if preview_codec not in (None, "cp037", "cp500"):
        return _error("Unsupported preview codec.", 400, "invalid_request")
    raw_query = params.get("q", "")
    if not isinstance(raw_query, str):
        return _error("Query must be text.")
    query = raw_query.strip()
    if len(query) > 256 or (query and (kind != "tags" or preview_codec is None)):
        return _error("Text search requires a preview code page and a query of at most 256 characters.", 400, "invalid_request")
    try:
        limit = int(params.get("limit", 100))
        offset = int(params.get("offset", 0))
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError
    except (ValueError, TypeError):
        return _error("Invalid limit or offset.", 400, "invalid_request")
    try:
        if kind == "mailpieces":
            candidate = build_mailpiece_index(document, document.mailpiece_rule)
            result = {"mailpieces": candidate["items"], "complete": candidate["complete"],
                      "warnings": candidate["warnings"], "coverage": candidate["basis"]}
        else:
            result = build_structure_index(document.path, document.index, preview_codec=preview_codec)
        document.check_sources()
    except (SourceChangedError, OSError) as exc:
        _forget_document(document_id)
        return _error(str(exc), 409, "source_changed")
    rows = result[kind]
    if query:
        needle = query.casefold()
        rows = [row for row in rows if any(needle in item.get("preview", "").casefold() for item in row["triplets"])]
    return jsonify({"kind": kind, "items": rows[offset:offset + limit], "total": len(rows),
                    "complete": result["complete"], "coverage": result["coverage"],
                    "warnings": len(result["warnings"]), "warning_details": result["warnings"][:20]})


@bp.route("/documents/<document_id>/search", methods=["GET", "POST"])
@require_csrf
def search(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    params = (request.get_json(silent=True) or {}) if request.method == "POST" else request.args
    query = params.get("q", "")
    if not isinstance(query, str) or len(query) > 256:
        return _error("Query must be text of at most 256 characters.")
    if not query.strip():
        return jsonify({"structure": [], "text": []})
    structure = [row.as_dict() for row in document.search_structure(query)]
    text = document.search_text(query)
    return jsonify({"structure": structure, "text": text})


@bp.get("/documents/<document_id>/resource-dependencies")
def document_resource_dependencies(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    kind = request.args.get("kind", "references")
    if kind not in ("references", "definitions", "warnings"):
        return _error("Unknown resource index kind.", 400, "invalid_request")
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError
    except ValueError:
        return _error("Invalid limit or offset.", 400, "invalid_request")
    try:
        result = resource_dependencies(document.index, document.path, inventory=document.resource_inventory)
        document.check_sources()
    except (OSError, SourceChangedError) as exc:
        _forget_document(document_id)
        return _error(str(exc), 409, "source_changed")
    rows = result[kind]
    return jsonify({"kind": kind, "items": rows[offset:offset + limit], "total": len(rows),
                    "coverage": result["coverage"], "complete": result["complete"],
                    "warnings": len(result["warnings"])})


@bp.get("/documents/<document_id>/preflight")
def preflight(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    payload = document.preflight.as_dict()
    payload["statuses"] = list(STATUSES)
    return jsonify(payload)


@bp.get("/documents/<document_id>/report")
def report(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    stats = build_statistics(
        document.preflight,
        missing_resources=document.missing_resources,
        parsing_warnings=document.index.warnings,
    )
    control = ControlReport(
        job_reference=document.path.name, filename=document.path.name
    )
    control.set_afp_input(stats.total_pages)
    try:
        journal = _journal_report(document)
    except JournalError as exc:
        return _error(str(exc), 409, "journal_integrity")
    mailpieces = build_mailpiece_index(document, document.mailpiece_rule)
    return jsonify({"statistics": stats.as_dict(), "control_report": control.as_dict(), "production_journal": journal, "mailpiece_index": mailpieces})


def _journal_report(document):
    result = current_app.extensions["studio"]["journal"].report(document.preflight.file_digest)
    result["release_gate"]["media_preflight_status"] = document.preflight.status
    snapshot = _preflight_snapshot(document)
    result["preflight_snapshot"] = snapshot
    signoffs = [event for event in result["events"] if event["stage"] == "signoff"]
    if result["signoff_status"] == "operator_recorded" and signoffs[-1].get("preflight_snapshot") != snapshot:
        result["signoff_status"] = "preflight_snapshot_changed"
    if document.preflight.status != "pass" or not document.parse_complete:
        result["release_gate"]["eligible"] = False
        result["release_gate"]["reasons"].append("Current complete media preflight has not passed.")
    return result


def _preflight_snapshot(document):
    if not document.preflight_snapshot:
        hasher = hashlib.sha256(json.dumps(document.preflight.as_dict(), sort_keys=True).encode())
        for path in document.source_signatures:
            if path != document.path:
                with path.open("rb") as source:
                    hasher.update(hashlib.file_digest(source, "sha256").digest())
        document.preflight_snapshot = hasher.hexdigest()
    return document.preflight_snapshot


@bp.route("/documents/<document_id>/production-journal", methods=["GET", "POST"])
@require_csrf
def production_journal(document_id):
    document, error = _session_or_error(document_id)
    if error:
        return error
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return _error("Evidence must be a JSON object.")
            action = payload.pop("action", "evidence")
            journal = current_app.extensions["studio"]["journal"]
            if action == "signoff":
                journal.signoff(document.preflight.file_digest, payload,
                    preflight_status=document.preflight.status if document.parse_complete else "partial",
                    preflight_snapshot=_preflight_snapshot(document))
            elif action == "evidence":
                journal.append(document.preflight.file_digest, payload)
            else:
                return _error("Unknown journal action.")
        return jsonify(_journal_report(document))
    except JournalError as exc:
        return _error(str(exc), 409, "journal_validation")


@bp.post("/documents/<document_id>/export")
@require_csrf
def export_report(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    fmt = str(payload.get("format", "csv")).lower()
    raw_path = payload.get("path")
    if fmt not in ("csv", "xlsx"):
        return _error("Only 'csv' and 'xlsx' exports are supported.", 400, "bad_format")
    try:
        suffix = "." + fmt
        safe_output_path(raw_path, _config().allowed_roots(), source=document.path)
        final_path = Path(str(raw_path)).with_suffix(suffix) if raw_path else raw_path
        roots = _config().allowed_roots()
        target = safe_output_path(final_path, roots, source=document.path)
        for source in document.source_signatures:
            safe_output_path(target, roots, source=source)
        for opened in _store().list():
            for source in opened.source_signatures:
                safe_output_path(target, roots, source=source)
    except (PathError, ValueError) as exc:
        return _error(str(exc), 400, "invalid_path")
    if target.exists() and payload.get("overwrite") is not True:
        return _error("Output file already exists; confirmation required.", 409, "overwrite_required")

    stats = build_statistics(
        document.preflight,
        missing_resources=document.missing_resources,
        parsing_warnings=document.index.warnings,
    )
    control = ControlReport(
        job_reference=document.path.name, filename=document.path.name
    )
    control.set_afp_input(stats.total_pages)

    try:
        journal = _journal_report(document)
    except JournalError as exc:
        return _error(str(exc), 409, "journal_integrity")
    if fmt == "xlsx":
        data = export_excel(stats, document.preflight.pages, control, journal=journal)
        suffix = ".xlsx"
    elif fmt == "csv":
        data = export_csv(stats, document.preflight.pages, control, journal=journal)
        suffix = ".csv"
    else:
        return _error(
            "Only 'csv' and 'xlsx' exports are supported.", 400, "bad_format"
        )

    try:
        store = _store()
        atomic_write_bytes(target, data, overwrite=payload.get("overwrite") is True,
                           verify=document.check_sources, publisher=lambda action:
                           store.publish_output(target, roots, action, sources=document.source_signatures))
    except SourceChangedError as exc:
        _forget_document(document_id)
        return _error(str(exc), 409, "source_changed")
    except FileExistsError:
        return _error("Output appeared during generation; confirm replacement explicitly.", 409, "overwrite_required")
    except PathError as exc:
        return _error(str(exc), 400, "invalid_path")
    except OSError as exc:
        return _error(f"The report could not be written: {exc}", 500, "io_error")
    return jsonify({"path": str(target), "bytes": len(data)})


@bp.post("/documents/<document_id>/engineering")
@require_csrf
def engineering(document_id):
    document, error = _session_or_error(document_id)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    operation = payload.get("operation")
    if operation == "pdf":
        from .pdf import submit_pdf_export
        return submit_pdf_export(document, payload, legacy=True)
    if operation not in ("copy", "edit", "extract", "merge", "png", "pdf", "tiff"):
        return _error("Select a supported engineering operation.")
    additional_paths = []
    try:
        raw = payload.get("path")
        target = Path(str(raw)).with_suffix(".afp" if operation in ("copy", "edit", "extract", "merge") else "." + operation)
        safe_output_path(raw, _config().allowed_roots(), source=document.path)
        if operation == "merge":
            raw_paths = payload.get("merge_paths")
            if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= 19:
                return _error("Merge requires 1-19 additional file paths.")
            for raw_path in raw_paths:
                source = resolve_under_roots(raw_path, _config().allowed_roots())
                if source.stat().st_size > _config().max_local_file_bytes:
                    return _error("A source exceeds the configured byte limit.", 413)
                additional_paths.append(source)
                safe_output_path(target, _config().allowed_roots(), source=source)
        for source in document.source_signatures:
            target = safe_output_path(target, _config().allowed_roots(), source=source)
        for opened in _store().list():
            for source in opened.source_signatures:
                safe_output_path(target, _config().allowed_roots(), source=source)
    except (ValueError, PathError) as exc:
        return _error(str(exc), 400, "invalid_path")
    overwrite = payload.get("overwrite") is True
    if target.exists() and not overwrite:
        return _error("Output exists; explicit replacement confirmation is required.", 409, "overwrite_required")
    store = _store()
    output_roots = _config().allowed_roots()
    def work(context):
        context.progress(.05, "Preparing local output.")
        def publish(destination, action):
            if destination != target:
                raise PathError("Output location changed before publishing.")
            return context.publish(lambda: store.publish_output(
                destination, output_roots, action, sources=(*document.source_signatures, *additional_paths)))
        if operation in ("copy", "edit"):
            result = write_document(document, target, [] if operation == "copy" else payload.get("patches", []),
                overwrite=overwrite, check_cancelled=context.check_cancelled, publisher=publish)
        elif operation in ("merge", "extract"):
            temporary_sessions = []
            try:
                for path in additional_paths:
                    temporary_sessions.append(store.open(path, check_cancelled=context.check_cancelled))
                result = combine_documents([document] + temporary_sessions, target,
                    document_numbers=[payload.get("document_number", 1)] if operation == "extract" else None,
                    overwrite=overwrite, check_cancelled=context.check_cancelled, publisher=publish)
            finally:
                for session_doc in temporary_sessions:
                    store.close(session_doc.document_id)
        else:
            result = convert_preview(document, target, operation, payload.get("pages", [0]),
                dpi=payload.get("dpi", 144), overwrite=overwrite,
                check_cancelled=context.check_cancelled, publisher=publish)
        return result
    try:
        job = _jobs().submit("engineering", "Generate local output", work)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


def _check_comparison_sources(store, documents):
    for document in documents:
        if store.get(document.document_id) is not document:
            raise SourceChangedError("A comparison source is closed. Open both files again.")
        document.check_sources()


def _comparison(key):
    registry = _comparisons()
    with registry.lock:
        item = registry.items.get(key)
    if item is None:
        return None, _error("Comparison is no longer available.", 404, "comparison_not_found")
    try:
        _check_comparison_sources(_store(), item.documents)
    except SourceChangedError as exc:
        with registry.lock:
            registry.items.pop(key, None)
        return None, _error(str(exc), 409, "source_changed")
    return item, None


@bp.post("/comparisons")
@require_csrf
def start_comparison():
    body = request.get_json(silent=True) or {}
    ids = (body.get("left"), body.get("right"))
    codepage = body.get("codepage", "cp500")
    if any(not isinstance(x, str) for x in ids) or ids[0] == ids[1] or codepage not in ("cp500", "cp037"):
        return _error("Choose two different open documents and a supported code page.")
    documents = []
    for doc_id in ids:
        document, error = _session_or_error(doc_id)
        if error:
            return error
        documents.append(document)
    store, registry = _store(), _comparisons()
    key = uuid.uuid4().hex

    def work(context):
        def cleanup():
            with registry.lock:
                registry.items.pop(key, None)
        context.on_cancel(cleanup)
        evidence = []
        for side, document in enumerate(documents):
            _check_comparison_sources(store, documents)
            evidence.append(collect(document, codepage, lambda page, total:
                context.progress((side + page / max(1, total)) / 2,
                                 f"Indexing {'old' if side == 0 else 'new'} page {page + 1} of {total}.")))
        rows = compare_rows(*evidence, [])
        context.check_cancelled()
        with registry.lock:
            _check_comparison_sources(store, documents)
            registry.put(key, Comparison(tuple(documents), tuple(evidence), codepage, rows))
        return {"comparison_id": key}

    try:
        job = _jobs().submit("compare-afp", "Compare AFP documents", work, related_document_ids=ids)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.get("/comparisons/<key>")
def comparison_results(key):
    item, error = _comparison(key)
    if error:
        return error
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        return _error("Invalid result range.")
    mode = request.args.get("filter", "all")
    if mode not in ("all", "changed", "unmatched", "unverified", "text", "position", "size", "media", "visual"):
        return _error("Unknown comparison filter.")
    with _comparisons().lock:
        revision, anchors, rows = item.revision, item.anchors, item.rows
        visuals = dict(item.visual_results)
        image_row = item.visual[0] if item.visual else None

    def matches(i, row):
        changes = row["changes"]
        return (mode == "all" or mode == "changed" and (changes or visuals.get(i, {}).get("changed_pixels"))
                or mode == "unmatched" and (row["left"] is None or row["right"] is None)
                or mode == "unverified" and (row["unknown"] or i not in visuals)
                or mode in changes or mode == "media" and any("." in change for change in changes)
                or mode == "visual" and visuals.get(i, {}).get("changed_pixels"))

    filtered = [(i, row) for i, row in enumerate(rows) if matches(i, row)] if mode != "all" else None
    selected = filtered[offset:offset + limit] if filtered is not None else enumerate(rows[offset:offset + limit], offset)
    return jsonify({"sources": item.sources(), "revision": revision, "anchors": anchors,
                    "total": len(filtered) if filtered is not None else len(rows),
                    "total_rows": len(rows), "actual_media": "unknown",
                    "items": [dict(row, id=i, visual=visuals.get(i), image_ready=i == image_row)
                              for i, row in selected]})


@bp.post("/comparisons/<key>/anchors")
@require_csrf
def comparison_anchors(key):
    item, error = _comparison(key)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    anchors = body.get("anchors")
    if not isinstance(anchors, list) or len(anchors) > 1000 or any(
        not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair)
        for pair in anchors
    ):
        return _error("Provide up to 1,000 pairs of page numbers.")
    try:
        rows = compare_rows(*item.evidence, anchors)
    except ComparisonError as exc:
        return _error(str(exc))
    registry = _comparisons()
    with registry.lock:
        if registry.items.get(key) is not item or body.get("revision") != item.revision:
            return _error("Comparison changed. Refresh the results.", 409, "comparison_changed")
        item.anchors = sorted(anchors)
        item.rows = rows
        item.revision += 1
        item.visual = None
        item.visual_results = {}
        return jsonify({"revision": item.revision})


@bp.post("/comparisons/<key>/visual")
@require_csrf
def comparison_visual(key):
    item, error = _comparison(key)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    row_id = body.get("row")
    registry, store = _comparisons(), _store()
    with registry.lock:
        revision = item.revision
        if body.get("revision") != revision:
            return _error("Comparison changed. Refresh the results.", 409, "comparison_changed")
        if type(row_id) is not int or not 0 <= row_id < len(item.rows):
            return _error("Choose a comparison row.")
        row = item.rows[row_id]
    if not row["left"] or not row["right"]:
        return _error("Choose a row with pages on both sides.")
    if row["left_size"] != row["right_size"] or not row["left_size"] or None in row["left_size"]:
        return _error("Preview difference requires matching physical page dimensions.")

    def work(context):
        context.progress(.1, "Rendering preview difference.")
        _check_comparison_sources(store, item.documents)
        png, stats = visual_difference(*item.documents, row["left"], row["right"],
                                       row["left_size"], store.cache, context.check_cancelled)
        def commit():
            with registry.lock:
                _check_comparison_sources(store, item.documents)
                if registry.items.get(key) is not item or revision != item.revision:
                    raise ComparisonError("Comparison changed while rendering.")
                item.visual = (row_id, png)
                item.visual_results[row_id] = stats
        context.publish(commit)
        return stats

    try:
        job = _jobs().submit("compare-visual", "Render AFP preview difference", work,
                             related_document_ids=tuple(doc.document_id for doc in item.documents))
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.get("/comparisons/<key>/visual/<int:revision>/<int:row_id>.png")
def comparison_visual_image(key, revision, row_id):
    item, error = _comparison(key)
    if error:
        return error
    with _comparisons().lock:
        if revision != item.revision or not item.visual or item.visual[0] != row_id:
            return _error("Preview difference is no longer available.", 404)
        image = item.visual[1]
    return current_app.response_class(image, mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.post("/comparisons/<key>/export")
@require_csrf
def export_comparison(key):
    item, error = _comparison(key)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    fmt, raw_path = body.get("format"), body.get("path")
    if fmt not in ("csv", "xlsx") or not isinstance(raw_path, str) or not raw_path.strip():
        return _error("Choose a CSV or Excel output path.")
    roots, store, registry = _config().allowed_roots(), _store(), _comparisons()
    protected = {path for doc in item.documents for path in doc.source_signatures}
    try:
        safe_output_path(raw_path, roots)
        target = safe_output_path(Path(raw_path).with_suffix("." + fmt), roots)
        for path in protected:
            safe_output_path(raw_path, roots, source=path)
            safe_output_path(target, roots, source=path)
        overwrite = body.get("overwrite") is True
        if target.exists() and not overwrite:
            raise FileExistsError()
        with registry.lock:
            revision = item.revision
            if body.get("revision") != revision:
                raise ComparisonError("Comparison changed. Refresh the results.")
            rows, visuals = item.rows, dict(item.visual_results)
        data = comparison_report(item.sources(), rows, item.codepage, fmt,
                                 body.get("include_text") is True, visuals)

        def publish(action):
            with registry.lock:
                if registry.items.get(key) is not item or item.revision != revision:
                    raise ComparisonError("Comparison changed before export.")
                _check_comparison_sources(store, item.documents)
                store.publish_output(target, roots, action, sources=protected)

        atomic_write_bytes(target, data, overwrite=overwrite, publisher=publish)
    except FileExistsError:
        return _error("Output exists; explicit replacement confirmation is required.", 409, "overwrite_required")
    except (PathError, ValueError) as exc:
        return _error(str(exc), 400, "invalid_path")
    except SourceChangedError as exc:
        return _error(str(exc), 409, "source_changed")
    except OSError:
        return _error("Could not write the comparison report.", 500, "io_error")
    return jsonify({"path": str(target), "bytes": len(data)})


@bp.post("/reports/folder")
@require_csrf
def folder_production_report():
    payload = request.get_json(silent=True) or {}
    input_format = payload.get("input_format", "standard")
    if input_format not in ("standard", "rdw"):
        return _error("Select a supported AFP input format.")
    profile_name = payload.get("profile")
    if profile_name is not None and not isinstance(profile_name, str):
        return _error("Select a valid printer profile.")
    profile = _selected_profile(profile_name)
    if profile_name and profile is None:
        return _error("Unknown printer profile.")
    config = _config()
    roots = config.allowed_roots()
    overwrite = payload.get("overwrite") is True
    try:
        output = payload.get("output")
        if not isinstance(output, str) or Path(output.strip()).suffix.lower() != ".xlsx":
            return _error("Enter an Excel destination ending in .xlsx.")
        target = safe_output_path(output, roots)
        if target.suffix.lower() != ".xlsx" or not target.parent.is_dir() or target.is_dir():
            return _error("Choose an Excel file in an existing permitted directory.")
        if target.exists() and not overwrite:
            return _error("Output file already exists; confirmation required.", 409, "overwrite_required")
        grants = current_app.extensions["studio"]["file_grants"]
        raw = payload.get("folder")
        grant = payload.get("folder_grant")
        folder = grants.consume(grant, raw, "folder-report") if grant is not None else resolve_report_folder(raw, roots)
        form_defs = []
        if payload.get("form_def"):
            raw = payload["form_def"]
            grant = payload.get("form_def_grant")
            form_def = grants.consume(grant, raw, "formdef") if grant is not None else resolve_under_roots(raw, roots)
            if form_def.stat().st_size > config.max_local_file_bytes:
                return _error("The FormDef exceeds the configured byte limit.", 413)
            form_defs.append(form_def)
    except (OSError, PathError, ValueError, RuntimeError) as exc:
        return _error(str(exc), 400, "invalid_path")

    store = _store()
    try:
        job = _jobs().submit("folder-report", "Generate folder production report",
                             lambda context: run_folder_report(
                                 folder, target, roots, store, context, profile=profile, form_defs=form_defs,
                                 input_format=input_format, max_bytes=config.max_local_file_bytes, overwrite=overwrite))
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.post("/batch/preflight")
@require_csrf
def batch_preflight():
    payload = request.get_json(silent=True) or {}
    raw_paths = payload.get("paths")
    input_format = payload.get("input_format", "standard")
    if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= 20 or input_format not in ("standard", "rdw"):
        return _error("Batch accepts 1-20 paths and a supported input format.")
    config = _config()
    paths = []
    try:
        for raw in raw_paths:
            path = resolve_under_roots(raw, config.allowed_roots())
            if path.stat().st_size > config.max_local_file_bytes:
                return _error("A source exceeds the configured byte limit.", 413)
            paths.append(path)
    except (PathError, OSError) as exc:
        return _error(str(exc), 400, "invalid_path")
    profile = _selected_profile(payload.get("profile"))
    if payload.get("profile") and profile is None:
        return _error("Unknown printer profile.")
    store = _store()
    def work(context):
        rows = []
        for ordinal, path in enumerate(paths):
            context.progress(ordinal / len(paths), "Preflighting batch input.")
            document = None
            try:
                document = store.open(path, profile=profile, input_format=input_format,
                    progress=lambda ratio: context.progress((ordinal + ratio) / len(paths)),
                    check_cancelled=context.check_cancelled)
                rows.append({"filename": path.name, "status": document.preflight.status,
                    "indexed_pages": document.indexed_page_count, "parse_complete": document.parse_complete,
                    "missing_resources": len(document.missing_resources), "findings": len(document.preflight.findings),
                    "actual_media": "unknown", "comparison_status": "not_requested"})
            except (ValueError, IndexingError, OSError, SourceChangedError):
                rows.append({"filename": path.name, "status": "fail", "indexed_pages": 0,
                             "parse_complete": False, "error": "Input could not be parsed completely."})
            finally:
                if document:
                    store.close(document.document_id)
        from afpstudio.preflight.engine import worst
        return {"files": rows, "status": worst(row["status"] for row in rows),
                "indexed_pages": sum(row["indexed_pages"] for row in rows),
                "quantity_basis": "parsed" if all(row["parse_complete"] for row in rows) else "partial_parsed",
                "actual_production": "unknown"}
    try:
        job = _jobs().submit("batch-preflight", "Preflight local batch", work)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})
