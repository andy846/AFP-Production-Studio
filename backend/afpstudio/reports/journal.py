"""Local append-only operator evidence journal with unit-aware reconciliation.

Hash chaining detects corruption, not a malicious administrator rewriting
the database. Actor names are declarations, not authenticated identities.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class JournalError(ValueError):
    pass


STAGES = {"expected", "printing", "rejects", "reprints", "enveloping", "dispatch", "reconciliation", "signoff"}


def _gate(events):
    latest = {(e["stage"], e["unit"]): e for e in events if e["stage"] not in ("signoff", "reconciliation")}
    reasons = []
    for stage, unit in (("printing", "sheets"), ("enveloping", "mailpieces"), ("dispatch", "mailpieces")):
        expected = latest.get(("expected", unit))
        observed = latest.get((stage, unit))
        if expected is None or observed is None:
            reasons.append(f"External expected and {stage} quantities in {unit} are required.")
        elif expected["value"] != observed["value"]:
            reasons.append(f"{stage} {unit} differ from the externally evidenced expectation.")
    for stage in ("rejects", "reprints"):
        item = latest.get((stage, "sheets"))
        if item is None:
            reasons.append(f"A reported {stage} sheet count is required, including an evidenced zero.")
        elif item["value"] and not any(e["stage"] == "reconciliation" and e["sequence"] > item["sequence"] for e in events):
            reasons.append(f"Nonzero {stage} require a later external reconciliation reference.")
    return {"eligible": not reasons, "reasons": reasons,
            "basis": "operator_reported_net_good_output_not_independently_verified"}


class ProductionJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (job TEXT NOT NULL, sequence INTEGER NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(job, sequence))")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _read(db, job):
        if not isinstance(job, str) or not re.fullmatch("[a-f0-9]{64}", job):
            raise JournalError("Job key must be the full source SHA-256.")
        rows = db.execute("SELECT sequence, previous_hash, event_hash, body FROM events WHERE job=? ORDER BY sequence LIMIT 10001", (job,)).fetchall()
        if len(rows) > 10000:
            raise JournalError("Journal event limit exceeded.")
        previous = "0" * 64
        events = []
        for ordinal, (sequence, prior, digest, body) in enumerate(rows, 1):
            expected = hashlib.sha256((previous + ":" + body).encode()).hexdigest()
            if sequence != ordinal or prior != previous or digest != expected:
                raise JournalError("Evidence journal integrity verification failed.")
            try:
                item = json.loads(body)
            except ValueError as exc:
                raise JournalError("Invalid journal event.") from exc
            item.update(sequence=sequence, event_hash=digest)
            events.append(item)
            previous = digest
        return events, previous

    def report(self, job):
        with self._connect() as db:
            events, _ = self._read(db, job)
        signoffs = [e for e in events if e["stage"] == "signoff"]
        status = ("operator_recorded" if signoffs and signoffs[-1]["sequence"] == len(events)
                  else "superseded_by_later_evidence" if signoffs else "not_recorded")
        return {"job_sha256": job, "events": events, "release_gate": _gate(events),
                "signoff_status": status, "actual_media": "unknown",
                "evidence_basis": "operator_reported_external_not_independently_verified",
                "identity_basis": "operator_declared_not_authenticated",
                "integrity_basis": "local_hash_chain_not_tamper_proof"}

    def append(self, job, payload):
        if not isinstance(payload, dict) or payload.get("stage") == "signoff":
            raise JournalError("Use the sign-off action for a release record.")
        return self._write(job, payload)

    def signoff(self, job, payload, *, preflight_status, preflight_snapshot=None):
        if preflight_status != "pass":
            raise JournalError("Current complete media preflight must pass before recording sign-off.")
        return self._write(job, {**payload, "stage": "signoff", "unit": "unknown", "value": None}, signoff=True, preflight_snapshot=preflight_snapshot)

    def _write(self, job, payload, signoff=False, preflight_snapshot=None):
        if set(payload) - {"stage", "unit", "value", "source", "reference", "recorded_by", "note"}:
            raise JournalError("Unknown evidence fields.")
        stage, unit, value = payload.get("stage"), payload.get("unit"), payload.get("value")
        if stage not in STAGES or unit not in ("pages", "sheets", "mailpieces", "unknown"):
            raise JournalError("Select a supported stage and an explicit quantity unit.")
        if stage in ("reconciliation", "signoff"):
            if unit != "unknown" or value is not None:
                raise JournalError("Reconciliation/sign-off records are references, not quantities.")
        elif unit == "unknown" or type(value) is not int or not 0 <= value <= 10**12:
            raise JournalError("Reported quantities require a non-negative integer and unit.")
        for key in ("source", "reference", "recorded_by"):
            if not isinstance(payload.get(key), str) or not payload[key].strip() or len(payload[key]) > 512:
                raise JournalError("Evidence source, reference and operator are required (maximum 512 characters each).")
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 2000:
            raise JournalError("Evidence note exceeds the 2000-character limit.")
        event = dict(payload)
        event["recorded_at"] = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            events, previous = self._read(db, job)
            if len(events) >= 10000 or db.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= 100000:
                raise JournalError("Journal capacity reached; archive it before adding evidence.")
            if signoff and not _gate(events)["eligible"]:
                raise JournalError("Production quantities are missing or unreconciled.")
            if signoff:
                event["signed_snapshot_hash"] = previous
                event["preflight_snapshot"] = preflight_snapshot
            body = json.dumps(event, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            digest = hashlib.sha256((previous + ":" + body).encode()).hexdigest()
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)", (job, len(events) + 1, previous, digest, body))
        return self.report(job)
