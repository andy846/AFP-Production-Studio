"""Printer profile schema and validation.

A printer profile says what a medium map means on a particular device:
which paper stock it is, which tray it comes from, and optionally the
physical sheet size so a mismatch against the medium descriptor can be
detected.

The profile also carries the operator's own rules, because "letterhead
only on page 1" is a statement about the job, not about the printer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

SEVERITIES = ("fail", "warning", "ignore")
RULE_SCOPES = ("medium_map", "media_transition", "document")
DUPLEX_VALUES = (
    "unknown",
    "simplex",
    "duplex-long-edge",
    "duplex-short-edge",
)

#: Match value that stands for "pages with no usable medium map".
UNMAPPED = "__unmapped__"

#: The constraints each rule scope understands. Unknown keys are rejected
#: so a typo in the Settings tab fails loudly instead of silently never
#: matching anything.
MEDIUM_MAP_CONSTRAINTS = frozenset(
    {
        "min_page_number",
        "max_page_number",
        "min_pages",
        "max_pages",
        "stock_name",
        "allowed_stock_names",
        "tray",
        "require_duplex",
        "duplex",
    }
)

MEDIA_TRANSITION_CONSTRAINTS = frozenset(
    {
        "max_occurrences",
        "min_occurrences",
        "allowed_page_numbers",
    }
)

DOCUMENT_CONSTRAINTS = frozenset(
    {
        "max_distinct_medium_maps",
        "min_distinct_medium_maps",
        "max_pages_for_medium_map",
        "min_pages_for_medium_map",
        "require_all_pages_mapped",
    }
)

KNOWN_CONSTRAINTS = {
    "medium_map": MEDIUM_MAP_CONSTRAINTS,
    "media_transition": MEDIA_TRANSITION_CONSTRAINTS,
    "document": DOCUMENT_CONSTRAINTS,
}

_MEDIA_MAP_KEYS = {"stock_name", "tray", "paper_size", "duplex"}
_RULE_KEYS = {"id", "description", "scope", "match", "constraints", "severity"}


class ProfileError(ValueError):
    """Raised when a printer profile is not valid."""


@dataclass
class PaperSize:
    width_mm: float
    height_mm: float

    def as_dict(self) -> dict[str, float]:
        return {"width_mm": self.width_mm, "height_mm": self.height_mm}


@dataclass
class MediaMapEntry:
    stock_name: str
    tray: str | None = None
    paper_size: PaperSize | None = None
    duplex: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"stock_name": self.stock_name}
        if self.tray:
            data["tray"] = self.tray
        if self.paper_size:
            data["paper_size"] = self.paper_size.as_dict()
        if self.duplex and self.duplex != "unknown":
            data["duplex"] = self.duplex
        return data


@dataclass
class Rule:
    id: str
    scope: str
    severity: str
    description: str = ""
    match: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "scope": self.scope,
            "severity": self.severity,
        }
        if self.description:
            data["description"] = self.description
        if self.match:
            data["match"] = self.match
        if self.constraints:
            data["constraints"] = self.constraints
        return data


@dataclass
class PrinterProfile:
    name: str
    media_maps: dict[str, MediaMapEntry] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "printer_profile": self.name,
            "media_maps": {
                key: value.as_dict() for key, value in self.media_maps.items()
            },
            "rules": [rule.as_dict() for rule in self.rules],
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _validate_media_maps(raw_maps: Any) -> dict[str, MediaMapEntry]:
    _require(isinstance(raw_maps, dict), "'media_maps' must be an object.")
    media_maps: dict[str, MediaMapEntry] = {}
    for key, value in raw_maps.items():
        _require(isinstance(key, str) and key != "", "Medium map names must be text.")
        _require(
            isinstance(value, dict),
            f"Medium map {key!r} must map to an object.",
        )
        unknown = set(value) - _MEDIA_MAP_KEYS
        _require(
            not unknown,
            f"Medium map {key!r} has unknown keys: {sorted(unknown)}.",
        )
        stock = value.get("stock_name")
        _require(
            isinstance(stock, str) and stock.strip() != "",
            f"Medium map {key!r} needs a 'stock_name'.",
        )
        tray = value.get("tray")
        _require(
            tray is None or isinstance(tray, str),
            f"Medium map {key!r} has a non-text 'tray'.",
        )
        duplex = value.get("duplex", "unknown")
        _require(
            duplex in DUPLEX_VALUES,
            f"Medium map {key!r} has an unknown duplex value {duplex!r}.",
        )
        paper_size = None
        raw_size = value.get("paper_size")
        if raw_size is not None:
            _require(
                isinstance(raw_size, dict),
                f"Medium map {key!r} has a non-object 'paper_size'.",
            )
            _require(
                set(raw_size) == {"width_mm", "height_mm"},
                f"Medium map {key!r} 'paper_size' needs width_mm and height_mm.",
            )
            width = raw_size["width_mm"]
            height = raw_size["height_mm"]
            _require(
                isinstance(width, (int, float)) and width > 0,
                f"Medium map {key!r} 'paper_size.width_mm' must be positive.",
            )
            _require(
                isinstance(height, (int, float)) and height > 0,
                f"Medium map {key!r} 'paper_size.height_mm' must be positive.",
            )
            paper_size = PaperSize(float(width), float(height))
        media_maps[key] = MediaMapEntry(
            stock_name=stock,
            tray=tray,
            paper_size=paper_size,
            duplex=duplex,
        )
    return media_maps


def _validate_rules(raw_rules: Any) -> list[Rule]:
    _require(isinstance(raw_rules, list), "'rules' must be an array.")
    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for raw in raw_rules:
        _require(isinstance(raw, dict), "Every rule must be an object.")
        unknown = set(raw) - _RULE_KEYS
        _require(not unknown, f"Rule has unknown keys: {sorted(unknown)}.")
        rule_id = raw.get("id")
        _require(
            isinstance(rule_id, str) and rule_id != "",
            "Every rule needs a non-empty 'id'.",
        )
        _require(rule_id not in seen_ids, f"Duplicate rule id {rule_id!r}.")
        seen_ids.add(rule_id)
        scope = raw.get("scope")
        _require(
            scope in RULE_SCOPES,
            f"Rule {rule_id!r} has an unknown scope {scope!r}; "
            f"expected one of {list(RULE_SCOPES)}.",
        )
        severity = raw.get("severity")
        _require(
            severity in SEVERITIES,
            f"Rule {rule_id!r} has an unknown severity {severity!r}; "
            f"expected one of {list(SEVERITIES)}.",
        )
        match = raw.get("match", {})
        constraints = raw.get("constraints", {})
        _require(
            isinstance(match, dict), f"Rule {rule_id!r} 'match' must be an object."
        )
        _require(
            isinstance(constraints, dict),
            f"Rule {rule_id!r} 'constraints' must be an object.",
        )
        known = KNOWN_CONSTRAINTS[scope]
        unknown_constraints = set(constraints) - known
        _require(
            not unknown_constraints,
            f"Rule {rule_id!r} has unknown constraints for scope {scope!r}: "
            f"{sorted(unknown_constraints)}; expected one of {sorted(known)}.",
        )
        description = raw.get("description", "")
        _require(
            isinstance(description, str),
            f"Rule {rule_id!r} 'description' must be text.",
        )
        rules.append(
            Rule(
                id=rule_id,
                scope=scope,
                severity=severity,
                description=description,
                match=match,
                constraints=constraints,
            )
        )
    return rules


def validate_profile(data: Any) -> PrinterProfile:
    """Validate a decoded printer profile document."""
    _require(isinstance(data, dict), "Printer profile must be a JSON object.")

    version = data.get("schema_version")
    _require(
        version == SCHEMA_VERSION,
        f"Unsupported printer profile schema_version {version!r}; "
        f"expected {SCHEMA_VERSION!r}.",
    )

    name = data.get("printer_profile")
    _require(
        isinstance(name, str) and name.strip() != "",
        "Printer profile needs a non-empty 'printer_profile' name.",
    )

    media_maps = _validate_media_maps(data.get("media_maps", {}))
    rules = _validate_rules(data.get("rules", []))

    return PrinterProfile(
        name=name,
        media_maps=media_maps,
        rules=rules,
        schema_version=version,
    )
