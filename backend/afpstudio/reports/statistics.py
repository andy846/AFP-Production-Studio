"""Production statistics.

Every number in the report carries a *basis*, so nobody has to guess
where it came from:

``parsed``
    Read directly out of the AFP. The page count, the document count and
    the medium map usage are parsed quantities.
``calculated``
    Derived from parsed quantities under a stated assumption. The
    estimated sheet count is a calculated quantity: it assumes duplex
    pages pair up two to a sheet and that a page group does not restart
    the pairing.
``unknown``
    The AFP cannot answer it. Physical sheets for a job with an
    undeterminable duplex setting fall into this bucket.
``external``
    Comes from outside the AFP — a printer report, a dispatch system.
    Studio has no such feed yet, so nothing is reported as external.

The report never turns a page count into a mailpiece count or a
dispatched count. Those are production facts, not document facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from afpstudio.preflight.engine import PreflightResult
from afpstudio.preflight.rules import Transition

PARSED = "parsed"
CALCULATED = "calculated"
UNKNOWN = "unknown"
EXTERNAL = "external"


@dataclass
class Quantity:
    """A number and where it came from."""

    value: int | None
    basis: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "basis": self.basis,
            "note": self.note,
        }


@dataclass
class MediumMapUsage:
    """How many pages used one medium map."""

    medium_map: str
    pages: int
    stock_name: str | None = None
    tray: str | None = None
    copies: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "medium_map": self.medium_map,
            "pages": self.pages,
            "stock_name": self.stock_name,
            "tray": self.tray,
            "copies": self.copies,
        }


@dataclass
class ProductionStatistics:
    """Everything the production report shows."""

    filename: str
    file_path: str
    file_size: int
    file_digest: str
    preflight_status: str = "unknown"
    total_pages: Quantity = field(
        default_factory=lambda: Quantity(None, UNKNOWN)
    )
    total_documents: Quantity = field(
        default_factory=lambda: Quantity(None, UNKNOWN)
    )
    total_page_groups: Quantity = field(
        default_factory=lambda: Quantity(None, UNKNOWN)
    )
    estimated_sheets: Quantity = field(
        default_factory=lambda: Quantity(None, UNKNOWN)
    )
    medium_map_usage: list[MediumMapUsage] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    missing_resources: list[dict[str, Any]] = field(default_factory=list)
    parsing_warnings: list[dict[str, Any]] = field(default_factory=list)
    preflight_failures: list[dict[str, Any]] = field(default_factory=list)
    unresolved_pages: int = 0
    mailpieces: Quantity = field(
        default_factory=lambda: Quantity(
            None,
            UNKNOWN,
            "Mailpieces are a production fact and cannot be derived from "
            "an AFP file.",
        )
    )
    dispatched: Quantity = field(
        default_factory=lambda: Quantity(
            None,
            UNKNOWN,
            "Dispatched quantities come from the dispatch system, not "
            "from the AFP.",
        )
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "file_digest": self.file_digest,
            "preflight_status": self.preflight_status,
            "total_pages": self.total_pages.as_dict(),
            "total_documents": self.total_documents.as_dict(),
            "total_page_groups": self.total_page_groups.as_dict(),
            "estimated_sheets": self.estimated_sheets.as_dict(),
            "mailpieces": self.mailpieces.as_dict(),
            "dispatched": self.dispatched.as_dict(),
            "medium_map_usage": [m.as_dict() for m in self.medium_map_usage],
            "transitions": self.transitions,
            "missing_resources": self.missing_resources,
            "parsing_warnings": self.parsing_warnings,
            "preflight_failures": self.preflight_failures,
            "unresolved_pages": self.unresolved_pages,
        }


def _estimate_sheets(pages: list[Any]) -> Quantity:
    """Estimate physical sheets from the duplex setting of every page.

    The estimate is deliberately conservative. Consecutive pages that
    share a duplex setting are grouped; a duplex group is two pages to a
    sheet, a simplex group is one page to a sheet. If any page has no
    determinable duplex setting the whole figure is reported as
    unknown rather than guessed.
    """
    if not pages:
        return Quantity(
            0,
            CALCULATED,
            "The document has no pages, so it needs no sheets.",
        )

    groups: list[list[Any]] = []
    for page in pages:
        duplex = page.requested.duplex
        if duplex not in ("simplex", "duplex-long-edge", "duplex-short-edge"):
            return Quantity(
                None,
                UNKNOWN,
                "At least one page has no determinable duplex setting, so "
                "the sheet count cannot be calculated.",
            )
        if groups and groups[-1][0] == duplex:
            groups[-1][1].append(page)
        else:
            groups.append([duplex, [page]])

    sheets = 0
    copies = 1
    copy_known = True
    for duplex, group in groups:
        pages_per_sheet = 2 if duplex != "simplex" else 1
        sheets += (len(group) + pages_per_sheet - 1) // pages_per_sheet
        for page in group:
            page_copies = page.requested.copies
            if page_copies is None:
                copy_known = False
            else:
                copies = max(copies, page_copies)

    if not copy_known:
        return Quantity(
            sheets,
            CALCULATED,
            "Estimated from the duplex settings. The copy count is not "
            "stated in the document, so this figure does not include it.",
        )
    return Quantity(
        sheets * copies,
        CALCULATED,
        "Estimated from the duplex settings and the medium copy count. "
        "This is a calculated figure, not an observed one.",
    )


def build_statistics(
    preflight: PreflightResult,
    missing_resources: list[dict[str, Any]] | None = None,
    parsing_warnings: list[dict[str, Any]] | None = None,
) -> ProductionStatistics:
    """Turn a preflight result into production statistics."""
    stats = ProductionStatistics(
        filename=preflight.filename,
        file_path=preflight.file_path,
        file_size=preflight.file_size,
        file_digest=preflight.file_digest,
        preflight_status=preflight.status,
    )

    stats.total_pages = Quantity(
        len(preflight.pages),
        PARSED,
        "Counted from the Begin Page structured fields in the document.",
    )
    stats.total_documents = Quantity(
        preflight.documents,
        PARSED,
        "Counted from the Begin Document structured fields in the document.",
    )
    stats.total_page_groups = Quantity(
        len(preflight.page_groups),
        PARSED,
        "Counted from the Begin Named Page Group structured fields.",
    )

    usage: dict[str, MediumMapUsage] = {}
    for page in preflight.pages:
        key = page.medium_map or "(none)"
        entry = usage.get(key)
        if entry is None:
            entry = MediumMapUsage(
                medium_map=key,
                pages=0,
                stock_name=page.resolved.stock_name,
                tray=page.resolved.tray,
                copies=page.requested.copies,
            )
            usage[key] = entry
        entry.pages += 1
        if entry.stock_name is None:
            entry.stock_name = page.resolved.stock_name
        if entry.tray is None:
            entry.tray = page.resolved.tray
        if entry.copies is None:
            entry.copies = page.requested.copies
    stats.medium_map_usage = sorted(
        usage.values(), key=lambda m: (-m.pages, m.medium_map)
    )

    stats.transitions = [
        {
            "page_number": t.page_number,
            "from_map": t.from_map,
            "to_map": t.to_map,
            "offset": t.offset,
        }
        for t in preflight.transitions
    ]

    stats.missing_resources = list(missing_resources or ())
    stats.parsing_warnings = list(parsing_warnings or ())
    stats.preflight_failures = [
        finding.as_dict()
        for finding in preflight.findings
        if finding.status == "fail"
    ]
    stats.unresolved_pages = sum(
        1 for page in preflight.pages if page.status == "unresolved"
    )
    stats.estimated_sheets = _estimate_sheets(preflight.pages)
    return stats
