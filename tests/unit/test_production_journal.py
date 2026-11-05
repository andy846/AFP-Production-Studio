import sqlite3

import pytest

from afpstudio.reports.journal import ProductionJournal, JournalError

JOB = "a" * 64


def evidence(stage, unit, value):
    return {"stage": stage, "unit": unit, "value": value, "source": "Synthetic UAT evidence",
            "reference": "TEST-001", "recorded_by": "UAT operator", "note": "Synthetic only"}


def test_persistent_evidence_does_not_compare_pages_with_sheets(tmp_path):
    path = tmp_path / "production.sqlite"
    journal = ProductionJournal(path)
    journal.append(JOB, evidence("printing", "sheets", 100))
    result = ProductionJournal(path).report(JOB)
    assert result["events"][0]["unit"] == "sheets"
    assert result["release_gate"]["eligible"] is False
    assert result["actual_media"] == "unknown"
    assert result["evidence_basis"] == "operator_reported_external_not_independently_verified"


def test_signoff_requires_preflight_and_reconciled_same_unit_evidence(tmp_path):
    journal = ProductionJournal(tmp_path / "ledger.sqlite")
    for stage, unit, value in (("expected", "sheets", 100), ("expected", "mailpieces", 75),
                               ("printing", "sheets", 100), ("enveloping", "mailpieces", 75),
                               ("dispatch", "mailpieces", 75), ("rejects", "sheets", 0), ("reprints", "sheets", 0)):
        journal.append(JOB, evidence(stage, unit, value))
    assert journal.report(JOB)["release_gate"]["eligible"] is True
    with pytest.raises(JournalError):
        journal.signoff(JOB, evidence("signoff", "unknown", None), preflight_status="unresolved")
    journal.signoff(JOB, evidence("signoff", "unknown", None), preflight_status="pass")
    assert journal.report(JOB)["signoff_status"] == "operator_recorded"
    journal.append(JOB, evidence("dispatch", "mailpieces", 74))
    result = journal.report(JOB)
    assert not result["release_gate"]["eligible"]
    assert result["signoff_status"] == "superseded_by_later_evidence"


def test_missing_evidence_and_chain_tampering_fail_closed(tmp_path):
    path = tmp_path / "ledger.sqlite"
    journal = ProductionJournal(path)
    bad = evidence("dispatch", "mailpieces", 1)
    bad["reference"] = ""
    with pytest.raises(JournalError):
        journal.append(JOB, bad)
    journal.append(JOB, evidence("dispatch", "mailpieces", 1))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET body='{}'")
    with pytest.raises(JournalError):
        journal.report(JOB)


@pytest.mark.parametrize("stage", ["rejects", "reprints"])
def test_reject_and_reprint_records_cannot_bypass_sheet_reconciliation(tmp_path, stage):
    journal = ProductionJournal(tmp_path / "ledger.sqlite")
    journal.append(JOB, evidence(stage, "sheets", 0))
    with pytest.raises(JournalError):
        journal.append(JOB, evidence(stage, "pages", 10))
    assert len(journal.report(JOB)["events"]) == 1
