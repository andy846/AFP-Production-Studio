"""Document, page-group and raw TLE index for bracketed AFP streams."""

from afpstudio.viewer.index import BDT, EDT, BNG, ENG, BPG, HEADER_BYTES

TLE = 0xD3A090


def build_structure_index(path, index, max_items=10000, max_raw_bytes=4 * 1024 * 1024, preview_codec=None, max_triplets=100000):
    """Retain raw TLE triplets without guessing their character encoding."""
    if preview_codec not in (None, "cp037", "cp500"):
        raise ValueError("Unsupported preview codec.")
    documents, groups, tags, warnings = [], [], [], []
    document = None
    stack = []
    page = 0
    raw_bytes = 0
    triplet_count = 0
    limited = False
    with path.open("rb") as source:
        for i, sf_id in enumerate(index.sf_ids):
            offset = int(index.offsets[i])
            if len(documents) + len(groups) + len(tags) + len(warnings) >= max_items:
                warnings.append({"code": "INDEX_LIMIT_REACHED", "offset": offset})
                limited = True
                break
            if sf_id == BDT:
                if document is not None:
                    warnings.append({"code": "NESTED_DOCUMENT", "offset": offset})
                document = {"id": len(documents) + 1, "offset": offset, "end_offset": None, "first_page": None, "last_page": None}
                documents.append(document)
            elif sf_id == EDT:
                if document is None:
                    warnings.append({"code": "UNMATCHED_DOCUMENT_END", "offset": offset})
                else:
                    document["end_offset"] = offset
                    document = None
            elif sf_id == BNG:
                if len(stack) >= 64:
                    warnings.append({"code": "PAGE_GROUP_DEPTH_LIMIT", "offset": offset})
                    limited = True
                    break
                group = {"id": len(groups) + 1, "name": index.token_at(i), "offset": offset, "end_offset": None, "parent_id": stack[-1]["id"] if stack else None, "document_id": document["id"] if document else None, "first_page": None, "last_page": None}
                groups.append(group)
                stack.append(group)
            elif sf_id == ENG:
                if stack:
                    stack.pop()["end_offset"] = offset
                else:
                    warnings.append({"code": "UNMATCHED_PAGE_GROUP_END", "offset": offset})
            elif sf_id == BPG:
                page += 1
                for item in ([document] if document else []) + stack:
                    if item["first_page"] is None:
                        item["first_page"] = page
                    item["last_page"] = page
            elif sf_id == TLE:
                length = int(index.lengths[i]) - HEADER_BYTES
                if raw_bytes + length > max_raw_bytes:
                    warnings.append({"code": "INDEX_RAW_LIMIT_REACHED", "offset": offset})
                    limited = True
                    break
                source.seek(offset + HEADER_BYTES)
                payload = source.read(length)
                if len(payload) != length:
                    warnings.append({"code": "TLE_TRUNCATED", "offset": offset})
                    limited = True
                    break
                raw_bytes += length
                entry = {"offset": offset, "document_id": document["id"] if document else None, "page_group_id": stack[-1]["id"] if stack else None, "page": page if index.in_page[i] else None, "raw_hex": payload.hex(), "triplets": [], "status": "raw_encoding_unknown"}
                if index.flags[i]:
                    entry["status"] = "unsupported_flags"
                    warnings.append({"code": "TLE_UNSUPPORTED_FLAGS", "offset": offset})
                else:
                    pos = 0
                    while pos < len(payload):
                        if triplet_count >= max_triplets:
                            entry["status"] = "triplet_limit"
                            warnings.append({"code": "INDEX_TRIPLET_LIMIT", "offset": offset + HEADER_BYTES + pos})
                            limited = True
                            break
                        if len(payload) - pos < 2 or payload[pos] < 2 or pos + payload[pos] > len(payload):
                            entry["status"] = "malformed"
                            warnings.append({"code": "TLE_TRIPLET_INVALID", "offset": offset + HEADER_BYTES + pos})
                            break
                        length = payload[pos]
                        triplet_data = payload[pos + 2:pos + length]
                        triplet = {"id": payload[pos + 1], "offset": offset + HEADER_BYTES + pos, "raw_hex": triplet_data.hex()}
                        if preview_codec and len(triplet_data) >= 2 and (triplet["id"] == 0x36 or (triplet["id"] == 0x02 and triplet_data[1] == 0)):
                            triplet["preview"] = triplet_data[2:].decode(preview_codec, errors="strict").strip()
                            triplet["preview_codec"] = preview_codec
                            triplet["preview_basis"] = "operator_selected_unverified"
                        entry["triplets"].append(triplet)
                        triplet_count += 1
                        pos += length
                tags.append(entry)
                if limited:
                    break
    if document is not None:
        warnings.append({"code": "DOCUMENT_UNCLOSED", "offset": document["offset"]})
    warnings.extend({"code": "PAGE_GROUP_UNCLOSED", "offset": group["offset"]} for group in stack)
    return {"documents": documents, "page_groups": groups, "tags": tags, "warnings": warnings, "complete": not index.truncated and not warnings and not limited, "coverage": "partial_raw_tle"}
