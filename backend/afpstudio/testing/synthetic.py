"""Builders for synthetic AFP documents.

The media preflight needs documents that exercise one situation each —
a duplex medium, a medium change halfway through a job, a document with
no form definition at all. Rather than shipping opaque binary blobs, the
samples are described here as code, so a test can change one field and
see exactly what it is testing.

Only synthetic or publicly licensed material is used; no customer AFP
ever enters the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CARRIAGE_CONTROL = 0x5A

# Structured field identifiers used by the builder.
BDT = 0xD3A8A8
EDT = 0xD3A9A8
BNG = 0xD3A8AD
ENG = 0xD3A9AD
BPG = 0xD3A8AF
EPG = 0xD3A9AF
BFM = 0xD3A8CD
EFM = 0xD3A9CD
BMM = 0xD3A8CC
EMM = 0xD3A9CC
IMM = 0xD3ABCC
MDD = 0xD3A688
MCC = 0xD3A288
MMC = 0xD3A788
MMT = 0xD3AB88
MMD = 0xD3ABCD
PGD = 0xD3A6AF

#: Triplet identifiers.
TRIPLET_FULLY_QUALIFIED_NAME = 0x02
TRIPLET_MEDIUM_MAP_PAGE_NUMBER = 0x56
TRIPLET_MEDIUM_ORIENTATION = 0x68

#: Fully qualified name types.
FQN_TYPE_MEDIA_TYPE = 0x11
FQN_TYPE_MEDIA_DESTINATION = 0x12

#: Medium modification control keywords.
KWD_DUPLEX_CONTROL = 0xF4
KWD_MEDIA_SOURCE_SELECTOR = 0xE1

#: Duplex control values.
DUPLEX_SIMPLEX = 0x01
DUPLEX_LONG_EDGE = 0x02
DUPLEX_SHORT_EDGE = 0x03

#: Unit bases.
UNIT_BASE_INCHES_10 = 0x00
UNIT_BASE_CENTIMETRES_10 = 0x01


def encode_name(name: str, length: int = 8) -> bytes:
    """Encode a name as EBCDIC, padded with EBCDIC spaces (0x40)."""
    raw = name.encode("cp500")[:length]
    return raw + b"\x40" * (length - len(raw))


def structured_field(sf_id: int, payload: bytes = b"", flags: int = 0) -> bytes:
    """Wrap a payload in an AFP structured field header."""
    length = 8 + len(payload)
    return (
        bytes([CARRIAGE_CONTROL])
        + length.to_bytes(2, "big")
        + sf_id.to_bytes(3, "big")
        + bytes([flags])
        + (0).to_bytes(2, "big")
        + payload
    )


def triplet(triplet_id: int, data: bytes = b"") -> bytes:
    """Build one triplet."""
    return bytes([len(data) + 2, triplet_id]) + data


def fqn_triplet(fqn_type: int, name: str) -> bytes:
    """Build a fully qualified name triplet."""
    payload = bytes([fqn_type, 0]) + encode_name(name)
    return triplet(TRIPLET_FULLY_QUALIFIED_NAME, payload)


def bdt() -> bytes:
    return structured_field(BDT)


def edt() -> bytes:
    return structured_field(EDT)


def bng(name: str) -> bytes:
    return structured_field(BNG, encode_name(name))


def eng(name: str) -> bytes:
    return structured_field(ENG, encode_name(name))


def bpg() -> bytes:
    return structured_field(BPG)


def epg() -> bytes:
    return structured_field(EPG)


def bfm(name: str = "FORM01") -> bytes:
    return structured_field(BFM, encode_name(name))


def efm(name: str = "FORM01") -> bytes:
    return structured_field(EFM, encode_name(name))


def bmm(name: str) -> bytes:
    return structured_field(BMM, encode_name(name))


def emm(name: str) -> bytes:
    return structured_field(EMM, encode_name(name))


def imm(name: str, page_number: int | None = None, orientation: int | None = None) -> bytes:
    payload = encode_name(name)
    if page_number is not None:
        payload += triplet(
            TRIPLET_MEDIUM_MAP_PAGE_NUMBER, page_number.to_bytes(4, "big")
        )
    if orientation is not None:
        payload += triplet(TRIPLET_MEDIUM_ORIENTATION, bytes([orientation]))
    return structured_field(IMM, payload)


def mdd(
    width_mm: float,
    height_mm: float,
    unit_base: int = UNIT_BASE_INCHES_10,
    units_per_base: int = 2400,
    orientation: int | None = 0,
) -> bytes:
    """Build a medium descriptor for a sheet of the given physical size."""
    scale = 254.0 if unit_base == UNIT_BASE_INCHES_10 else 100.0
    x_extent = round(width_mm * units_per_base / scale)
    y_extent = round(height_mm * units_per_base / scale)
    payload = (
        bytes([unit_base, unit_base])
        + units_per_base.to_bytes(2, "big")
        + units_per_base.to_bytes(2, "big")
        + x_extent.to_bytes(3, "big")
        + y_extent.to_bytes(3, "big")
        + bytes([0x00])
    )
    if orientation is not None:
        payload += triplet(TRIPLET_MEDIUM_ORIENTATION, bytes([orientation]))
    return structured_field(MDD, payload)


def pgd(
    width_mm: float,
    height_mm: float,
    unit_base: int = UNIT_BASE_INCHES_10,
    units_per_base: int = 2400,
) -> bytes:
    """Build a page descriptor for a page presentation space."""
    scale = 254.0 if unit_base == UNIT_BASE_INCHES_10 else 100.0
    x_size = round(width_mm * units_per_base / scale)
    y_size = round(height_mm * units_per_base / scale)
    payload = (
        bytes([unit_base, unit_base])
        + units_per_base.to_bytes(2, "big")
        + units_per_base.to_bytes(2, "big")
        + x_size.to_bytes(3, "big")
        + y_size.to_bytes(3, "big")
        + b"\x00\x00\x00"
    )
    return structured_field(PGD, payload)


def mcc(groups: list[tuple[int, int, int]]) -> bytes:
    """Build a medium copy count from (start, end, mmc id) groups."""
    payload = b""
    for start, end, mmc_id in groups:
        payload += (
            start.to_bytes(2, "big")
            + end.to_bytes(2, "big")
            + bytes([0x00, mmc_id])
        )
    return structured_field(MCC, payload)


def mmc(
    identifier: int = 1,
    duplex: int | None = None,
    source_selector: int | None = None,
    constant: int = 0xFF,
) -> bytes:
    """Build a medium modification control."""
    payload = bytes([identifier, constant])
    if source_selector is not None:
        payload += bytes([KWD_MEDIA_SOURCE_SELECTOR, source_selector])
    if duplex is not None:
        payload += bytes([KWD_DUPLEX_CONTROL, duplex])
    return structured_field(MMC, payload)


def _map_entry(local_id: int, fqn_type: int, name: str) -> bytes:
    triplets = fqn_triplet(fqn_type, name)
    group = (
        (len(triplets) + 8).to_bytes(2, "big")
        + bytes([local_id])
        + b"\x00" * 5
        + triplets
    )
    return group


def mmt(local_id: int, name: str) -> bytes:
    """Build a map media type entry."""
    return structured_field(MMT, _map_entry(local_id, FQN_TYPE_MEDIA_TYPE, name))


def mmd(local_id: int, name: str) -> bytes:
    """Build a map media destination entry."""
    return structured_field(
        MMD, _map_entry(local_id, FQN_TYPE_MEDIA_DESTINATION, name)
    )


@dataclass
class MediumMapSpec:
    """How to build one medium map."""

    name: str
    width_mm: float = 210.0
    height_mm: float = 297.0
    duplex: int | None = DUPLEX_SIMPLEX
    copies: tuple[int, int] = (1, 1)
    mmc_id: int = 1
    media_type: str | None = None
    media_destination: str | None = None
    source_selector: int | None = None

    def build(self) -> bytes:
        out = bmm(self.name)
        out += mdd(self.width_mm, self.height_mm)
        out += mcc([(self.copies[0], self.copies[1], self.mmc_id)])
        out += mmc(
            identifier=self.mmc_id,
            duplex=self.duplex,
            source_selector=self.source_selector,
        )
        if self.media_type:
            out += mmt(1, self.media_type)
        if self.media_destination:
            out += mmd(1, self.media_destination)
        out += emm(self.name)
        return out


@dataclass
class DocumentSpec:
    """How to build one synthetic document."""

    medium_maps: list[MediumMapSpec] = field(default_factory=list)
    form_map_name: str | None = "FORM01"
    #: One entry per page: the medium map invoked on that page.
    page_invokes: list[str | None] = field(default_factory=list)
    #: Medium map invoked between pages (applies to the pages that follow).
    document_invokes: list[str] = field(default_factory=list)
    page_group: str | None = None
    page_size: tuple[float, float] = (210.0, 297.0)
    with_page_descriptor: bool = True
    with_form_map: bool = True

    def build(self) -> bytes:
        out = bdt()
        if self.page_group:
            out += bng(self.page_group)
        if self.with_form_map:
            if self.form_map_name:
                out += bfm(self.form_map_name)
            for spec in self.medium_maps:
                out += spec.build()
            if self.form_map_name:
                out += efm(self.form_map_name)
        for invoke_name in self.document_invokes:
            out += imm(invoke_name)
        for invoke_name in self.page_invokes:
            out += bpg()
            if self.with_page_descriptor:
                out += pgd(*self.page_size)
            if invoke_name:
                out += imm(invoke_name)
            out += epg()
        if self.page_group:
            out += eng(self.page_group)
        out += edt()
        return out


def simplex_document(pages: int = 3) -> bytes:
    """One form map, one medium map, simplex, invoked per page."""
    spec = DocumentSpec(
        medium_maps=[
            MediumMapSpec(
                name="MAP01",
                duplex=DUPLEX_SIMPLEX,
                media_type="WHITE80",
                media_destination="TRAY1",
            )
        ],
        page_invokes=["MAP01"] * pages,
    )
    return spec.build()


def duplex_document(pages: int = 2) -> bytes:
    """A duplex medium map invoked between the pages."""
    spec = DocumentSpec(
        medium_maps=[
            MediumMapSpec(
                name="MAPDUP",
                duplex=DUPLEX_LONG_EDGE,
                media_type="WHITE80",
                media_destination="TRAY1",
            )
        ],
        document_invokes=["MAPDUP"],
        page_invokes=[None] * pages,
    )
    return spec.build()


def transition_document(pages: int = 4) -> bytes:
    """Two medium maps; the second takes over halfway through the job."""
    spec = DocumentSpec(
        medium_maps=[
            MediumMapSpec(
                name="MAP01",
                duplex=DUPLEX_SIMPLEX,
                media_type="WHITE80",
                media_destination="TRAY1",
            ),
            MediumMapSpec(
                name="MAP02",
                duplex=DUPLEX_SIMPLEX,
                media_type="LETTERHEAD",
                media_destination="TRAY2",
            ),
        ],
        page_invokes=["MAP01", "MAP01", "MAP02", "MAP02"],
    )
    return spec.build()


def no_form_def_document(pages: int = 2) -> bytes:
    """A document that invokes a medium map it never declares."""
    spec = DocumentSpec(
        medium_maps=[],
        with_form_map=False,
        page_invokes=["MAP01"] * pages,
    )
    return spec.build()


def unknown_media_document(pages: int = 1) -> bytes:
    """A document whose medium map is declared but never invoked."""
    spec = DocumentSpec(
        medium_maps=[
            MediumMapSpec(
                name="MAP99",
                media_type="UNKNOWN",
                media_destination="TRAY9",
            )
        ],
        page_invokes=[None] * pages,
    )
    return spec.build()


def malformed_imm_document() -> bytes:
    """A document whose invoke medium map carries no name."""
    out = bdt()
    out += bfm("FORM01")
    out += MediumMapSpec(name="MAP01").build()
    out += efm("FORM01")
    out += bpg()
    out += structured_field(IMM, b"        ")
    out += epg()
    out += edt()
    return out


def invalid_length_document() -> bytes:
    """A document whose structured field claims an impossible length."""
    out = bdt()
    out += bpg()
    broken = bytearray(structured_field(EPG))
    broken[1:3] = (3).to_bytes(2, "big")
    out += bytes(broken)
    out += edt()
    return out


def truncated_document() -> bytes:
    """A document that stops in the middle of a structured field."""
    out = bdt()
    out += bpg()
    out += epg()
    out += edt()
    return out[:-3]


def conflicting_duplex_document() -> bytes:
    """A medium map whose two modification controls disagree on duplex."""
    out = bdt()
    out += bfm("FORM01")
    out += bmm("MAP01")
    out += mdd(210.0, 297.0)
    out += mcc([(1, 1, 1), (2, 2, 2)])
    out += mmc(identifier=1, duplex=DUPLEX_SIMPLEX)
    out += mmc(identifier=2, duplex=DUPLEX_LONG_EDGE)
    out += emm("MAP01")
    out += efm("FORM01")
    out += bpg()
    out += imm("MAP01")
    out += epg()
    out += edt()
    return out
