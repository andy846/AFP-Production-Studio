"""Font resource checks.

An AFP document can reference a coded font, a code page or a character
set that it does not embed. The text still renders — with a substitute
font — but the output is not what the producer asked for, so the
preflight reports it.

The check works from the streaming index, so it costs one pass over the
field headers and a seek to each map-coded-font field rather than a
full in-memory parse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from readafp.triplets import codec_for_codepage_name, mcf_resource_names

from .index import BCF, BFN, BCP, MCF1, MCF2, DocumentIndex

#: The embedded resource kind each begin field carries.
EMBED_FIELDS = {
    "code page": BCP,
    "character set": BFN,
    "coded font": BCF,
}


def missing_resources(
    index: DocumentIndex, path: Path
) -> list[dict[str, Any]]:
    """Return the resources the document references but does not embed."""
    referenced: dict[str, set[str]] = {
        "coded font": set(),
        "code page": set(),
        "character set": set(),
    }
    embedded: dict[str, set[str]] = {kind: set() for kind in EMBED_FIELDS}

    with Path(path).open("rb") as handle:
        for i in range(index.field_count):
            sf_id = index.sf_ids[i]
            if sf_id in (MCF1, MCF2):
                handle.seek(index.offsets[i] + 9)
                data = handle.read(max(0, index.lengths[i] - 9))
                for kind, names in mcf_resource_names(
                    data, sf_id == MCF1
                ).items():
                    referenced[kind] |= names
            elif sf_id in EMBED_FIELDS.values():
                kind = next(k for k, v in EMBED_FIELDS.items() if v == sf_id)
                token = index.token_at(i)
                if token:
                    embedded[kind].add(token)

    missing: list[dict[str, Any]] = []
    for kind in ("coded font", "code page", "character set"):
        for name in sorted(referenced[kind] - embedded[kind]):
            codec = (
                codec_for_codepage_name(name) if kind == "code page" else None
            )
            missing.append({"kind": kind, "name": name, "codec": codec})
    return missing
