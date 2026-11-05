"""The production control report.

This is a separate, auditable model from the production statistics.
Statistics describe the AFP. The control report describes the *job* as
it moves through the plant:

    AFP input -> printing / RIP -> reprints and rejects -> enveloping
              -> dispatch -> final release sign-off

Every stage starts as unknown. A stage only carries a number when
evidence has been attached to it, and the evidence is recorded with its
source so a reader can check it. Studio does not guess a reprint count
from a page count, and it does not treat "the job was sent to the
printer" as "the job was printed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .statistics import UNKNOWN, Quantity

#: The stages of the production chain, in order.
STAGES = (
    "afp_input",
    "printing",
    "reprints_rejects",
    "enveloping",
    "dispatch",
    "final_release",
)

STAGE_LABELS = {
    "afp_input": "AFP input",
    "printing": "Printing / RIP",
    "reprints_rejects": "Reprints and rejects",
    "enveloping": "Enveloping",
    "dispatch": "Dispatch",
    "final_release": "Final release sign-off",
}

NOT_STARTED = "not_started"
IN_PROGRESS = "in_progress"
COMPLETE = "complete"


@dataclass
class Evidence:
    """One piece of evidence backing a stage."""

    source: str
    reference: str
    recorded: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    value: int | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "reference": self.reference,
            "recorded": self.recorded,
            "value": self.value,
            "note": self.note,
        }


@dataclass
class ControlStage:
    """One stage of the production chain."""

    key: str
    label: str
    status: str = NOT_STARTED
    quantity: Quantity = field(
        default_factory=lambda: Quantity(
            None, UNKNOWN, "No production system has reported this stage yet."
        )
    )
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""
    unit: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "quantity": self.quantity.as_dict(),
            "evidence": [e.as_dict() for e in self.evidence],
            "note": self.note,
            "unit": self.unit,
        }


class ControlReport:
    """The auditable production control report."""

    def __init__(self, job_reference: str = "", filename: str = "") -> None:
        self.job_reference = job_reference
        self.filename = filename
        self.stages: dict[str, ControlStage] = {
            key: ControlStage(key=key, label=STAGE_LABELS[key]) for key in STAGES
        }
        self.created = datetime.now(timezone.utc).isoformat()
        self.notes: list[str] = []

    def stage(self, key: str) -> ControlStage:
        """Return a stage, raising when the key is not one of the stages."""
        if key not in self.stages:
            raise KeyError(f"Unknown production stage {key!r}.")
        return self.stages[key]

    def set_afp_input(self, quantity: Quantity) -> None:
        """Record what the AFP itself proves about the job."""
        stage = self.stages["afp_input"]
        stage.quantity = quantity
        stage.status = COMPLETE
        stage.unit = "pages"
        stage.evidence.append(
            Evidence(
                source="AFP Production Studio",
                reference=self.filename or self.job_reference,
                value=quantity.value,
                note="Parsed from the AFP file.",
            )
        )

    def record(
        self,
        key: str,
        value: int | None,
        source: str,
        reference: str,
        note: str = "",
        status: str = COMPLETE,
        unit: str = "unknown",
    ) -> ControlStage:
        """Attach a production figure from an external system."""
        stage = self.stage(key)
        stage.status = status
        if unit not in ("pages", "sheets", "mailpieces", "unknown"):
            raise ValueError("Unsupported production quantity unit.")
        stage.unit = unit
        stage.quantity = Quantity(
            value,
            "external",
            f"Reported by {source} ({reference}).",
        )
        stage.evidence.append(
            Evidence(
                source=source,
                reference=reference,
                value=value,
                note=note,
            )
        )
        return stage

    def reconciliation(self) -> list[dict[str, Any]]:
        """Compare the stages that carry a number.

        Stages without evidence are listed as unknown rather than being
        treated as zero, because "nobody reported it" and "it was zero"
        are very different statements in a production record.
        """
        rows: list[dict[str, Any]] = []
        for key in STAGES:
            stage = self.stages[key]
            rows.append(
                {
                    "stage": key,
                    "label": stage.label,
                    "status": stage.status,
                    "value": stage.quantity.value,
                    "basis": stage.quantity.basis,
                    "note": stage.quantity.note,
                    "evidence_count": len(stage.evidence),
                    "unit": stage.unit,
                }
            )
        return rows

    def discrepancies(self) -> list[dict[str, Any]]:
        """Find stages whose numbers do not line up with the input."""
        input_stage = self.stages["afp_input"]
        baseline = input_stage.quantity.value
        rows: list[dict[str, Any]] = []
        for key in STAGES:
            if key == "afp_input":
                continue
            stage = self.stages[key]
            value = stage.quantity.value
            if stage.unit == "unknown" or stage.unit != input_stage.unit:
                continue
            if value is None or baseline is None:
                continue
            if value != baseline:
                rows.append(
                    {
                        "stage": key,
                        "label": stage.label,
                        "expected": baseline,
                        "actual": value,
                        "difference": value - baseline,
                        "note": (
                            "The difference may be legitimate — reprints, "
                            "rejects or spoilage — and must be reconciled "
                            "against the recorded evidence."
                        ),
                    }
                )
        return rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_reference": self.job_reference,
            "filename": self.filename,
            "created": self.created,
            "stages": [
                self.stages[key].as_dict() for key in STAGES
            ],
            "reconciliation": self.reconciliation(),
            "discrepancies": self.discrepancies(),
            "notes": self.notes,
        }
