"""Media preflight: what sheet does every page ask for, and can we say?

The engine answers three questions per page and refuses to answer when
the AFP does not carry the answer:

1. **Requested media** — the medium map the document invokes, the sheet
   extent it declares, the stock name it names and the duplex it asks
   for. This comes from the AFP and the (possibly external) form
   definition.
2. **Resolved media** — the paper stock and tray that follow from the
   operator's printer profile. This is a *mapping*, not an observation.
3. **Actual media** — never produced here. Actual media is what the
   printer or the RIP confirms, and Studio has no such feed yet. The
   report says so explicitly instead of guessing.

Status ladder
-------------
``pass``
    The medium map is declared in the document and mapped in the printer
    profile, and nothing complained.
``warning``
    Resolved, but something is off (an undeclared transition, a duplex
    mismatch, a control Studio does not model).
``unresolved``
    The information needed is missing: no form definition, no printer
    mapping, an unknown medium map. Never silently downgraded to a pass.
``fail``
    The information contradicts itself or the data is broken.

The ranking is ``fail > unresolved > warning > pass``: a definite error
outranks missing information, and missing information outranks a
warning.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from afpstudio.preflight.media import (
    DUPLEX_CONTROL_NAMES,
    MediumMapDefinition,
    MediaAnalysis,
    PageDescriptor,
)
from afpstudio.preflight.rules import (
    RuleViolation,
    Transition,
    evaluate_rules,
)
from afpstudio.printer_profiles.schema import (
    MediaMapEntry,
    PaperSize,
    PrinterProfile,
)
from afpstudio.viewer.index import BDT, DocumentIndex

STATUS_PASS = "pass"
STATUS_WARNING = "warning"
STATUS_FAIL = "fail"
STATUS_UNRESOLVED = "unresolved"

STATUS_ORDER = {
    STATUS_PASS: 0,
    STATUS_WARNING: 1,
    STATUS_UNRESOLVED: 2,
    STATUS_FAIL: 3,
}

STATUSES = (STATUS_PASS, STATUS_WARNING, STATUS_FAIL, STATUS_UNRESOLVED)

#: How far the sheet extent may differ from the profile before it counts
#: as a mismatch. Two millimetres covers rounding in the MO:DCA unit
#: bases without hiding a genuine A4-versus-Letter problem.
PAPER_TOLERANCE_MM = 2.0

def worst(statuses: Iterable[str]) -> str:
    """Return the most severe status of ``statuses``."""
    worst_status = STATUS_PASS
    for status in statuses:
        if STATUS_ORDER.get(status, 0) > STATUS_ORDER[worst_status]:
            worst_status = status
    return worst_status


@dataclass
class Finding:
    """One preflight observation."""

    code: str
    status: str
    message: str
    page: int | None = None
    offset: int | None = None
    sf_id: int | None = None
    source: str = "afp"
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "page": self.page,
            "offset": self.offset,
            "sf_id": self.sf_id,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass
class RequestedMedia:
    """What the AFP asks for."""

    medium_map: str | None = None
    form_map: str | None = None
    media_type: str | None = None
    media_destination: str | None = None
    medium_width_mm: float | None = None
    medium_height_mm: float | None = None
    page_width_mm: float | None = None
    page_height_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    orientation: str | None = None
    duplex: str | None = None
    copies: int | None = None
    declared: bool = False
    definition_source: str = "none"
    offset: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "medium_map": self.medium_map,
            "form_map": self.form_map,
            "media_type": self.media_type,
            "media_destination": self.media_destination,
            "medium_width_mm": self.medium_width_mm,
            "medium_height_mm": self.medium_height_mm,
            "page_width_mm": self.page_width_mm,
            "page_height_mm": self.page_height_mm,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "orientation": self.orientation,
            "duplex": self.duplex,
            "copies": self.copies,
            "declared": self.declared,
            "definition_source": self.definition_source,
            "offset": self.offset,
        }


@dataclass
class ResolvedMedia:
    """What the printer profile says about the request."""

    stock_name: str | None = None
    tray: str | None = None
    paper_size: PaperSize | None = None
    duplex: str | None = None
    source: str = "none"
    profile: str | None = None
    verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "stock_name": self.stock_name,
            "tray": self.tray,
            "paper_size": self.paper_size.as_dict() if self.paper_size else None,
            "duplex": self.duplex,
            "source": self.source,
            "profile": self.profile,
            "verified": self.verified,
        }


@dataclass
class PageMedia:
    """The preflight result for a single page."""

    page_number: int
    page_index: int
    document_id: str
    page_group: str | None
    offset: int | None
    medium_map: str | None
    medium_map_source: str
    requested: RequestedMedia
    resolved: ResolvedMedia
    status: str = STATUS_PASS
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "page_index": self.page_index,
            "document_id": self.document_id,
            "page_group": self.page_group,
            "offset": self.offset,
            "medium_map": self.medium_map,
            "medium_map_source": self.medium_map_source,
            "requested": self.requested.as_dict(),
            "resolved": self.resolved.as_dict(),
            "status": self.status,
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass
class PreflightResult:
    """Everything the preflight learned about one AFP file."""

    filename: str
    file_path: str
    file_size: int
    file_digest: str
    status: str = STATUS_PASS
    pages: list[PageMedia] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    medium_maps: list[str] = field(default_factory=list)
    form_maps: list[str] = field(default_factory=list)
    documents: int = 0
    page_groups: list[str] = field(default_factory=list)
    implicit_pages: int = 0
    profile_name: str | None = None
    external_form_defs: list[str] = field(default_factory=list)
    parser: str = "studio"
    parse_complete: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "file_digest": self.file_digest,
            "status": self.status,
            "pages": [p.as_dict() for p in self.pages],
            "transitions": [
                {
                    "page_number": t.page_number,
                    "from_map": t.from_map,
                    "to_map": t.to_map,
                    "offset": t.offset,
                }
                for t in self.transitions
            ],
            "findings": [f.as_dict() for f in self.findings],
            "medium_maps": self.medium_maps,
            "form_maps": self.form_maps,
            "documents": self.documents,
            "page_groups": self.page_groups,
            "implicit_pages": self.implicit_pages,
            "profile_name": self.profile_name,
            "external_form_defs": self.external_form_defs,
            "parser": self.parser,
            "parse_complete": self.parse_complete,
        }


def _digest(path: Path) -> str:
    """A short content digest, used to tell two jobs apart in a report."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def _document_map(index: DocumentIndex) -> list[str]:
    """Return the document id of every page, e.g. ``D1``, ``D2``."""
    boundaries: list[int] = []
    for i in range(index.field_count):
        if index.sf_ids[i] == BDT:
            boundaries.append(i)
    if not boundaries:
        return ["D1"] * index.page_count
    result: list[str] = []
    ordinal = 0
    for page in range(index.page_count):
        # A page belongs to the last document that started at or before it.
        while (
            ordinal + 1 < len(boundaries)
            and index.pages[boundaries[ordinal + 1]] <= page
        ):
            ordinal += 1
        result.append(f"D{ordinal + 1}")
    return result


