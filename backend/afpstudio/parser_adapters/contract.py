"""The versioned JSON contract between Studio and the Java parser.

The Alpheus adapter is a separate process that reads an AFP file and
writes one JSON document to standard output. This module is the single
source of truth for that document's shape.

Versioning rules
----------------
* The contract version is a three part number.
* A new **optional** field bumps the minor part.
* Changing the meaning of an existing field bumps the major part.
* A consumer must refuse a document whose **major** version it does not
  know, and must ignore unknown **minor** fields.

Everything the adapter reports keeps the byte offset it was read from,
so Studio can point the operator at the exact structured field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from array import array
import math
from typing import Any

#: The contract version this build speaks.
CONTRACT_VERSION = "1.0.0"

#: The contract version the bundled Java adapter speaks.
ADAPTER_CONTRACT_VERSION = "2.0.0"


class AdapterError(Exception):
    """Raised when the adapter cannot be used or returned nonsense."""

    def __init__(self, message: str, code: str = "invalid_result") -> None:
        super().__init__(message)
        self.code = code


def _require(condition: bool, message: str, code: str = "invalid_result") -> None:
    if not condition:
        raise AdapterError(message, code)


@dataclass
class AdapterPage:
    """One page as the Java parser saw it."""

    page_number: int
    offset: int | None = None
    page_group: str | None = None
    field_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "offset": self.offset,
            "page_group": self.page_group,
            "field_count": self.field_count,
        }


@dataclass
class AdapterMediumMap:
    """A medium map as the Java parser saw it."""

    name: str
    offset: int | None = None
    form_map: str | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    media_type: str | None = None
    media_destination: str | None = None
    duplex: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "offset": self.offset,
            "form_map": self.form_map,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "media_type": self.media_type,
            "media_destination": self.media_destination,
            "duplex": self.duplex,
        }


@dataclass
class AdapterInvoke:
    """An invoke medium map as the Java parser saw it."""

    name: str
    offset: int | None = None
    page_number: int | None = None
    in_page: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "offset": self.offset,
            "page_number": self.page_number,
            "in_page": self.in_page,
        }


@dataclass
class AdapterResult:
    """The whole adapter answer."""

    contract_version: str = CONTRACT_VERSION
    adapter_name: str = "alpheus-adapter"
    adapter_version: str = "0.0.0"
    alpheus_commit: str | None = None
    filename: str = ""
    size_bytes: int = 0
    page_count: int = 0
    documents: int = 0
    structured_field_count: int = 0
    complete: bool = True
    pages: list[AdapterPage] = field(default_factory=list)
    medium_maps: list[AdapterMediumMap] = field(default_factory=list)
    invokes: list[AdapterInvoke] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    field_index: array | None = None
    input_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "adapter": {
                "name": self.adapter_name,
                "version": self.adapter_version,
                "alpheus_commit": self.alpheus_commit,
            },
            "document": {
                "filename": self.filename,
                "size_bytes": self.size_bytes,
                "page_count": self.page_count,
                "documents": self.documents,
                "structured_field_count": self.structured_field_count,
            },
            "complete": self.complete,
            "pages": [p.as_dict() for p in self.pages],
            "medium_maps": [m.as_dict() for m in self.medium_maps],
            "invokes": [i.as_dict() for i in self.invokes],
            "errors": self.errors,
        }


def _as_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AdapterError(f"Adapter result field {label!r} must be a non-negative integer.")
    return value


def _as_optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError(f"Adapter result field {label!r} must be a number.")
    _require(math.isfinite(value), f"{label} must be finite.")
    return float(value)


def validate_result(data: Any) -> AdapterResult:
    """Validate and convert an adapter answer into :class:`AdapterResult`."""
    _require(isinstance(data, dict), "Adapter result must be a JSON object.")

    version = data.get("contract_version")
    _require(
        isinstance(version, str),
        "Adapter result is missing 'contract_version'.",
    )
    known_major = CONTRACT_VERSION.split(".")[0]
    given_major = version.split(".")[0]
    _require(
        given_major == known_major,
        f"Adapter speaks contract version {version}, which this build "
        f"({CONTRACT_VERSION}) cannot read.",
        "contract_version_mismatch",
    )

    adapter = data.get("adapter") or {}
    _require(isinstance(adapter, dict), "'adapter' must be an object.")
    document = data.get("document") or {}
    _require(isinstance(document, dict), "'document' must be an object.")
    _require(isinstance(data.get("complete"), bool), "Adapter result requires a boolean 'complete' field.")

    result = AdapterResult(
        contract_version=version,
        adapter_name=str(adapter.get("name", "unknown")),
        adapter_version=str(adapter.get("version", "0.0.0")),
        alpheus_commit=adapter.get("alpheus_commit"),
        filename=str(document.get("filename", "")),
        size_bytes=_as_int(document.get("size_bytes", 0), "size_bytes"),
        page_count=_as_int(document.get("page_count", 0), "page_count"),
        documents=_as_int(document.get("documents", 0), "documents"),
        structured_field_count=_as_int(
            document.get("structured_field_count", 0), "structured_field_count"
        ),
        complete=data["complete"],
    )

    for raw in data.get("pages") or ():
        _require(isinstance(raw, dict), "Every page must be an object.")
        result.pages.append(
            AdapterPage(
                page_number=_as_int(
                    raw.get("page_number", 0), "page_number"
                ),
                offset=(
                    _as_int(raw["offset"], "offset")
                    if raw.get("offset") is not None
                    else None
                ),
                page_group=raw.get("page_group"),
                field_count=_as_int(raw.get("field_count", 0), "field_count"),
            )
        )

    for raw in data.get("medium_maps") or ():
        _require(isinstance(raw, dict), "Every medium map must be an object.")
        result.medium_maps.append(
            AdapterMediumMap(
                name=str(raw.get("name", "")),
                offset=(
                    _as_int(raw["offset"], "offset")
                    if raw.get("offset") is not None
                    else None
                ),
                form_map=raw.get("form_map"),
                width_mm=_as_optional_float(
                    raw.get("width_mm"), "width_mm"
                ),
                height_mm=_as_optional_float(
                    raw.get("height_mm"), "height_mm"
                ),
                media_type=raw.get("media_type"),
                media_destination=raw.get("media_destination"),
                duplex=raw.get("duplex"),
            )
        )

    for raw in data.get("invokes") or ():
        _require(isinstance(raw, dict), "Every invoke must be an object.")
        result.invokes.append(
            AdapterInvoke(
                name=str(raw.get("name", "")),
                offset=(
                    _as_int(raw["offset"], "offset")
                    if raw.get("offset") is not None
                    else None
                ),
                page_number=(
                    _as_int(raw["page_number"], "page_number")
                    if raw.get("page_number") is not None
                    else None
                ),
                in_page=bool(raw.get("in_page", False)),
            )
        )

    for raw in data.get("errors") or ():
        _require(isinstance(raw, dict), "Every error must be an object.")
        result.errors.append(dict(raw))

    return result


def compare_with_local(
    adapter: AdapterResult, local_page_count: int, local_medium_maps: list[str],
    *, index=None, analysis=None,
) -> list[dict[str, Any]]:
    """Compare the two parsers and describe every disagreement.

    A disagreement is never silently ignored: the preflight turns it
    into a failure, because "the two engines disagree" means Studio
    cannot say which one is right.
    """
    disagreements: list[dict[str, Any]] = []

    if adapter.page_count != local_page_count:
        disagreements.append(
            {
                "kind": "page_count",
                "adapter": adapter.page_count,
                "local": local_page_count,
                "message": (
                    f"The Java parser counted {adapter.page_count} page(s); "
                    f"Studio counted {local_page_count}."
                ),
            }
        )

    adapter_maps = sorted({m.name for m in adapter.medium_maps if m.name})
    local_maps = sorted({name for name in local_medium_maps if name})
    if adapter_maps != local_maps:
        only_adapter = sorted(set(adapter_maps) - set(local_maps))
        only_local = sorted(set(local_maps) - set(adapter_maps))
        disagreements.append(
            {
                "kind": "medium_maps",
                "adapter": adapter_maps,
                "local": local_maps,
                "only_adapter": only_adapter,
                "only_local": only_local,
                "message": (
                    "The two parsers found different medium maps"
                    + (f"; only the Java parser found {only_adapter}" if only_adapter else "")
                    + (f"; only Studio found {only_local}" if only_local else "")
                    + "."
                ),
            }
        )

    if index is not None:
        if adapter.field_index is not None:
            fields = adapter.field_index
            for i in range(max(index.field_count, len(fields) // 4)):
                local = (int(index.offsets[i]), int(index.lengths[i]), int(index.sf_ids[i]),
                         int(index.pages[i]) + 1 if index.in_page[i] else 0) if i < index.field_count else None
                remote = tuple(fields[i * 4:i * 4 + 4]) if i * 4 < len(fields) else None
                if local != remote:
                    entry = local or remote
                    disagreements.append({"kind": "structured_field", "offset": entry[0],
                        "page": entry[3] or None, "local": local, "adapter": remote,
                        "message": "Independent parsers disagree on field offset, length, identifier or page association."})
                    if len(disagreements) >= 1000:
                        disagreements.append({"kind": "comparison_limit", "message": "Comparison stopped after 1000 differences; results are incomplete."})
                        break
        for page in adapter.pages:
            number = page.page_number
            if page.offset is None or number < 1 or number > len(index.page_starts):
                continue
            local_offset = int(index.page_starts[number - 1])
            if page.offset != local_offset:
                disagreements.append({
                    "kind": "page_offset", "page": number, "offset": local_offset,
                    "local": local_offset, "adapter": page.offset,
                    "message": f"Page {number} starts at different offsets in the two parsers.",
                })

    if analysis is not None:
        if adapter.contract_version == "2.0.0":
            from afpstudio.preflight.media import DUPLEX_CONTROL_NAMES
            for remote in adapter.medium_maps:
                local = analysis.medium_maps.get(remote.name)
                if local is None:
                    continue
                values = {value for mod in local.modifications.values() for value in mod.keyword(0xF4)}
                explicit_duplex = None
                if len(values) > 1:
                    explicit_duplex = "conflict"
                elif values:
                    value = next(iter(values))
                    explicit_duplex = DUPLEX_CONTROL_NAMES.get(value, f"unknown-0x{value:02X}")
                checks = [("medium_map_offset", local.offset, remote.offset), ("duplex", explicit_duplex, remote.duplex)]
                if local.descriptor:
                    for label, a, b in (("medium_width", local.descriptor.width_mm, remote.width_mm),
                                        ("medium_height", local.descriptor.height_mm, remote.height_mm)):
                        if a is not None and b is not None and abs(a - b) > .05:
                            checks.append((label, a, b))
                for kind, local_value, remote_value in checks:
                    if local_value != remote_value:
                        disagreements.append({"kind": kind, "offset": local.offset, "local": local_value,
                            "adapter": remote_value, "message": f"The parsers disagree on {kind} for medium map {local.name}."})
        local_invokes = {item.offset: item for item in analysis.invokes}
        java_invokes = {item.offset: item for item in adapter.invokes if item.offset is not None}
        for offset in sorted(local_invokes.keys() | java_invokes.keys()):
            local = local_invokes.get(offset)
            java = java_invokes.get(offset)
            local_name = local.name if local else None
            java_name = java.name if java else None
            if local_name != java_name:
                disagreements.append({
                    "kind": "invoke", "offset": offset,
                    "page": local.page + 1 if local and local.page is not None and local.in_page else None,
                    "local": local_name, "adapter": java_name,
                    "message": f"The parsers disagree on the medium map invocation at offset {offset}.",
                })
    if len(disagreements) > 1000:
        return disagreements[:1000] + [{"kind": "comparison_limit", "message": "Further differences omitted; comparison is incomplete."}]
    return disagreements
