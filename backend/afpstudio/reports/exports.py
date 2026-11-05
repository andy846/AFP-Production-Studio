"""CSV and Excel exports.

Both exporters write numbers and text that came out of a customer file,
so every cell is passed through :func:`guard_cell` first. A value that
starts with ``=``, ``+``, ``-`` or ``@`` is prefixed with an apostrophe
so a spreadsheet treats it as text instead of a formula. Without that,
a file name like ``=cmd|...`` would execute when an operator opened the
export.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Sequence

from .control_report import ControlReport
from .statistics import ProductionStatistics

#: Characters a spreadsheet treats as the start of a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def guard_cell(value: Any) -> Any:
    """Neutralise a value that a spreadsheet would treat as a formula."""
    if isinstance(value, str):
        stripped = value.lstrip()
        if value and (value[0] in FORMULA_PREFIXES or stripped[:1] in FORMULA_PREFIXES):
            return "'" + value
    return value


def _statistics_rows(stats: ProductionStatistics) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["AFP Production Studio — production statistics"],
        ["File", stats.filename],
        ["Path", stats.file_path],
        ["Size (bytes)", stats.file_size],
        ["Content digest", stats.file_digest],
        ["Preflight status", stats.preflight_status],
        [],
        ["Measure", "Value", "Basis", "Note"],
    ]
    for label, quantity in (
        ("Total AFP pages", stats.total_pages),
        ("Total documents", stats.total_documents),
        ("Total named page groups", stats.total_page_groups),
        ("Estimated sheets", stats.estimated_sheets),
        ("Mailpieces", stats.mailpieces),
        ("Dispatched", stats.dispatched),
    ):
        rows.append(
            [label, quantity.value, quantity.basis, quantity.note]
        )
    rows.append([])
    rows.append(
        ["Medium map", "Pages", "Paper stock", "Tray", "Copies"]
    )
    for usage in stats.medium_map_usage:
        rows.append(
            [
                usage.medium_map,
                usage.pages,
                usage.stock_name or "",
                usage.tray or "",
                usage.copies if usage.copies is not None else "",
            ]
        )
    rows.append([])
    rows.append(["Media transitions"])
    rows.append(["Page", "From", "To", "Offset"])
    for transition in stats.transitions:
        rows.append(
            [
                transition["page_number"],
                transition["from_map"] or "",
                transition["to_map"] or "",
                transition["offset"] if transition["offset"] is not None else "",
            ]
        )
    rows.append([])
    rows.append(["Preflight failures"])
    rows.append(["Code", "Status", "Page", "Offset", "Message"])
    for finding in stats.preflight_failures:
        rows.append(
            [
                finding["code"],
                finding["status"],
                finding["page"] if finding["page"] is not None else "",
                finding["offset"] if finding["offset"] is not None else "",
                finding["message"],
            ]
        )
    if stats.missing_resources:
        rows.append([])
        rows.append(["Missing resources"])
        rows.append(["Kind", "Name", "Codec"])
        for resource in stats.missing_resources:
            rows.append(
                [
                    resource.get("kind", ""),
                    resource.get("name", ""),
                    resource.get("codec") or "",
                ]
            )
    return rows


def _page_rows(stats: ProductionStatistics, pages: Sequence[Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [
        [
            "Page",
            "Document ID",
            "Page group",
            "Structured field offset",
            "Medium map",
            "Map source",
            "Media type",
            "Media destination",
            "Width (mm)",
            "Height (mm)",
            "Duplex",
            "Copies",
            "Resolved stock",
            "Resolved tray",
            "Resolution source",
            "Status",
        ]
    ]
    for page in pages:
        rows.append(
            [
                page.page_number,
                page.document_id,
                page.page_group or "",
                page.offset if page.offset is not None else "",
                page.medium_map or "",
                page.medium_map_source,
                page.requested.media_type or "",
                page.requested.media_destination or "",
                (
                    round(page.requested.width_mm, 2)
                    if page.requested.width_mm is not None
                    else ""
                ),
                (
                    round(page.requested.height_mm, 2)
                    if page.requested.height_mm is not None
                    else ""
                ),
                page.requested.duplex or "",
                page.requested.copies if page.requested.copies is not None else "",
                page.resolved.stock_name or "",
                page.resolved.tray or "",
                page.resolved.source,
                page.status,
            ]
        )
    return rows


def _control_rows(report: ControlReport) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["AFP Production Studio — production control report"],
        ["Job reference", report.job_reference],
        ["File", report.filename],
        ["Created", report.created],
        [],
        [
            "Stage",
            "Status",
            "Value",
            "Basis",
            "Unit",
            "Evidence",
            "Note",
        ],
    ]
    for row in report.reconciliation():
        rows.append(
            [
                row["label"],
                row["status"],
                row["value"] if row["value"] is not None else "",
                row["basis"],
                row["unit"],
                row["evidence_count"],
                row["note"],
            ]
        )
    rows.append([])
    rows.append(["Evidence"])
    rows.append(["Stage", "Source", "Reference", "Recorded", "Value", "Note"])
    for stage in report.stages.values():
        for evidence in stage.evidence:
            rows.append(
                [
                    stage.label,
                    evidence.source,
                    evidence.reference,
                    evidence.recorded,
                    evidence.value if evidence.value is not None else "",
                    evidence.note,
                ]
            )
    discrepancies = report.discrepancies()
    if discrepancies:
        rows.append([])
        rows.append(["Reconciliation differences"])
        rows.append(["Stage", "Expected", "Actual", "Difference", "Note"])
        for row in discrepancies:
            rows.append(
                [
                    row["label"],
                    row["expected"],
                    row["actual"],
                    row["difference"],
                    row["note"],
                ]
            )
    return rows


def export_csv(
    stats: ProductionStatistics,
    pages: Sequence[Any] = (),
    control: ControlReport | None = None,
    journal: dict | None = None,
) -> bytes:
    """Render the report as CSV bytes (UTF-8 with BOM for Excel)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in _statistics_rows(stats):
        writer.writerow([guard_cell(cell) for cell in row])
    if pages:
        buffer.write("\r\n")
        for row in _page_rows(stats, pages):
            writer.writerow([guard_cell(cell) for cell in row])
    if control is not None:
        buffer.write("\r\n")
        for row in _control_rows(control):
            writer.writerow([guard_cell(cell) for cell in row])
    if journal is not None:
        buffer.write("\r\n")
        for row in _journal_rows(journal):
            writer.writerow([guard_cell(cell) for cell in row])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def export_excel(
    stats: ProductionStatistics,
    pages: Sequence[Any] = (),
    control: ControlReport | None = None,
    journal: dict | None = None,
) -> bytes:
    """Render the report as an Excel workbook."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "The Excel export needs openpyxl. Install it with "
            "'pip install -r backend/requirements.txt'."
        ) from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Statistics"
    for row in _statistics_rows(stats):
        sheet.append([guard_cell(cell) for cell in row])

    if pages:
        page_sheet = workbook.create_sheet("Pages")
        for row in _page_rows(stats, pages):
            page_sheet.append([guard_cell(cell) for cell in row])
        page_sheet.freeze_panes = "A2"

    if control is not None:
        control_sheet = workbook.create_sheet("Control report")
        for row in _control_rows(control):
            control_sheet.append([guard_cell(cell) for cell in row])
    if journal is not None:
        journal_sheet = workbook.create_sheet("Production journal")
        for row in _journal_rows(journal):
            journal_sheet.append([guard_cell(cell) for cell in row])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _journal_rows(journal):
    yield ["Operator-reported external production evidence; identity not authenticated; actual media unknown"]
    yield ["AFP SHA-256", journal["job_sha256"]]
    yield ["Sign-off status", journal["signoff_status"]]
    yield ["Sequence", "Stage", "Unit", "Value", "Source", "Reference", "Operator", "Recorded UTC", "Note", "Event hash"]
    for event in journal["events"]:
        yield [event["sequence"], event["stage"], event["unit"], event["value"], event["source"], event["reference"],
               event["recorded_by"], event["recorded_at"], event.get("note", ""), event["event_hash"]]