def _duplex_of(definition: MediumMapDefinition) -> tuple[str | None, str]:
    """Return the duplex the medium map asks for and where it came from."""
    modifications = getattr(definition, "modifications", {}) or {}
    values = {
        mod.duplex_control
        for mod in modifications.values()
        if mod.duplex_control is not None
    }
    if not values:
        # MO:DCA: a medium without a duplex control is printed simplex.
        return "simplex", "medium_map_default"
    if len(values) > 1:
        return None, "conflict"
    control = next(iter(values))
    return DUPLEX_CONTROL_NAMES.get(control, f"unknown-0x{control:02X}"), "afp"


def _requested_for(
    definition: MediumMapDefinition | None,
    invoke_name: str | None,
    invoke_record: Any,
    definition_source: str,
    page_descriptor: PageDescriptor | None = None,
) -> RequestedMedia:
    requested = RequestedMedia(
        medium_map=invoke_name,
        offset=getattr(invoke_record, "offset", None),
        definition_source=definition_source,
    )
    if invoke_record is not None:
        requested.orientation = getattr(invoke_record, "orientation", None)
    if page_descriptor is not None:
        requested.page_width_mm = page_descriptor.width_mm
        requested.page_height_mm = page_descriptor.height_mm
    if definition is None:
        requested.width_mm = requested.page_width_mm
        requested.height_mm = requested.page_height_mm
        return requested
    requested.declared = True
    requested.form_map = definition.form_map
    requested.media_type = definition.media_type_name
    requested.media_destination = definition.media_destination_name
    descriptor = definition.descriptor
    if descriptor is not None:
        requested.medium_width_mm = descriptor.width_mm
        requested.medium_height_mm = descriptor.height_mm
    requested.width_mm = (
        requested.medium_width_mm
        if requested.medium_width_mm is not None
        else requested.page_width_mm
    )
    requested.height_mm = (
        requested.medium_height_mm
        if requested.medium_height_mm is not None
        else requested.page_height_mm
    )
    duplex, _source = _duplex_of(definition)
    requested.duplex = duplex
    requested.copies = definition.total_copies
    return requested


