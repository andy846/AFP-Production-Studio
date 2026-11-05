"""MO:DCA media analysis.

This module answers one question per page: which medium map does the
document ask for, and what does the AFP itself say about the media that
map implies?

The structures below mirror MO:DCA:

* a **form map** (BFM ... EFM) contains **medium maps** (BMM ... EMM),
* a medium map carries a **medium descriptor** (MDD) with the sheet
  extent, **medium copy count** (MCC) with the copy groups, **medium
  modification control** (MMC) with the device keywords, and
  **map media type** / **map media destination** (MMT / MMD) entries
  that name the paper and the destination,
* an **invoke medium map** (IMM) activates a medium map. An IMM inside a
  page bracket applies to that page; an IMM outside one applies to the
  pages that follow until another IMM supersedes it.

Nothing here resolves media against a printer. That is the job of the
printer profile, and it happens in :mod:`afpstudio.preflight.engine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from afpstudio.viewer.index import (
    BDT,
    EDT,
    BFM,
    BMM,
    BNG,
    BPG,
    ENG,
    EFM,
    EMM,
    EPG,
    IMM,
    MCC,
    MDD,
    MMD,
    MMT,
    MMC,
    PGD,
    PMC,
    DocumentIndex,
    decode_ebcdic_name,
)

# Medium map keywords carried by MMC (MO:DCA medium modification control).
KWD_MEDIA_SOURCE_SELECTOR = 0xE1
KWD_MEDIA_TYPE_LOCAL_ID_HIGH = 0xE8
KWD_MEDIA_TYPE_LOCAL_ID_LOW = 0xE9
KWD_MEDIA_DESTINATION_SELECTOR_HIGH = 0x90
KWD_MEDIA_DESTINATION_SELECTOR_LOW = 0x91
KWD_DUPLEX_CONTROL = 0xF4
KWD_MEDIUM_OVERLAY_LOCAL_ID = 0xF2

#: Triplet identifiers used by the media controls.
TRIPLET_FULLY_QUALIFIED_NAME = 0x02
TRIPLET_MEDIUM_MAP_PAGE_NUMBER = 0x56
TRIPLET_MEDIUM_ORIENTATION = 0x68

FQN_TYPE_MEDIA_TYPE = 0x11
FQN_TYPE_MEDIA_DESTINATION = 0x12

#: Duplex control values (MO:DCA medium modification control keyword 0xF4).
DUPLEX_CONTROL_NAMES = {
    0x01: "simplex",
    0x02: "duplex-long-edge",
    0x03: "duplex-short-edge",
}

#: Millimetres per unit for each MO:DCA unit base.
#: 0x00 means "units are ten inches", 0x01 "units are ten centimetres"
#: and 0x02 is a logical resolution ratio with no physical size.
UNIT_BASE_MM = {0x00: 254.0, 0x01: 100.0, 0x02: None}

UNIT_BASE_NAMES = {
    0x00: "tenths of an inch",
    0x01: "tenths of a centimetre",
    0x02: "logical resolution ratio",
}

ORIENTATIONS = {
    0x00: "portrait",
    0x01: "landscape",
    0x02: "reverse portrait",
    0x03: "reverse landscape",
    0x04: "portrait 90 degrees",
    0x05: "landscape 90 degrees",
}

#: Fields the media walk needs. Everything else in the file is ignored.
MEDIA_FIELDS = frozenset(
    {
        BDT,
        EDT,
        BNG,
        ENG,
        BPG,
        BMM,
        EMM,
        EPG,
        BFM,
        EFM,
        IMM,
        MDD,
        MCC,
        MMC,
        MMT,
        MMD,
        PGD,
        PMC,
    }
)


class MediaParseError(Exception):
    """Raised when a media control cannot be decoded."""


@dataclass
class MediumDescriptor:
    """The sheet extent declared by a medium descriptor."""

    x_unit_base: int
    y_unit_base: int
    x_units_per_unit_base: int
    y_units_per_unit_base: int
    x_extent: int
    y_extent: int
    flag: int

    @property
    def unit_base_name(self) -> str:
        return UNIT_BASE_NAMES.get(
            self.x_unit_base, f"unknown 0x{self.x_unit_base:02X}"
        )

    def _mm(self, extent: int, base: int, per_base: int) -> float | None:
        scale = UNIT_BASE_MM.get(base)
        if scale is None or per_base <= 0:
            return None
        return extent * scale / per_base

    @property
    def width_mm(self) -> float | None:
        if self.x_extent <= 0:
            # A zero extent means the medium does not state a size; it does
            # not mean a zero sized sheet.
            return None
        return self._mm(
            self.x_extent, self.x_unit_base, self.x_units_per_unit_base
        )

    @property
    def height_mm(self) -> float | None:
        if self.y_extent <= 0:
            return None
        return self._mm(
            self.y_extent, self.y_unit_base, self.y_units_per_unit_base
        )

    @property
    def size_known(self) -> bool:
        return self.width_mm is not None and self.height_mm is not None


@dataclass
class PageDescriptor:
    """The page presentation space declared by a page descriptor."""

    x_unit_base: int
    y_unit_base: int
    x_units_per_unit_base: int
    y_units_per_unit_base: int
    x_size: int
    y_size: int
    offset: int = 0

    def _mm(self, size: int, base: int, per_base: int) -> float | None:
        scale = UNIT_BASE_MM.get(base)
        if scale is None or per_base <= 0 or size <= 0:
            return None
        return size * scale / per_base

    @property
    def width_mm(self) -> float | None:
        return self._mm(
            self.x_size, self.x_unit_base, self.x_units_per_unit_base
        )

    @property
    def height_mm(self) -> float | None:
        return self._mm(
            self.y_size, self.y_unit_base, self.y_units_per_unit_base
        )

    @property
    def size_known(self) -> bool:
        return self.width_mm is not None and self.height_mm is not None


@dataclass
class CopyCountGroup:
    """One repeating group of a medium copy count."""

    starting_copy: int
    ending_copy: int
    mmc_identifier: int

    @property
    def copies(self) -> int:
        return max(0, self.ending_copy - self.starting_copy + 1)


@dataclass
class MediumModification:
    """The device keywords carried by a medium modification control."""

    identifier: int
    constant_data: int
    keywords: dict[int, list[int]] = field(default_factory=dict)

    def keyword(self, keyword_id: int) -> list[int]:
        return self.keywords.get(keyword_id, [])

    @property
    def duplex_control(self) -> int | None:
        values = self.keyword(KWD_DUPLEX_CONTROL)
        return values[0] if values else None

    @property
    def media_type_local_id(self) -> int | None:
        high = self.keyword(KWD_MEDIA_TYPE_LOCAL_ID_HIGH)
        low = self.keyword(KWD_MEDIA_TYPE_LOCAL_ID_LOW)
        if high and low:
            return (high[0] << 8) | low[0]
        return None

    @property
    def media_destination_local_id(self) -> int | None:
        high = self.keyword(KWD_MEDIA_DESTINATION_SELECTOR_HIGH)
        low = self.keyword(KWD_MEDIA_DESTINATION_SELECTOR_LOW)
        if high and low:
            return (high[0] << 8) | low[0]
        return None


@dataclass
class MediumMapDefinition:
    """A medium map: one named choice of sheet and destination."""

    name: str
    offset: int
    form_map: str | None = None
    descriptor: MediumDescriptor | None = None
    copy_counts: list[CopyCountGroup] = field(default_factory=list)
    modification: MediumModification | None = None
    modifications: dict[int, MediumModification] = field(default_factory=dict)
    media_types: dict[int, str] = field(default_factory=dict)
    media_destinations: dict[int, str] = field(default_factory=dict)

    @property
    def total_copies(self) -> int | None:
        """Number of copies the medium asks for, if it says so."""
        if not self.copy_counts:
            return None
        return sum(group.copies for group in self.copy_counts)

    @property
    def media_type_name(self) -> str | None:
        if not self.media_types:
            return None
        return next(iter(self.media_types.values()))

    @property
    def media_destination_name(self) -> str | None:
        if not self.media_destinations:
            return None
        return next(iter(self.media_destinations.values()))


@dataclass
class InvokeRecord:
    """An invocation of a medium map."""

    name: str
    offset: int
    page: int | None
    level: str
    in_page: bool = False
    medium_map_page_number: int | None = None
    orientation: str | None = None
    malformed: bool = False


@dataclass
class MediaAnalysis:
    """Everything the media walk learned about a document."""

    medium_maps: dict[str, MediumMapDefinition] = field(default_factory=dict)
    ambiguous_maps: set[str] = field(default_factory=set)
    invokes: list[InvokeRecord] = field(default_factory=list)
    form_maps: list[str] = field(default_factory=list)
    form_map_count: int = 0
    page_groups: dict[int, str] = field(default_factory=dict)
    page_descriptors: dict[int, PageDescriptor] = field(default_factory=dict)
    documents: int = 0
    warnings: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)

    def definition(self, name: str) -> MediumMapDefinition | None:
        return self.medium_maps.get(name)


def _u16(data: bytes, off: int) -> int:
    return int.from_bytes(data[off : off + 2], "big")


def _u24(data: bytes, off: int) -> int:
    return int.from_bytes(data[off : off + 3], "big")


def parse_medium_descriptor(data: bytes) -> MediumDescriptor:
    """Decode an MDD payload (13 byte prefix)."""
    if len(data) < 13:
        raise MediaParseError("Medium descriptor is shorter than 13 bytes.")
    return MediumDescriptor(
        x_unit_base=data[0],
        y_unit_base=data[1],
        x_units_per_unit_base=_u16(data, 2),
        y_units_per_unit_base=_u16(data, 4),
        x_extent=_u24(data, 6),
        y_extent=_u24(data, 9),
        flag=data[12],
    )


def parse_copy_counts(data: bytes) -> list[CopyCountGroup]:
    """Decode an MCC payload (repeating groups of six bytes)."""
    groups: list[CopyCountGroup] = []
    pos = 0
    while pos + 6 <= len(data):
        groups.append(
            CopyCountGroup(
                starting_copy=_u16(data, pos),
                ending_copy=_u16(data, pos + 2),
                mmc_identifier=data[pos + 5],
            )
        )
        pos += 6
    return groups


def parse_page_descriptor(data: bytes, offset: int = 0) -> PageDescriptor:
    """Decode a PGD payload (12 byte prefix plus three reserved bytes)."""
    if len(data) < 12:
        raise MediaParseError("Page descriptor is shorter than 12 bytes.")
    return PageDescriptor(
        x_unit_base=data[0],
        y_unit_base=data[1],
        x_units_per_unit_base=_u16(data, 2),
        y_units_per_unit_base=_u16(data, 4),
        x_size=_u24(data, 6),
        y_size=_u24(data, 9),
        offset=offset,
    )


def parse_modification(data: bytes) -> MediumModification:
    """Decode an MMC payload (identifier, constant, then two byte keywords)."""
    if len(data) < 2:
        raise MediaParseError(
            "Medium modification control is shorter than 2 bytes."
        )
    keywords: dict[int, list[int]] = {}
    pos = 2
    while pos + 2 <= len(data):
        keyword_id = data[pos]
        parameter = data[pos + 1]
        keywords.setdefault(keyword_id, []).append(parameter)
        pos += 2
    return MediumModification(
        identifier=data[0], constant_data=data[1], keywords=keywords
    )


def _parse_map_entries(data: bytes, fqn_type: int) -> dict[int, str]:
    """Decode an MMT or MMD payload into {local id: name}."""
    entries: dict[int, str] = {}
    pos = 0
    while pos + 8 <= len(data):
        group_length = _u16(data, pos)
        if group_length < 8 or pos + group_length > len(data):
            break
        local_id = data[pos + 2]
        group = data[pos + 8 : pos + group_length]
        name = _fqn_name(group, fqn_type)
        if name:
            entries[local_id] = name
        pos += group_length
    return entries


def _fqn_name(group: bytes, fqn_type: int) -> str | None:
    """Find the media name inside a repeating group's triplets."""
    pos = 0
    while pos + 2 <= len(group):
        length = group[pos]
        triplet_id = group[pos + 1]
        if length < 2 or pos + length > len(group):
            break
        payload = group[pos + 2 : pos + length]
        if triplet_id == TRIPLET_FULLY_QUALIFIED_NAME and len(payload) >= 2:
            if payload[0] == fqn_type:
                return _decode_name(payload[2:])
        pos += length
    return None


