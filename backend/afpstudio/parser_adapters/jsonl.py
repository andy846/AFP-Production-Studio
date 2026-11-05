"""Bounded contract 2 JSONL reader. No semantic success before the summary."""
from array import array
import hashlib
import json
import re

from .contract import AdapterError, validate_result


def _integer(value, label, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AdapterError(f"Invalid JSONL {label}.")
    return value


def read_jsonl(stream, *, expected_digest=None, max_line_bytes=16 * 1024 * 1024,
               max_fields=2_000_000):
    header = None
    summary = None
    fields = array("q")
    checksum = hashlib.sha256()
    end = 0
    for line_number in range(max_fields + 3):
        raw = stream.readline(max_line_bytes + 1)
        if not raw:
            break
        if len(raw) > max_line_bytes or not raw.endswith(b"\n"):
            raise AdapterError("JSONL line exceeds limit or is truncated.")
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise AdapterError("Invalid JSONL record.") from exc
        if not isinstance(row, dict) or summary is not None:
            raise AdapterError("Invalid JSONL ordering.")
        kind = row.get("type")
        if header is None:
            if kind != "header" or row.get("contract_version") != "2.0.0":
                raise AdapterError("Expected contract 2.0.0 header.")
            header = row
            _integer(row.get("input_size"), "input size")
            digest = row.get("input_sha256")
            if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
                raise AdapterError("Invalid source digest.")
            if expected_digest is not None and digest != expected_digest:
                raise AdapterError("Adapter parsed a different source.", "source_changed")
        elif kind == "field":
            count = len(fields) // 4
            if count >= max_fields or _integer(row.get("seq"), "sequence") != count:
                raise AdapterError("JSONL field limit or sequence mismatch.")
            offset = _integer(row.get("offset"), "offset")
            length = _integer(row.get("length"), "length", 9, 65536)
            sf_id = _integer(row.get("sf_id"), "identifier", 0, 0xFFFFFF)
            page = 0 if row.get("page") is None else _integer(row["page"], "page", 1)
            if offset < end or offset + length > header["input_size"]:
                raise AdapterError("JSONL field is overlapping or outside the input.")
            fields.extend((offset, length, sf_id, page))
            checksum.update(raw)
            end = offset + length
        elif kind == "summary":
            summary = row
        else:
            raise AdapterError("Unknown JSONL record type.")
    else:
        raise AdapterError("JSONL record limit exceeded.")
    if header is None or summary is None:
        raise AdapterError("Missing JSONL header or final summary.")
    count = len(fields) // 4
    if _integer(summary.get("field_count"), "field count") != count or summary.get("fields_sha256") != checksum.hexdigest():
        raise AdapterError("JSONL count or digest mismatch.")
    result = validate_result(summary.get("result"))
    if result.structured_field_count != count or result.size_bytes != header["input_size"]:
        raise AdapterError("JSONL metadata does not match the stream.")
    if result.complete:
        expected = 0
        for i in range(0, len(fields), 4):
            if fields[i] != expected:
                raise AdapterError("Complete parse omitted input bytes.")
            expected += fields[i + 1]
        if expected != header["input_size"] or not count or result.errors:
            raise AdapterError("Complete parse has unread bytes or errors.")
    result.contract_version = "2.0.0"
    result.field_index = fields
    result.input_sha256 = header["input_sha256"]
    return result
