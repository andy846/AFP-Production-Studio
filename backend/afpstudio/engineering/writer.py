"""Byte-preserving copy and same-length IMM / raw TLE edits.

No serializer reconstructs unknown data. Only explicitly selected payload
bytes change; all other bytes (including framing and padding) are copied.
"""
import hashlib
from bisect import bisect_left
import os
from pathlib import Path
import re
import tempfile

from afpstudio.document_model.structure import TLE
from afpstudio.preflight.media import analyse_media
from afpstudio.security.paths import PathError, safe_output_path, publish_temporary
from afpstudio.viewer.index import IMM, build_index


class EditError(ValueError):
    pass


def _digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_document(document, target, patches, *, overwrite=False, check_cancelled=None, publisher=None):
    check = check_cancelled or (lambda: None)
    check()
    document.check_sources()
    if document.index.truncated or not document.parse_complete:
        raise EditError("Incomplete input cannot be edited or certified as a round trip.")
    if not isinstance(patches, list) or len(patches) > 1000:
        raise EditError("Use an array of at most 1000 edits.")
    target = Path(target)
    try:
        for source in document.source_signatures or {document.path: None}:
            target = safe_output_path(target, [target.parent.resolve()], source=source)
    except PathError as exc:
        raise EditError(str(exc)) from exc
    if target.exists() and not overwrite:
        raise EditError("Output exists; explicit overwrite confirmation is required.")
    offsets = document.index.offsets
    spans = []
    changes = []
    with document.path.open("rb") as source:
        for patch in patches:
            check()
            if not isinstance(patch, dict) or type(patch.get("offset")) is not int:
                raise EditError("Each edit must identify an original structured field offset.")
            offset = patch["offset"]
            i = bisect_left(offsets, offset)
            if i == len(offsets) or offsets[i] != offset:
                raise EditError("Each edit must identify an original structured field offset.")
            if document.index.flags[i]:
                raise EditError("Editing fields with extension, padding or segmentation flags is unsupported.")
            sf_id = document.index.sf_ids[i]
            source.seek(offset + 9)
            payload = source.read(document.index.lengths[i] - 9)
            if sf_id == IMM and set(patch) == {"offset", "medium_map"}:
                name = patch["medium_map"]
                if not isinstance(name, str) or not re.fullmatch(r"[A-Z0-9@$#]{1,8}", name):
                    raise EditError("Use a 1-8 character AFP medium-map name.")
                if name not in document.analysis.medium_maps and name not in document.external_definitions:
                    raise EditError("The target medium map must have a known definition.")
                if len(payload) < 8:
                    raise EditError("Malformed IMM cannot be edited.")
                start, before, after = offset + 9, payload[:8], name.ljust(8).encode("cp500")
            elif sf_id == TLE and set(patch) == {"offset", "triplet_offset", "raw_hex"}:
                pos = 0
                selected = None
                while pos < len(payload):
                    if pos + 2 > len(payload) or payload[pos] < 2 or pos + payload[pos] > len(payload):
                        raise EditError("Malformed TLE triplet sequence.")
                    if offset + 9 + pos == patch["triplet_offset"]:
                        selected = pos
                    pos += payload[pos]
                if selected is None or payload[selected + 1] not in (0x02, 0x36):
                    raise EditError("Select an existing FQN or Attribute Value triplet.")
                try:
                    after = bytes.fromhex(patch["raw_hex"])
                except (ValueError, TypeError) as exc:
                    raise EditError("TLE replacement must contain hexadecimal bytes.") from exc
                start = offset + 9 + selected + 2
                before = payload[selected + 2:selected + payload[selected]]
                if len(before) != len(after) or len(before) < 2 or before[:2] != after[:2]:
                    raise EditError("TLE replacement must preserve length and encoding/name-format bytes.")
            else:
                raise EditError("Only named IMM and same-length raw TLE edits are supported.")
            spans.append((start, before, after))
            changes.append({"offset": offset, "payload_offset": start, "before_hex": before.hex(), "after_hex": after.hex()})
    spans.sort()
    for previous, current in zip(spans, spans[1:]):
        if previous[0] + len(previous[1]) > current[0]:
            raise EditError("Edits overlap or duplicate a payload.")
    target.parent.mkdir(parents=True, exist_ok=True)
    input_digest = _digest(document.path)
    fd, temporary = tempfile.mkstemp(prefix=".afp-edit-", suffix=".part", dir=target.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output, document.path.open("rb") as source:
            cursor = 0
            for start, before, after in spans + [(document.index.file_size, b"", b"")]:
                remaining = start - cursor
                while remaining:
                    check()
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise EditError("Source became truncated during copy.")
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(len(before)) != before:
                    raise EditError("Source bytes changed after editing was requested.")
                output.write(after)
                cursor = start + len(before)
            output.flush()
            os.fsync(output.fileno())
        check()
        parsed = build_index(temporary, input_format=document.index.input_format)
        if parsed.truncated or parsed.sf_ids != document.index.sf_ids:
            raise EditError("Output field verification failed.")
        analysis = analyse_media(parsed, temporary, check_cancelled=check)
        if patches and (analysis.warnings or analysis.ambiguous_maps):
            raise EditError("Output media analysis contains errors.")
        output_digest = _digest(temporary)
        document.check_sources()
        if _digest(document.path) != input_digest:
            raise EditError("Source changed during the operation.")
        if not patches and output_digest != input_digest:
            raise EditError("Unmodified round trip is not byte-identical.")
        check()
        def commit():
            publish_temporary(temporary, target, overwrite=overwrite)
        (publisher or (lambda destination, action: action()))(target, commit)
        return {"path": str(target), "input_sha256": input_digest, "output_sha256": output_digest,
                "byte_identical": input_digest == output_digest, "changes": changes,
                "validation": "framing_and_media_reparsed", "fidelity": "not_production_validated"}
    except FileExistsError as exc:
        raise EditError("Output appeared during generation; confirm replacement explicitly.") from exc
    finally:
        temporary.unlink(missing_ok=True)