def _decode_name(raw: bytes) -> str:
    """Decode a media name from an FQN payload."""
    try:
        text = raw.decode("cp500")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace").strip()
    return "".join(ch for ch in text if ch.isprintable()).strip()


def _name_of(data: bytes) -> str:
    if len(data) < 8:
        return ""
    return decode_ebcdic_name(data[:8])


def imm_triplets(data: bytes) -> list[tuple[int, bytes]]:
    """Triplets of an IMM payload, which starts with an eight byte name."""
    triplets: list[tuple[int, bytes]] = []
    pos = 8
    while pos + 2 <= len(data):
        length = data[pos]
        triplet_id = data[pos + 1]
        if length < 2 or pos + length > len(data):
            break
        triplets.append((triplet_id, bytes(data[pos + 2 : pos + length])))
        pos += length
    return triplets


def _medium_map_page_number(data: bytes) -> int | None:
    for triplet_id, payload in imm_triplets(data):
        if triplet_id == TRIPLET_MEDIUM_MAP_PAGE_NUMBER and len(payload) >= 4:
            return int.from_bytes(payload[:4], "big")
    return None


def _orientation(data: bytes) -> str | None:
    for triplet_id, payload in imm_triplets(data):
        if triplet_id == TRIPLET_MEDIUM_ORIENTATION and payload:
            return ORIENTATIONS.get(payload[0], f"orientation 0x{payload[0]:02X}")
    return None


