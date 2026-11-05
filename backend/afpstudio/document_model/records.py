"""Bounded AFP record framing with offsets in the original input stream.

This module does not yet replace the viewer's existing indexer. Callers must
select the framing explicitly; guessing on malformed inputs is unsafe.
"""

from dataclasses import dataclass
from typing import BinaryIO, Iterator


class RecordError(ValueError):
    def __init__(self, code: str, offset: int, message: str):
        super().__init__(message)
        self.code = code
        self.offset = offset


@dataclass(frozen=True)
class Record:
    offset: int
    payload_offset: int
    payload: bytes
    framing: str


def rdw_length(header: bytes, offset: int) -> int:
    if len(header) != 4:
        raise RecordError("truncated_record", offset, "Truncated RDW header.")
    length = int.from_bytes(header[:2], "big")
    if not 4 <= length <= 32760:
        raise RecordError("invalid_rdw_length", offset, "RDW length is outside the supported range.")
    if header[2:] != b"\x00\x00":
        raise RecordError("unsupported_spanned_record", offset, "Spanned or non-standard RDW records are unsupported.")
    return length


def _read_exact(stream: BinaryIO, length: int, offset: int) -> bytes:
    chunks = []
    left = length
    while left:
        chunk = stream.read(left)
        if not chunk:
            raise RecordError("truncated_record", offset, "Record ends before its declared length.")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def iter_records(stream: BinaryIO, framing: str) -> Iterator[Record]:
    """Read standard 5A or unspanned RDW records; preserve payload bytes."""
    if framing not in ("standard", "rdw"):
        raise ValueError("Unsupported record framing.")
    offset = 0
    while True:
        first = stream.read(1)
        if not first:
            return
        if framing == "standard":
            if first != b"\x5a":
                raise RecordError("unsupported_record", offset, "Expected a 5A structured field.")
            prefix = first + _read_exact(stream, 2, offset)
            length = int.from_bytes(prefix[1:3], "big")
            if length < 8:
                raise RecordError("invalid_length", offset, "Structured field length is below 8.")
            payload = prefix + _read_exact(stream, length - 2, offset)
            yield Record(offset, offset, payload, framing)
            offset += length + 1
        else:
            header = first + _read_exact(stream, 3, offset)
            length = rdw_length(header, offset)
            payload = _read_exact(stream, length - 4, offset)
            yield Record(offset, offset + 4, payload, framing)
            offset += length
