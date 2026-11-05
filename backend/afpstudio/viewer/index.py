"""Streaming structured-field index.

The index is built with a single forward pass over the file. Only field
headers are kept — offset, length, id, flags, sequence and page number —
plus the eight byte EBCDIC token name for named Begin/End fields. A
20 000 page document therefore costs a few megabytes instead of the size
of the file itself.

The pass also records what the renderer needs later:

* which fields live inside a *replay scope* — a resource group, page
  segment, overlay, object container or active environment group. Those
  fields carry state that later pages depend on (fonts, code pages,
  overlays, named images), so they are replayed when a single page is
  rendered on demand.
* the page structure, so the Structure view can map any byte offset back
  to the page that contains it.
"""

from __future__ import annotations

import mmap
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from afpstudio.document_model.records import RecordError, rdw_length

# --- MO:DCA structured field identifiers (3-byte id) ----------------------

BDT = 0xD3A8A8  # Begin Document
EDT = 0xD3A9A8  # End Document
BNG = 0xD3A8AD  # Begin Named Page Group
ENG = 0xD3A9AD  # End Named Page Group
BPG = 0xD3A8AF  # Begin Page
EPG = 0xD3A9AF  # End Page
BPF = 0xD3A8A5  # Begin Print File
EPF = 0xD3A9A5  # End Print File
BAG = 0xD3A8C9  # Begin Active Environment Group
EAG = 0xD3A9C9  # End Active Environment Group
BRG = 0xD3A8C6  # Begin Resource Group
ERG = 0xD3A9C6  # End Resource Group
BRS = 0xD3A8CE  # Begin Resource
ERS = 0xD3A9CE  # End Resource
BPS = 0xD3A85F  # Begin Page Segment
EPS = 0xD3A95F  # End Page Segment
BMO = 0xD3A8DF  # Begin Overlay
EMO = 0xD3A9DF  # End Overlay
BOC = 0xD3A892  # Begin Object Container
EOC = 0xD3A992  # End Object Container
BFM = 0xD3A8CD  # Begin Form Map
EFM = 0xD3A9CD  # End Form Map
BMM = 0xD3A8CC  # Begin Medium Map
EMM = 0xD3A9CC  # End Medium Map
IMM = 0xD3ABCC  # Invoke Medium Map
MDD = 0xD3A688  # Medium Descriptor
MCC = 0xD3A288  # Medium Copy Count
MMC = 0xD3A788  # Medium Modification Control
MMT = 0xD3AB88  # Map Media Type
MMD = 0xD3ABCD  # Map Media Destination
PGD = 0xD3A6AF  # Page Descriptor
PMC = 0xD3A7AF  # Page Modification Control
MCF1 = 0xD3B18A  # Map Coded Font, format 1
MCF2 = 0xD3AB8A  # Map Coded Font, format 2
PTX = 0xD3EE9B  # Presentation Text Data
BCP = 0xD3A887  # Begin Code Page
BFN = 0xD3A889  # Begin Font
BCF = 0xD3A88A  # Begin Coded Font

#: Fields that carry state forward even though they sit inside a page:
#: coded font mappings and data resource mappings accumulate for the rest
#: of the document.
ALWAYS_INCLUDE = frozenset({MCF1, MCF2, 0xD3ABC3})

#: Scope markers whose contents must be replayed to rebuild state.
REPLAY_SCOPES: dict[int, int] = {
    BRG: ERG,
    BRS: ERS,
    BPS: EPS,
    BMO: EMO,
    BOC: EOC,
    BAG: EAG,
    BFM: EFM,
    BMM: EMM,
    BPF: EPF,
}

#: Begin fields whose eight byte payload is an EBCDIC object name.
NAMED_BEGIN_FIELDS = frozenset(
    {
        BNG,
        BMO,
        BPS,
        BRS,
        BRG,
        BAG,
        BFM,
        BMM,
        BCP,
        BFN,
        BCF,
        EFM,
        EMM,
        EMO,
        EPS,
        ERS,
        ERG,
        EAG,
        IMM,
    }
)

CARRIAGE_CONTROL = 0x5A
HEADER_BYTES = 9
MIN_SF_LENGTH = 8


class IndexingError(Exception):
    """Raised when a document cannot be indexed at all."""


@dataclass
class FieldRef:
    """A single structured field as seen by the index."""

    index: int
    offset: int
    length: int
    sf_id: int
    flags: int
    sequence: int
    page: int
    in_scope: bool
    in_page: bool = False
    token: str | None = None


