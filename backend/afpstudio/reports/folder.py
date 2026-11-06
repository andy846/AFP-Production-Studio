"""Read-only folder inventory and an evidence-labelled production workbook."""

import io
import os
import stat
from pathlib import Path

from afpstudio.document_model.mailpieces import build_mailpiece_index
from afpstudio.document_model.model import SourceChangedError, source_signature
from afpstudio.security.paths import atomic_write_bytes
from afpstudio.viewer.index import IndexingError
from afpstudio.viewer.renderer import RenderError
from .exports import guard_cell
from .statistics import build_statistics

MAX_FILES = 1000
MAX_ENTRIES = 50_000


def _inputs(folder, context):
    names = []
    with os.scandir(folder) as entries:
        for ordinal, entry in enumerate(entries, 1):
            if ordinal > MAX_ENTRIES:
                raise ValueError("Folder contains too many entries. Use a smaller folder.")
            context.check_cancelled()
            if Path(entry.name).suffix.lower() != ".afp":
                continue
            # Keep links and unreadable entries visible as unknown rows.
            try:
                if entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                pass
            names.append(entry.name)
            if len(names) > MAX_FILES:
                raise ValueError(f"Folder exceeds the {MAX_FILES:,} AFP file limit. No report was written.")
    if not names:
        raise ValueError("No .afp files found directly in this folder.")
    return sorted(names, key=lambda name: (name.casefold(), name))


def _row(name, path, folder, store, profile, form_defs, input_format, max_bytes, progress, check):
    row = dict(filename=name, pages=None, sheets=None, groups=None, candidates=None,
               status="Unreadable", notes="The AFP could not be read or parsed.")
    doc = None
    try:
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or
                getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or
                path.resolve(strict=True) != path or path.parent != folder):
            row["notes"] = "Skipped a link, redirected path or non-regular file."
            return row, {}
        if metadata.st_size > max_bytes:
            row["notes"] = "The AFP exceeds the configured file size limit."
            return row, {}
        doc = store.open(path, profile=profile, form_defs=form_defs, input_format=input_format,
                         progress=progress, check_cancelled=check)
        if not doc.parse_complete or not doc.indexed_page_count or doc.preflight.implicit_pages:
            row.update(status="Incomplete", notes="No complete bracketed AFP page count is available.")
            return row, doc.source_signatures
        quantities = build_statistics(doc.preflight)
        candidates = build_mailpiece_index(doc, doc.mailpiece_rule)
        doc.check_sources()
        row.update(pages=doc.indexed_page_count, sheets=quantities.estimated_sheets.value,
                   groups=len(doc.index.page_groups), candidates=candidates["count"], status="Parsed",
                   notes=quantities.estimated_sheets.note)
        if doc.mailpiece_rule and candidates["count"] is None:
            row["notes"] += " The configured mailpiece rule did not cover all pages."
        return row, doc.source_signatures
    except (IndexingError, OSError, RenderError, SourceChangedError, ValueError):
        return row, {}
    finally:
        if doc:
            store.close(doc.document_id)


def run_folder_report(folder, target, roots, store, context, *, profile, form_defs,
                      input_format, max_bytes, overwrite):
    context.progress(0, "Finding AFP files in the selected folder.")
    # A parent redirected after authorization must not be read.
    if folder.resolve(strict=True) != folder:
        raise ValueError("The selected folder changed. Select it again.")
    names = _inputs(folder, context)
    rows = []
    signatures = {path: source_signature(path) for path in form_defs}
    for index, name in enumerate(names):
        context.progress(.9 * index / len(names), f"Analysing file {index + 1} of {len(names)}.")
        row, observed = _row(name, folder / name, folder, store, profile, form_defs, input_format,
                             max_bytes, lambda ratio: context.progress(.9 * (index + ratio) / len(names)),
                             context.check_cancelled)
        rows.append(row)
        for source, signature in observed.items():
            if source in signatures and signatures[source] != signature:
                raise SourceChangedError("An AFP or FormDef changed during the report. Run it again.")
            signatures[source] = signature

    def publish(action):
        context.check_cancelled()
        if any(source_signature(path) != signature for path, signature in signatures.items()):
            raise SourceChangedError("An AFP or FormDef changed during the report. Run it again.")
        context.publish(lambda: store.publish_output(
            target, roots, action, sources=[*(folder / name for name in names), *form_defs]))

    totals = {}
    for key in ("pages", "sheets", "groups", "candidates"):
        known = [row[key] for row in rows if row[key] is not None]
        totals[key] = {"total": sum(known) if len(known) == len(rows) else None,
                       "subtotal": sum(known) if known else None, "known_files": len(known)}
    context.progress(.95, "Writing the Excel production report.")
    workbook = _workbook(rows, totals, folder.name, profile, form_defs, input_format)
    atomic_write_bytes(target, workbook, overwrite=overwrite, publisher=publish)
    return {"path": str(target), "files": len(rows),
            "incomplete_files": sum(row["status"] != "Parsed" for row in rows), "totals": totals}


def _workbook(rows, totals, folder_name, profile, form_defs, input_format):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    def cell(value):
        if isinstance(value, str):
            value = ILLEGAL_CHARACTERS_RE.sub("", value)
        return guard_cell("Unknown" if value is None else value)

    def headings(sheet, widths):
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        for item in sheet[1]:
            item.fill = PatternFill("solid", fgColor="126C5A")
            item.font = Font(color="FFFFFF", bold=True)
        sheet.freeze_panes = "B2"

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["Folder production report", "Value"])
    summary.append(["Folder", cell(folder_name)])
    summary.append(["AFP files", len(rows)])
    summary.append(["Files without a complete page count", len(rows) - totals["pages"]["known_files"]])
    for key, label in (("pages", "AFP pages"), ("sheets", "Estimated sheets"),
                       ("groups", "Named page groups (BNG)"), ("candidates", "Rule-based mailpiece candidates")):
        entry = totals[key]
        summary.append([label, cell(entry["total"])])
        summary.append([f"Known {label} subtotal", cell(entry["subtotal"])])
        summary.append([f"Files with known {label}", entry["known_files"]])
    summary.append(["Actual sheets printed", "Unknown"])
    summary.append(["Actual mailpieces", "Unknown"])
    summary.append(["Printer profile", cell(profile.name if profile else "None selected")])
    summary.append(["External FormDef", cell(form_defs[0].name if form_defs else "None selected")])
    summary.append(["Input format", input_format])
    summary.append(["Scope", "Direct .afp files only; subfolders are not included."])
    summary.append(["Sheet basis", "AFP duplex and copy controls when known; no RIP or printer evidence."])
    summary.append(["Group basis", "Counts each named BNG occurrence. A group is not a confirmed mailpiece."])
    summary.append(["Unknown totals", "A folder total is Unknown when any file is Unknown. The known subtotal is shown separately."])
    headings(summary, (44, 90))
    files = workbook.create_sheet("Files")
    files.append(["AFP filename", "AFP pages", "Estimated sheets", "Named page groups (BNG)",
                  "Rule-based mailpiece candidates", "Count status", "Notes"])
    for row in rows:
        files.append([cell(row[key]) for key in ("filename", "pages", "sheets", "groups", "candidates", "status", "notes")])
    headings(files, (36, 15, 19, 28, 35, 17, 82))
    files.auto_filter.ref = files.dimensions
    for (note,) in files.iter_rows(min_row=2, min_col=7, max_col=7):
        note.alignment = Alignment(wrap_text=True, vertical="top")
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
