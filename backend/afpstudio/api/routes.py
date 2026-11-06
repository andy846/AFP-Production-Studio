"""The HTTP API.

Every route here is local-only and every route that can change state
requires the CSRF token. File paths arrive from the browser, so they
are resolved and checked against the allow-list before anything is
opened, and an export can never be pointed at the AFP it is describing.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request, session

from afpstudio.document_model.model import DocumentStore, SourceChangedError
from afpstudio.document_model.structure import build_structure_index
from afpstudio.parser_adapters.alpheus import AlpheusAdapter
from afpstudio.parser_adapters.contract import AdapterError
from afpstudio.preflight.engine import STATUSES
from afpstudio.printer_profiles.schema import ProfileError
from afpstudio.reports.control_report import ControlReport
from afpstudio.reports.exports import export_csv, export_excel
from afpstudio.reports.statistics import build_statistics
from afpstudio.security.csrf import current_token, require_csrf
from afpstudio.security.paths import PathError, resolve_under_roots, resolve_selected_file, safe_output_path
from afpstudio.security.paths import atomic_write_bytes
from afpstudio.viewer.resources import resource_dependencies
from afpstudio.viewer.jobs import JobQueueFull
from afpstudio.viewer.renderer import RenderError, select_global_fields
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
        return jsonify(store.snapshot())
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
        recent = current_app.extensions["studio"]["workspace"].find(payload["id"])
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
        job = _jobs().submit("open-document", "Open document", work)
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
        _store().close(document_id)
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
    if not _store().close(document_id):
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
        _store().close(document_id)
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
        _store().close(document_id)
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
        shared = select_global_fields(document.index)
        for page in range(indexed):
            context.progress(page / max(1, total), f"Searching page {page + 1} of {total}.")
            if store.get(document_id) is not document:
                raise SourceChangedError("The document was closed during search.")
            try:
                result = document.renderer.text_runs(page, codepage=codepage, global_fields=shared)
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
                "complete": not (incomplete or limited or document.index.truncated),
                "incomplete_pages": incomplete, "limited": limited}

    try:
        job = _jobs().submit("text-search", "Search preview text", work)
    except JobQueueFull as exc:
        return _error(str(exc), 429, "job_queue_full")
    return jsonify({"job": job.as_dict()})


@bp.get("/documents/<document_id>/fields")
def document_fields(document_id: str):
    document, error = _session_or_error(document_id)
    if error:
        return error
    page_arg = request.args.get("page")
    try:
        page = int(page_arg) if page_arg not in (None, "") else None
        limit = min(int(request.args.get("limit", 2000)), 20000)
        offset = max(0, int(request.args.get("offset", 0)))
        if limit < 1 or (page is not None and page < 0):
            raise ValueError
    except ValueError:
        return _error("Invalid page, limit or offset.", 400, "invalid_request")
    rows = document.fields(page=page, limit=limit, offset=offset)
    return jsonify(
        {
            "fields": [row.as_dict() for row in rows],
            "total": document.index.field_count,
        }
    )


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
        _store().close(document_id)
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
        _store().close(document_id)
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
        def verify():
            document.check_sources()
            for source in document.source_signatures:
                if safe_output_path(target, roots, source=source) != target:
                    raise PathError("Output location changed before publishing.")
            for opened in _store().list():
                for source in opened.source_signatures:
                    safe_output_path(target, roots, source=source)
        atomic_write_bytes(target, data, overwrite=payload.get("overwrite") is True, verify=verify)
    except SourceChangedError as exc:
        _store().close(document_id)
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
        def publish(action):
            def guarded():
                for opened in store.list():
                    for source in opened.source_signatures:
                        safe_output_path(target, output_roots, source=source)
                return action()
            return context.publish(guarded)
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