@dataclass
class DocumentIndex:
    """Compact, offset-preserving index of an AFP document."""

    path: Path
    file_size: int
    offsets: array = field(default_factory=lambda: array("q"))
    lengths: array = field(default_factory=lambda: array("i"))
    sf_ids: array = field(default_factory=lambda: array("i"))
    flags: array = field(default_factory=lambda: array("B"))
    sequences: array = field(default_factory=lambda: array("i"))
    pages: array = field(default_factory=lambda: array("i"))
    scopes: array = field(default_factory=lambda: array("B"))
    in_page: array = field(default_factory=lambda: array("B"))
    tokens: dict[int, str] = field(default_factory=dict)
    page_starts: array = field(default_factory=lambda: array("q"))
    page_ends: array = field(default_factory=lambda: array("q"))
    page_field_starts: array = field(default_factory=lambda: array("i"))
    page_field_ends: array = field(default_factory=lambda: array("i"))
    page_groups: list[str] = field(default_factory=list)
    documents: int = 0
    warnings: list[dict] = field(default_factory=list)
    truncated: bool = False
    input_format: str = "standard"

    # -- basic accessors -------------------------------------------------

    @property
    def field_count(self) -> int:
        return len(self.offsets)

    @property
    def page_count(self) -> int:
        return len(self.page_starts)

    def token_at(self, i: int) -> str | None:
        return self.tokens.get(i)

    def in_page_at(self, i: int) -> bool:
        return bool(self.in_page[i])

    def field(self, i: int) -> FieldRef:
        return FieldRef(
            index=i,
            offset=self.offsets[i],
            length=self.lengths[i],
            sf_id=self.sf_ids[i],
            flags=self.flags[i],
            sequence=self.sequences[i],
            page=self.pages[i],
            in_scope=bool(self.scopes[i]),
            in_page=bool(self.in_page[i]),
            token=self.tokens.get(i),
        )

    def fields_for_page(self, page: int) -> range:
        """Return the range of field indexes belonging to ``page``."""
        if page < 0 or page >= len(self.page_field_starts):
            return range(0, 0)
        return range(self.page_field_starts[page], self.page_field_ends[page])

    def page_for_offset(self, offset: int) -> int | None:
        """Map a byte offset to the page that contains it, if any."""
        if not self.page_starts:
            return None
        lo, hi = 0, len(self.page_starts)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.page_starts[mid] <= offset:
                lo = mid + 1
            else:
                hi = mid
        candidate = lo - 1
        if candidate < 0:
            return None
        if offset <= self.page_ends[candidate]:
            return candidate
        return None


def _decode_token(raw: bytes) -> str | None:
    try:
        text = raw.decode("cp500")
    except UnicodeDecodeError:
        return None
    # EBCDIC names are blank padded; some producers pad with other
    # control codes, so drop anything that is not printable.
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text or None


def decode_ebcdic_name(raw: bytes) -> str:
    """Decode an EBCDIC name, dropping padding and control codes."""
    try:
        text = raw.decode("cp500")
    except UnicodeDecodeError:
        return ""
    return "".join(ch for ch in text if ch.isprintable()).strip()