def analyse_media(index: DocumentIndex, path: Path, check_cancelled=None) -> MediaAnalysis:
    """Walk the media controls of an indexed document."""
    analysis = MediaAnalysis()
    form_map_stack: list[str | None] = []
    medium_map_stack: list[MediumMapDefinition] = []
    page_group_stack: list[str] = []
    current_form_map: str | None = None
    page = -1

    with path.open("rb") as handle:
        for i in range(index.field_count):
            if check_cancelled and i % 256 == 0:
                check_cancelled()
            sf_id = index.sf_ids[i]
            if sf_id == BPG:
                page += 1
                if page_group_stack:
                    analysis.page_groups[page] = page_group_stack[-1]
                continue
            if sf_id == ENG:
                if page_group_stack:
                    page_group_stack.pop()
                continue
            if sf_id not in MEDIA_FIELDS:
                continue

            handle.seek(index.offsets[i] + 9)
            data = handle.read(max(0, index.lengths[i] - 9))

            if sf_id == BDT:
                analysis.documents += 1
                page_group_stack.clear()
            elif sf_id == EDT:
                page_group_stack.clear()
            elif sf_id == BNG:
                name = index.token_at(i)
                if name:
                    page_group_stack.append(name)
            elif sf_id == BFM:
                name = index.token_at(i) or ""
                current_form_map = name or None
                form_map_stack.append(current_form_map)
                analysis.form_map_count += 1
                if name:
                    analysis.form_maps.append(name)
            elif sf_id == EFM:
                if form_map_stack:
                    form_map_stack.pop()
                else:
                    analysis.warnings.append({"code": "UNMATCHED_FORM_MAP_END", "offset": int(index.offsets[i]), "message": "End Form Map has no matching begin."})
                current_form_map = form_map_stack[-1] if form_map_stack else None
            elif sf_id == BMM:
                name = index.token_at(i) or ""
                definition = MediumMapDefinition(
                    name=name, offset=index.offsets[i], form_map=current_form_map
                )
                medium_map_stack.append(definition)
                if name:
                    if name in analysis.medium_maps or name in analysis.ambiguous_maps:
                        analysis.medium_maps.pop(name, None)
                        analysis.ambiguous_maps.add(name)
                    else:
                        analysis.medium_maps[name] = definition
                else:
                    analysis.warnings.append(
                        {
                            "code": "MEDIUM_MAP_UNNAMED",
                            "offset": index.offsets[i],
                            "message": "A medium map was declared without a name.",
                        }
                    )
            elif sf_id == EMM:
                if medium_map_stack:
                    medium_map_stack.pop()
            elif sf_id == MDD:
                if medium_map_stack:
                    try:
                        medium_map_stack[-1].descriptor = parse_medium_descriptor(
                            data
                        )
                    except MediaParseError as exc:
                        analysis.warnings.append(
                            {
                                "code": "MEDIUM_DESCRIPTOR_INVALID",
                                "offset": index.offsets[i],
                                "message": str(exc),
                            }
                        )
            elif sf_id == MCC:
                if medium_map_stack:
                    medium_map_stack[-1].copy_counts = parse_copy_counts(data)
            elif sf_id == PGD:
                if page >= 0 and index.in_page[i]:
                    try:
                        analysis.page_descriptors[page] = parse_page_descriptor(
                            data, index.offsets[i]
                        )
                    except MediaParseError as exc:
                        analysis.warnings.append(
                            {
                                "code": "PAGE_DESCRIPTOR_INVALID",
                                "offset": index.offsets[i],
                                "message": str(exc),
                            }
                        )
            elif sf_id == MMC:
                if medium_map_stack:
                    try:
                        modification = parse_modification(data)
                        medium_map_stack[-1].modification = modification
                        medium_map_stack[-1].modifications[
                            modification.identifier
                        ] = modification
                    except MediaParseError as exc:
                        analysis.warnings.append(
                            {
                                "code": "MEDIUM_MODIFICATION_INVALID",
                                "offset": index.offsets[i],
                                "message": str(exc),
                            }
                        )
            elif sf_id == MMT:
                entries = _parse_map_entries(data, FQN_TYPE_MEDIA_TYPE)
                if medium_map_stack:
                    medium_map_stack[-1].media_types.update(entries)
                else:
                    analysis.unsupported.append(
                        {
                            "code": "MEDIA_TYPE_OUTSIDE_MEDIUM_MAP",
                            "offset": index.offsets[i],
                            "message": (
                                "Map Media Type was found outside a medium map; "
                                "it was not attributed to one."
                            ),
                        }
                    )
            elif sf_id == MMD:
                entries = _parse_map_entries(data, FQN_TYPE_MEDIA_DESTINATION)
                if medium_map_stack:
                    medium_map_stack[-1].media_destinations.update(entries)
                else:
                    analysis.unsupported.append(
                        {
                            "code": "MEDIA_DESTINATION_OUTSIDE_MEDIUM_MAP",
                            "offset": index.offsets[i],
                            "message": (
                                "Map Media Destination was found outside a medium "
                                "map; it was not attributed to one."
                            ),
                        }
                    )
            elif sf_id == IMM:
                name = _name_of(data)
                analysis.invokes.append(
                    InvokeRecord(
                        name=name,
                        offset=index.offsets[i],
                        page=page if index.in_page[i] else None,
                        level="page" if index.in_page[i] else "document",
                        in_page=bool(index.in_page[i]),
                        medium_map_page_number=_medium_map_page_number(data),
                        orientation=_orientation(data),
                        malformed=not name,
                    )
                )

    return analysis


def load_form_definitions(path: Path) -> dict[str, MediumMapDefinition]:
    """Read an external form definition file and return its medium maps.

    Not every AFP carries its own form definition; print shops often keep
    the form definition in a separate file and reference it from the job
    ticket. Loading one here lets the preflight resolve a medium map that
    the document invokes but does not declare.
    """
    from afpstudio.viewer.index import build_index

    path = Path(path)
    index = build_index(path)
    analysis = analyse_media(index, path)
    if analysis.ambiguous_maps:
        raise MediaParseError("External form definition contains duplicate medium map names.")
    return analysis.medium_maps