def _resolved_for(
    profile: PrinterProfile | None,
    medium_map: str | None,
    verified: bool = False,
) -> ResolvedMedia:
    if profile is None or medium_map is None:
        return ResolvedMedia(source="none")
    entry: MediaMapEntry | None = profile.media_maps.get(medium_map)
    if entry is None:
        return ResolvedMedia(source="none", profile=profile.name)
    return ResolvedMedia(
        stock_name=entry.stock_name,
        tray=entry.tray,
        paper_size=entry.paper_size,
        duplex=entry.duplex,
        source="printer_profile",
        profile=profile.name,
        verified=verified,
    )


def _paper_size_mismatch(
    requested: RequestedMedia, resolved: ResolvedMedia
) -> tuple[bool, dict[str, Any]]:
    width = requested.medium_width_mm or requested.page_width_mm
    height = requested.medium_height_mm or requested.page_height_mm
    if width is None or height is None:
        return False, {}
    if resolved.paper_size is None:
        return False, {}
    width_gap = abs(width - resolved.paper_size.width_mm)
    height_gap = abs(height - resolved.paper_size.height_mm)
    if width_gap <= PAPER_TOLERANCE_MM and height_gap <= PAPER_TOLERANCE_MM:
        return False, {}
    return True, {
        "requested_width_mm": width,
        "requested_height_mm": height,
        "profile_width_mm": resolved.paper_size.width_mm,
        "profile_height_mm": resolved.paper_size.height_mm,
        "tolerance_mm": PAPER_TOLERANCE_MM,
    }


def _page_exceeds_sheet(requested: RequestedMedia) -> tuple[bool, dict[str, Any]]:
    """Does the page presentation space exceed the sheet it sits on?"""
    medium_w = requested.medium_width_mm
    medium_h = requested.medium_height_mm
    page_w = requested.page_width_mm
    page_h = requested.page_height_mm
    if None in (medium_w, medium_h, page_w, page_h):
        return False, {}
    if page_w <= medium_w + PAPER_TOLERANCE_MM and (
        page_h <= medium_h + PAPER_TOLERANCE_MM
    ):
        return False, {}
    return True, {
        "medium_width_mm": medium_w,
        "medium_height_mm": medium_h,
        "page_width_mm": page_w,
        "page_height_mm": page_h,
        "tolerance_mm": PAPER_TOLERANCE_MM,
    }


def _effective_invokes(
    index: DocumentIndex, analysis: MediaAnalysis
) -> list[dict[str, Any]]:
    """Work out which medium map each page uses.

    The rule is stream order: the last invoke that appears before a page
    starts carries over to that page, and an invoke that sits *inside* a
    page additionally wins for that page. This matches how generators
    emit media — sometimes between pages, sometimes inside the first
    page of a section.
    """
    invokes = sorted(analysis.invokes, key=lambda r: r.offset)
    page_starts = list(index.page_starts)
    effective: list[dict[str, Any]] = []
    cursor = 0
    carried = None
    for page_index in range(len(page_starts)):
        start = page_starts[page_index]
        end = (
            index.page_ends[page_index]
            if page_index < len(index.page_ends)
            else None
        )
        inside = None
        while cursor < len(invokes) and invokes[cursor].offset < start:
            carried = invokes[cursor]
            cursor += 1
        while cursor < len(invokes) and end is not None and invokes[cursor].offset < end:
            inside = invokes[cursor]
            cursor += 1
        chosen = inside or carried
        if inside is not None:
            carried = inside
        effective.append(
            {
                "record": chosen,
                "source": (
                    "page_imm"
                    if inside is not None
                    else ("document_imm" if carried is not None else "none")
                ),
            }
        )
    return effective


