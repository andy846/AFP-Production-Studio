"""Merge/extract complete, self-contained standard-AFP document brackets.

Resource renaming and inherited print-file state cannot yet be proven, so
those jobs are refused rather than emitted with broken references.
"""
import hashlib
from bisect import bisect_left
import os
from pathlib import Path
import tempfile

from afpstudio.document_model.structure import build_structure_index
from afpstudio.preflight.media import analyse_media
from afpstudio.security.paths import safe_output_path, PathError, publish_temporary
from afpstudio.viewer.index import BMM, IMM, build_index
from afpstudio.viewer.resources import resource_dependencies


class DocumentOperationError(ValueError):
    pass


SAFE_FIELDS = {0xD3A8A8, 0xD3A9A8, 0xD3A8AD, 0xD3A9AD, 0xD3A8AF, 0xD3A9AF,
    0xD3A8CD, 0xD3A9CD, 0xD3A8CC, 0xD3A9CC, 0xD3A688, 0xD3A288, 0xD3A788,
    0xD3AB88, 0xD3ABCD, 0xD3ABCC, 0xD3A6AF, 0xD3A8C9, 0xD3A9C9, 0xD3EE9B,
    0xD3A89B, 0xD3A99B, 0xD3A69B, 0xD3A090, 0xD3EEEE}


def combine_documents(documents, target, *, document_numbers=None, overwrite=False,
                      check_cancelled=None, publisher=None):
    check = check_cancelled or (lambda: None)
    if not isinstance(documents, list) or not 1 <= len(documents) <= 20:
        raise DocumentOperationError("Select 1-20 source sessions.")
    if document_numbers is not None and (len(documents) != 1 or not isinstance(document_numbers, list)
        or not document_numbers or any(type(n) is not int or n < 1 for n in document_numbers)
        or len(set(document_numbers)) != len(document_numbers)):
        raise DocumentOperationError("Extraction requires distinct one-based document numbers from one source.")
    target = Path(target)
    parts = []
    for document in documents:
        check()
        document.check_sources()
        index = document.index
        if index.input_format != "standard" or index.truncated or not document.parse_complete:
            raise DocumentOperationError("Merge/extract supports completely indexed standard AFP only.")
        if any(sf_id not in SAFE_FIELDS or index.flags[i] for i, sf_id in enumerate(index.sf_ids)):
            raise DocumentOperationError("Fields affecting closure are unsupported; use byte-identical copy instead.")
        graph = resource_dependencies(index, document.path)
        if graph["definitions"] or graph["references"] or graph["warnings"]:
            raise DocumentOperationError("Resource scope cannot be proven; resource-bearing jobs require a fuller closure engine.")
        structure = build_structure_index(document.path, index)
        if not structure["complete"] or not structure["documents"]:
            raise DocumentOperationError("Document/group brackets are incomplete.")
        lengths = {int(index.offsets[i]): int(index.lengths[i]) for i in range(index.field_count)}
        brackets = structure["documents"]
        cursor = 0
        for item in brackets:
            if item["offset"] != cursor or item["end_offset"] is None:
                raise DocumentOperationError("Print-file state outside document brackets cannot be inherited safely.")
            cursor = item["end_offset"] + lengths[item["end_offset"]]
        if cursor != index.file_size:
            raise DocumentOperationError("Trailing print-file state cannot be extracted safely.")
        selected = brackets
        if document_numbers is not None:
            if max(document_numbers) > len(brackets):
                raise DocumentOperationError("No such document number.")
            selected = [brackets[n - 1] for n in document_numbers]
        try:
            for source in document.source_signatures:
                target = safe_output_path(target, [target.parent.resolve()], source=source)
        except PathError as exc:
            raise DocumentOperationError(str(exc)) from exc
        for item in selected:
            start, end = item["offset"], item["end_offset"] + lengths[item["end_offset"]]
            fields = range(bisect_left(index.offsets, start), bisect_left(index.offsets, end))
            maps = {index.token_at(i) for i in fields if index.sf_ids[i] == BMM and index.token_at(i)}
            if any(index.token_at(i) not in maps for i in fields if index.sf_ids[i] == IMM):
                raise DocumentOperationError("A selected document invokes a medium map outside its own bracket.")
            parts.append((document, start, end))
    if len(parts) > 500:
        raise DocumentOperationError("This operation supports at most 500 complete documents.")
    if target.exists() and not overwrite:
        raise DocumentOperationError("Output exists; explicit replacement confirmation is required.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".afp-documents-", suffix=".part", dir=target.parent)
    temporary = Path(name)
    provenance = []
    try:
        with os.fdopen(fd, "wb") as output:
            for document, start, end in parts:
                output_start = output.tell()
                hasher = hashlib.sha256()
                with document.path.open("rb") as source:
                    source.seek(start)
                    remaining = end - start
                    while remaining:
                        check()
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise DocumentOperationError("Source became truncated.")
                        output.write(chunk)
                        hasher.update(chunk)
                        remaining -= len(chunk)
                provenance.append({"source": document.path.name, "source_start": start, "source_end": end,
                                   "output_start": output_start, "range_sha256": hasher.hexdigest()})
            output.flush()
            os.fsync(output.fileno())
        parsed = build_index(temporary)
        analysis = analyse_media(parsed, temporary, check_cancelled=check)
        structure = build_structure_index(temporary, parsed)
        if parsed.truncated or analysis.ambiguous_maps or analysis.warnings or not structure["complete"]:
            raise DocumentOperationError("Output failed structural/media validation or has medium-map name collisions.")
        if any(item.name not in analysis.medium_maps for item in analysis.invokes):
            raise DocumentOperationError("Output references a medium map outside its self-contained definitions.")
        for document in documents:
            document.check_sources()
        check()
        def commit():
            publish_temporary(temporary, target, overwrite=overwrite)
        (publisher or (lambda action: action()))(commit)
        return {"path": str(target), "documents": len(parts), "pages": parsed.page_count,
                "provenance": provenance, "validation": "complete_document_brackets_and_media",
                "fidelity": "not_production_validated"}
    except FileExistsError as exc:
        raise DocumentOperationError("Output appeared during generation; confirmation required.") from exc
    finally:
        temporary.unlink(missing_ok=True)
