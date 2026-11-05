"""Statistics, exports and the production control report."""

from __future__ import annotations

import csv
import io

from afpstudio.preflight.engine import run_preflight
from afpstudio.reports.control_report import ControlReport, STAGES
from afpstudio.reports.exports import export_csv, export_excel, guard_cell
from afpstudio.reports.statistics import (
    CALCULATED,
    PARSED,
    UNKNOWN,
    build_statistics,
    _estimate_sheets,
)
from afpstudio.testing import synthetic


def statistics_for(indexed, profile, data, name="job.afp"):
    path, index, analysis = indexed(name, data)
    preflight = run_preflight(path, index, analysis, profile=profile)
    return build_statistics(preflight), preflight


def test_parsed_quantities_are_labelled(indexed, permissive_profile):
    stats, _ = statistics_for(
        indexed, permissive_profile, synthetic.simplex_document(3)
    )
    assert stats.total_pages.value == 3
    assert stats.total_pages.basis == PARSED
    assert stats.total_documents.value == 1
    assert stats.total_documents.basis == PARSED


def test_sheet_count_is_calculated_not_parsed(indexed, permissive_profile):
    stats, _ = statistics_for(
        indexed, permissive_profile, synthetic.simplex_document(4)
    )
    assert stats.estimated_sheets.basis == CALCULATED
    assert stats.estimated_sheets.value == 4


def test_duplex_pages_share_a_sheet(indexed, permissive_profile):
    stats, _ = statistics_for(
        indexed, permissive_profile, synthetic.duplex_document(4)
    )
    assert stats.estimated_sheets.value == 2


def test_mixed_copy_counts_are_unknown(indexed, permissive_profile):
    _, preflight = statistics_for(indexed, permissive_profile, synthetic.duplex_document(4))
    preflight.pages[2].requested.copies = 3
    result = _estimate_sheets(preflight.pages)
    assert result.basis == UNKNOWN
    assert result.value is None


def test_mailpieces_and_dispatch_are_unknown(indexed, permissive_profile):
    stats, _ = statistics_for(
        indexed, permissive_profile, synthetic.simplex_document(3)
    )
    assert stats.mailpieces.value is None
    assert stats.mailpieces.basis == UNKNOWN
    assert stats.dispatched.value is None
    assert stats.dispatched.basis == UNKNOWN


def test_medium_map_usage_is_counted(indexed, permissive_profile):
    stats, _ = statistics_for(
        indexed, permissive_profile, synthetic.transition_document(4)
    )
    usage = {m.medium_map: m.pages for m in stats.medium_map_usage}
    assert usage == {"MAP01": 2, "MAP02": 2}


def test_csv_export_is_internally_consistent(indexed, permissive_profile):
    stats, preflight = statistics_for(
        indexed, permissive_profile, synthetic.transition_document(4)
    )
    data = export_csv(stats, preflight.pages)
    text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    flat = "\n".join(",".join(row) for row in rows)
    assert "Total AFP pages,4,parsed" in flat
    assert "MAP01" in flat
    assert "MAP02" in flat
    # The page table carries one row per page.
    assert flat.count("MAP01") >= 2
    assert flat.count("MAP02") >= 2


def test_excel_export_produces_a_workbook(indexed, permissive_profile):
    stats, preflight = statistics_for(
        indexed, permissive_profile, synthetic.simplex_document(2)
    )
    data = export_excel(stats, preflight.pages)
    assert data[:2] == b"PK"
    assert len(data) > 4000


def test_formula_injection_is_neutralised():
    assert guard_cell("=cmd|'/c calc'!A1").startswith("'")
    assert guard_cell("+1").startswith("'")
    assert guard_cell("@SUM(A1)").startswith("'")
    assert guard_cell("-2+3").startswith("'")
    assert guard_cell("normal text") == "normal text"
    assert guard_cell(42) == 42


def test_formula_injection_is_neutralised_in_the_csv(indexed, permissive_profile):
    stats, preflight = statistics_for(
        indexed, permissive_profile, synthetic.simplex_document(1)
    )
    preflight.pages[0].resolved.stock_name = "=HYPERLINK(\"http://x\")"
    data = export_csv(stats, preflight.pages).decode("utf-8-sig")
    assert "'=HYPERLINK" in data


def test_control_report_starts_unknown():
    report = ControlReport(job_reference="JOB1", filename="job.afp")
    rows = report.reconciliation()
    assert len(rows) == len(STAGES)
    for row in rows:
        assert row["value"] is None
        assert row["basis"] == UNKNOWN
        assert row["evidence_count"] == 0


def test_control_report_records_evidence():
    report = ControlReport(job_reference="JOB1", filename="job.afp")
    report.set_afp_input(
        type("Q", (), {"value": 100, "basis": PARSED, "note": ""})()
    )
    report.record("printing", 100, "RIP report", "RIP-2026-001")
    rows = {row["stage"]: row for row in report.reconciliation()}
    assert rows["afp_input"]["value"] == 100
    assert rows["printing"]["value"] == 100
    assert rows["printing"]["basis"] == "external"
    assert rows["printing"]["evidence_count"] == 1


def test_control_report_reports_differences():
    report = ControlReport(job_reference="JOB1", filename="job.afp")
    report.set_afp_input(
        type("Q", (), {"value": 100, "basis": PARSED, "note": ""})()
    )
    report.record("printing", 98, "RIP report", "RIP-2026-001", unit="pages")
    differences = report.discrepancies()
    assert differences
    assert differences[0]["difference"] == -2


def test_control_report_does_not_invent_evidence():
    report = ControlReport(job_reference="JOB1", filename="job.afp")
    report.set_afp_input(
        type("Q", (), {"value": 100, "basis": PARSED, "note": ""})()
    )
    assert report.discrepancies() == []