def run_preflight(
    path: Path,
    index: DocumentIndex,
    analysis: MediaAnalysis,
    profile: PrinterProfile | None = None,
    external_definitions: dict[str, MediumMapDefinition] | None = None,
    missing_resources: Sequence[dict[str, Any]] | None = None,
    parser_disagreements: Sequence[dict[str, Any]] | None = None,
    parser_failures: Sequence[dict[str, Any]] | None = None,
    incomplete: bool = False,
    implicit_pages: int = 0,
    parser: str = "studio",
) -> PreflightResult:
    """Run the full media preflight over an indexed document."""
    path = Path(path)
    external_definitions = external_definitions or {}
    result = PreflightResult(
        filename=path.name,
        file_path=str(path),
        file_size=path.stat().st_size if path.exists() else 0,
        file_digest=_digest(path) if path.exists() else "",
        profile_name=profile.name if profile else None,
        parser=parser,
        implicit_pages=implicit_pages,
    )
    result.documents = analysis.documents
    result.page_groups = list(index.page_groups)
    result.form_maps = list(analysis.form_maps)
    result.medium_maps = sorted(analysis.medium_maps)
    result.external_form_defs = sorted(external_definitions)

    document_ids = _document_map(index)
    effective = _effective_invokes(index, analysis)

    # -- document level findings ----------------------------------------
    if index.truncated:
        result.parse_complete = False
        result.findings.append(
            Finding(
                code="INCOMPLETE_PARSE",
                status=STATUS_FAIL,
                message=(
                    "The document could not be parsed to the end. Results are "
                    "partial and must not be treated as a pass."
                ),
                source="parser",
                detail={"warnings": index.warnings[:20]},
            )
        )
    if incomplete:
        result.parse_complete = False
        result.findings.append(
            Finding(
                code="INCOMPLETE_PARSE",
                status=STATUS_FAIL,
                message=(
                    "The parsing engine reported an incomplete parse. Results "
                    "are partial and must not be treated as a pass."
                ),
                source="parser",
            )
        )
    for warning in analysis.warnings:
        result.findings.append(
            Finding(
                code=warning.get("code", "MEDIA_WARNING"),
                status=STATUS_WARNING,
                message=warning.get("message", ""),
                offset=warning.get("offset"),
                source="afp",
            )
        )
    for entry in analysis.unsupported:
        result.findings.append(
            Finding(
                code=entry.get("code", "UNSUPPORTED_MEDIA_FIELD"),
                status=STATUS_WARNING,
                message=entry.get("message", ""),
                offset=entry.get("offset"),
                source="afp",
            )
        )
    for resource in missing_resources or ():
        result.findings.append(
            Finding(
                code="MISSING_RESOURCE",
                status=STATUS_FAIL,
                message=(
                    f"The document references {resource.get('kind', 'resource')} "
                    f"{resource.get('name', '')!r} but does not embed it."
                ),
                source="afp",
                detail=dict(resource),
            )
        )
    for disagreement in parser_disagreements or ():
        result.findings.append(
            Finding(
                code="PARSER_DISAGREEMENT",
                status=STATUS_FAIL,
                message=(
                    "The two parsing engines disagree: "
                    f"{disagreement.get('message', '')}"
                ),
                source="parser",
                detail=dict(disagreement),
            )
        )
    for failure in parser_failures or ():
        result.parse_complete = False
        result.findings.append(
            Finding(
                code="PARSER_FAILURE",
                status=STATUS_FAIL,
                message=(
                    "The optional Java parser did not complete: "
                    f"{failure.get('message', '')}"
                ),
                source="parser",
                detail=dict(failure),
            )
        )

    has_form_map = (
        analysis.form_map_count > 0 or bool(external_definitions)
    )
    if index.page_count and not has_form_map:
        result.findings.append(
            Finding(
                code="MISSING_FORM_DEF",
                status=STATUS_UNRESOLVED,
                message=(
                    "The document carries no form definition and no external "
                    "form definition was supplied, so the medium maps it "
                    "uses cannot be declared."
                ),
                source="afp",
            )
        )
    if profile is None:
        result.findings.append(
            Finding(
                code="MISSING_PRINTER_PROFILE",
                status=STATUS_UNRESOLVED,
                message=(
                    "No printer profile is selected, so requested media "
                    "cannot be resolved to a paper stock or tray."
                ),
                source="printer_profile",
            )
        )

    # -- per page -------------------------------------------------------
    previous_map: str | None = None
    for page_index in range(index.page_count):
        page_number = page_index + 1
        entry = effective[page_index] if page_index < len(effective) else None
        record = entry["record"] if entry else None
        map_source = entry["source"] if entry else "none"
        invoke_name = record.name if record is not None else None

        findings: list[Finding] = []
        definition = None
        definition_source = "none"
        if invoke_name:
            definition = analysis.medium_maps.get(invoke_name)
            if definition is not None:
                definition_source = "document"
            else:
                definition = external_definitions.get(invoke_name)
                if definition is not None:
                    definition_source = "external_form_def"

        requested = _requested_for(
            definition,
            invoke_name,
            record,
            definition_source,
            analysis.page_descriptors.get(page_index),
        )
        resolved = _resolved_for(
            profile, invoke_name, verified=definition is not None
        )

        page_offset = (
            int(index.page_starts[page_index])
            if page_index < len(index.page_starts)
            else None
        )

        if record is not None and record.malformed:
            findings.append(
                Finding(
                    code="INVALID_IMM",
                    status=STATUS_FAIL,
                    message=(
                        "The invoke medium map field at offset "
                        f"{record.offset} carries no usable medium map name."
                    ),
                    page=page_number,
                    offset=record.offset,
                    source="afp",
                )
            )
        elif invoke_name is None:
            findings.append(
                Finding(
                    code="NO_MEDIUM_MAP_INVOKED",
                    status=STATUS_UNRESOLVED,
                    message=(
                        "No medium map is invoked for this page, so the sheet "
                        "it prints on cannot be determined."
                    ),
                    page=page_number,
                    offset=page_offset,
                    source="afp",
                )
            )
        elif definition is None:
            findings.append(
                Finding(
                    code="MEDIUM_MAP_UNDECLARED",
                    status=STATUS_UNRESOLVED,
                    message=(
                        f"The document invokes medium map {invoke_name!r} but "
                        "no form definition declares it."
                    ),
                    page=page_number,
                    offset=record.offset if record else page_offset,
                    source="afp",
                    detail={"medium_map": invoke_name},
                )
            )
        elif profile is not None and resolved.source == "none":
            findings.append(
                Finding(
                    code="MEDIA_MAP_UNMAPPED",
                    status=STATUS_UNRESOLVED,
                    message=(
                        f"Medium map {invoke_name!r} is not mapped in printer "
                        f"profile {profile.name!r}, so no paper stock or tray "
                        "can be resolved."
                    ),
                    page=page_number,
                    offset=record.offset if record else page_offset,
                    source="printer_profile",
                    detail={"medium_map": invoke_name},
                )
            )

        if definition is not None:
            duplex, duplex_source = _duplex_of(definition)
            if duplex_source == "conflict":
                findings.append(
                    Finding(
                        code="DUPLEX_INCONSISTENT",
                        status=STATUS_FAIL,
                        message=(
                            "The medium modification controls of this medium "
                            "map ask for different duplex settings."
                        ),
                        page=page_number,
                        offset=page_offset,
                        source="afp",
                    )
                )
            elif (
                duplex
                and resolved.duplex
                and resolved.duplex != "unknown"
                and duplex != resolved.duplex
            ):
                findings.append(
                    Finding(
                        code="DUPLEX_PROFILE_MISMATCH",
                        status=STATUS_WARNING,
                        message=(
                            f"The AFP asks for {duplex} but printer profile "
                            f"{profile.name!r} maps this medium map to "
                            f"{resolved.duplex}."
                        ),
                        page=page_number,
                        offset=page_offset,
                        source="printer_profile",
                        detail={
                            "afp_duplex": duplex,
                            "profile_duplex": resolved.duplex,
                        },
                    )
                )

            mismatch, detail = _paper_size_mismatch(requested, resolved)
            if mismatch:
                findings.append(
                    Finding(
                        code="PAPER_SIZE_MISMATCH",
                        status=STATUS_FAIL,
                        message=(
                            "The document asks for "
                            f"{detail['requested_width_mm']:.1f} x "
                            f"{detail['requested_height_mm']:.1f} mm "
                            "but the printer profile maps it to "
                            f"{resolved.paper_size.width_mm:.1f} x "
                            f"{resolved.paper_size.height_mm:.1f} mm."
                        ),
                        page=page_number,
                        offset=page_offset,
                        source="printer_profile",
                        detail=detail,
                    )
                )

            exceeds, detail = _page_exceeds_sheet(requested)
            if exceeds:
                findings.append(
                    Finding(
                        code="PAGE_SIZE_EXCEEDS_SHEET",
                        status=STATUS_FAIL,
                        message=(
                            f"The page presentation space is "
                            f"{detail['page_width_mm']:.1f} x "
                            f"{detail['page_height_mm']:.1f} mm but the medium "
                            f"is only {detail['medium_width_mm']:.1f} x "
                            f"{detail['medium_height_mm']:.1f} mm."
                        ),
                        page=page_number,
                        offset=page_offset,
                        source="afp",
                        detail=detail,
                    )
                )

        page = PageMedia(
            page_number=page_number,
            page_index=page_index,
            document_id=(
                document_ids[page_index]
                if page_index < len(document_ids)
                else "D1"
            ),
            page_group=(
                analysis.page_groups.get(page_index)
                if page_index in analysis.page_groups
                else None
            ),
            offset=page_offset,
            medium_map=invoke_name,
            medium_map_source=map_source,
            requested=requested,
            resolved=resolved,
            findings=findings,
        )

        statuses = [f.status for f in findings]
        if resolved.source == "none" and invoke_name:
            statuses.append(STATUS_UNRESOLVED)
        page.status = worst(statuses) if statuses else STATUS_PASS
        result.pages.append(page)

        if previous_map != invoke_name:
            result.transitions.append(
                Transition(
                    page_number=page_number,
                    from_map=previous_map,
                    to_map=invoke_name,
                    offset=record.offset if record else page_offset,
                )
            )
            previous_map = invoke_name

    # -- operator rules --------------------------------------------------
    rule_violations: list[RuleViolation] = []
    if profile is not None:
        rule_violations = evaluate_rules(
            profile.rules, result.pages, result.transitions
        )
    for violation in rule_violations:
        finding = Finding(
            code="RULE_VIOLATION",
            status=(
                STATUS_FAIL if violation.severity == "fail" else STATUS_WARNING
            ),
            message=violation.message,
            page=violation.page,
            offset=violation.offset,
            source="printer_profile",
            detail={"rule_id": violation.rule_id, **violation.detail},
        )
        result.findings.append(finding)
        if violation.page is not None:
            for page in result.pages:
                if page.page_number == violation.page:
                    page.findings.append(finding)
                    page.status = worst([page.status, finding.status])

    # -- transitions not covered by a rule -------------------------------
    covered = {
        (rule.match.get("from_medium_map"), rule.match.get("to_medium_map"))
        for rule in (profile.rules if profile else [])
        if rule.scope == "media_transition"
    }
    for transition in result.transitions:
        if transition.page_number == 1:
            continue
        key = (transition.from_map, transition.to_map)
        if key in covered:
            continue
        finding = Finding(
            code="UNEXPECTED_MEDIA_CHANGE",
            status=STATUS_WARNING,
            message=(
                f"Page {transition.page_number} changes medium map from "
                f"{transition.from_map!r} to {transition.to_map!r}. No rule in "
                "the printer profile describes this transition."
            ),
            page=transition.page_number,
            offset=transition.offset,
            source="printer_profile",
            detail={
                "from_medium_map": transition.from_map,
                "to_medium_map": transition.to_map,
            },
        )
        result.findings.append(finding)
        for page in result.pages:
            if page.page_number == transition.page_number:
                page.findings.append(finding)
                page.status = worst([page.status, finding.status])

    # -- overall status --------------------------------------------------
    statuses = [page.status for page in result.pages]
    statuses.extend(finding.status for finding in result.findings)
    result.status = worst(statuses) if statuses else STATUS_PASS
    return result