def build_index(path: Path, progress=None, input_format="standard", *, max_fields=2_000_000, max_pages=100_000) -> DocumentIndex:
    """Scan ``path`` once and return its :class:`DocumentIndex`.

    ``progress`` is an optional callable taking a percentage; the
    background job uses it so the UI can show a bar.
    """
    path = Path(path)
    if input_format not in ("standard", "rdw"):
        raise IndexingError("Unsupported input format.")
    size = path.stat().st_size
    index = DocumentIndex(path=path, file_size=size, input_format=input_format)
    if not size:
        index.truncated = True
        index.warnings.append({"code": "EMPTY_INPUT", "offset": 0, "message": "Input contains no structured fields."})
        return index

    def bracket_error(code, offset, message):
        index.truncated = True
        if len(index.warnings) < 100:
            index.warnings.append({"code": code, "offset": offset, "message": message})

    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            pos = 0
            page = -1
            page_open = False
            document_start = None
            group_depth = 0
            scope_stack: list[int] = []
            open_scope: dict[int, int] = {}
            last_report = 0.0

            while pos < size:
                if index.field_count >= max_fields:
                    index.warnings.append({"code": "FIELD_LIMIT", "offset": pos, "message": "Structured field limit reached; analysis is incomplete."})
                    index.truncated = True
                    break
                record_end = None
                if input_format == "rdw":
                    try:
                        record_end = pos + rdw_length(mm[pos:pos + 4], pos)
                    except RecordError as exc:
                        index.warnings.append({"code": exc.code, "offset": exc.offset, "message": str(exc)})
                        index.truncated = True
                        break
                    if record_end > size:
                        index.warnings.append({"code": "RDW_TRUNCATED", "offset": pos, "message": "RDW record exceeds file size."})
                        index.truncated = True
                        break
                    pos += 4
                    if pos >= record_end:
                        index.warnings.append({"code": "RDW_EMPTY_RECORD", "offset": pos - 4, "message": "RDW record contains no structured field."})
                        index.truncated = True
                        break
                if mm[pos] != CARRIAGE_CONTROL:
                    index.warnings.append(
                        {
                            "code": "CARRIAGE_CONTROL_INVALID",
                            "offset": pos,
                            "message": (
                                "Expected a 0x5A carriage-control byte; the "
                                "remaining data was not parsed."
                            ),
                        }
                    )
                    index.truncated = True
                    break

                if pos + HEADER_BYTES > (record_end if record_end is not None else size):
                    index.warnings.append(
                        {
                            "code": "SF_TRUNCATED",
                            "offset": pos,
                            "message": "Structured field header runs past end of file.",
                        }
                    )
                    index.truncated = True
                    break

                length = int.from_bytes(mm[pos + 1 : pos + 3], "big")
                if length < MIN_SF_LENGTH:
                    index.warnings.append(
                        {
                            "code": "SF_LENGTH_INVALID",
                            "offset": pos,
                            "message": (
                                f"Structured field length {length} is below the "
                                f"minimum of {MIN_SF_LENGTH}."
                            ),
                        }
                    )
                    index.truncated = True
                    break

                total = length + 1
                if record_end is not None and pos + total != record_end:
                    index.warnings.append({"code": "RDW_FIELD_LENGTH_MISMATCH", "offset": pos, "message": "Each supported RDW record must contain one complete 5A structured field."})
                    index.truncated = True
                    break
                if pos + total > size:
                    index.warnings.append(
                        {
                            "code": "SF_TRUNCATED",
                            "offset": pos,
                            "message": "Structured field runs past end of file.",
                        }
                    )
                    index.truncated = True
                    break

                sf_id = int.from_bytes(mm[pos + 3 : pos + 6], "big")
                flags = mm[pos + 6]
                sequence = int.from_bytes(mm[pos + 7 : pos + 9], "big")

                # Scope tracking: a Begin field opens a scope, the matching
                # End field closes it.
                if sf_id in REPLAY_SCOPES:
                    scope_stack.append(sf_id)
                    open_scope[sf_id] = REPLAY_SCOPES[sf_id]
                else:
                    for begin_id, end_id in list(open_scope.items()):
                        if end_id == sf_id:
                            if scope_stack and scope_stack[-1] == begin_id:
                                scope_stack.pop()
                            open_scope.pop(begin_id, None)
                            break

                # Page structure.
                if sf_id == BPG:
                    if page_open:
                        bracket_error("NESTED_PAGE", pos, "Begin Page occurs before the previous page ends.")
                    if index.page_count >= max_pages:
                        index.warnings.append({"code": "PAGE_LIMIT", "offset": pos, "message": "Indexed page limit reached; analysis is incomplete."})
                        index.truncated = True
                        break
                    if index.page_ends:
                        index.page_field_ends[-1] = len(index.offsets)
                    page += 1
                    page_open = True
                    index.page_starts.append(pos)
                    index.page_ends.append(pos + total)
                    index.page_field_starts.append(len(index.offsets))
                    index.page_field_ends.append(len(index.offsets) + 1)
                elif sf_id == EPG:
                    if not page_open:
                        bracket_error("UNMATCHED_PAGE_END", pos, "End Page has no matching begin.")
                    if index.page_ends:
                        index.page_ends[-1] = pos + total
                        index.page_field_ends[-1] = len(index.offsets) + 1
                elif sf_id == BDT:
                    if document_start is not None:
                        bracket_error("NESTED_DOCUMENT", pos, "Begin Document occurs before the previous document ends.")
                    document_start = pos
                    index.documents += 1
                elif sf_id == EDT:
                    if document_start is None:
                        bracket_error("UNMATCHED_DOCUMENT_END", pos, "End Document has no matching begin.")
                    if page_open or group_depth:
                        bracket_error("DOCUMENT_SCOPE_UNCLOSED", pos, "Document ends with an open page or page group.")
                    document_start = None
                elif sf_id == BNG:
                    group_depth += 1
                    name = (
                        _decode_token(mm[pos + 9 : pos + 17])
                        if total >= HEADER_BYTES + 8
                        else None
                    )
                    if name:
                        index.page_groups.append(name)
                elif sf_id == ENG:
                    if group_depth:
                        group_depth -= 1
                    else:
                        bracket_error("UNMATCHED_PAGE_GROUP_END", pos, "End Named Page Group has no matching begin.")

                field_index = len(index.offsets)
                index.offsets.append(pos)
                index.lengths.append(total)
                index.sf_ids.append(sf_id)
                index.flags.append(flags)
                index.sequences.append(sequence)
                index.pages.append(page)
                index.scopes.append(1 if scope_stack else 0)
                index.in_page.append(1 if page_open else 0)

                if sf_id == EPG:
                    page_open = False

                if sf_id in NAMED_BEGIN_FIELDS:
                    if total >= HEADER_BYTES + 8:
                        token = _decode_token(mm[pos + 9 : pos + 17])
                        if token:
                            index.tokens[field_index] = token

                pos += total

                if progress is not None and size:
                    ratio = pos / size
                    if ratio - last_report >= 0.01:
                        last_report = ratio
                        progress(ratio)

    if document_start is not None:
        bracket_error("DOCUMENT_UNCLOSED", document_start, "Begin Document has no matching End Document.")
    if page_open:
        bracket_error("PAGE_UNCLOSED", int(index.page_starts[-1]), "Begin Page has no matching End Page.")
    if group_depth:
        bracket_error("PAGE_GROUP_UNCLOSED", pos, "Named page group remains open at end of input.")
    if index.page_field_ends:
        index.page_field_ends[-1] = len(index.offsets)
    if progress is not None:
        progress(1.0)
    return index
