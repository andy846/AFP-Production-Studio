"""The unified AFP document model.

One document in Studio is one :class:`DocumentSession`. It owns the
streaming index, the lazy renderer, the media analysis and the preflight
result, and it is the only thing the API layer talks to.

Everything keeps the byte offset it came from, so any row the operator
sees — a structured field, a page, a preflight warning — can be traced
back to a position in the original file.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from readafp.parser import SF_NAMES

from afpstudio.preflight.engine import PreflightResult, run_preflight
from afpstudio.preflight.media import (
    MediumMapDefinition,
    MediaAnalysis,
    analyse_media,
    load_form_definitions,
)
from afpstudio.printer_profiles.schema import PrinterProfile
from afpstudio.parser_adapters.contract import compare_with_local
from afpstudio.viewer.cache import PageCache
from afpstudio.viewer.index import DocumentIndex, build_index
from afpstudio.viewer.renderer import PageRenderer
from afpstudio.viewer.resources import missing_resources


def sf_name(sf_id: int) -> str:
    """Human readable name of a structured field."""
    return SF_NAMES.get(sf_id, f"Unknown (0x{sf_id:06X})")


@dataclass
class FieldRow:
    """One structured field, as shown in the Structure tab."""

    index: int
    offset: int
    length: int
    sf_id: int
    name: str
    page: int | None
    token: str | None
    in_page: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "offset": self.offset,
            "length": self.length,
            "sf_id": f"0x{self.sf_id:06X}",
            "name": self.name,
            "page": self.page,
            "token": self.token,
            "in_page": self.in_page,
        }


@dataclass
class DocumentSession:
    """One open AFP document."""

    document_id: str
    path: Path
    index: DocumentIndex
    renderer: PageRenderer
    analysis: MediaAnalysis
    preflight: PreflightResult
    external_definitions: dict[str, MediumMapDefinition] = field(
        default_factory=dict
    )
    missing_resources: list[dict[str, Any]] = field(default_factory=list)
    parser_disagreements: list[dict[str, Any]] = field(default_factory=list)
    parser_failures: list[dict[str, Any]] = field(default_factory=list)
    parser: str = "studio"
    parse_complete: bool = True

    @property
    def page_count(self) -> int:
        """Pages the viewer can show."""
        return self.renderer.page_count

    @property
    def indexed_page_count(self) -> int:
        """Pages that carry an explicit Begin Page / End Page bracket."""
        return self.index.page_count

    def page_svg(self, page: int, cache: PageCache | None = None) -> str:
        """Render one page, using the cache when there is one."""
        if cache is not None:
            cached = cache.get(self.document_id, page)
            if cached is not None:
                return cached
        svg = self.renderer.render(page)
        if cache is not None:
            cache.put(self.document_id, page, svg)
        return svg

    def fields(
        self,
        page: int | None = None,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[FieldRow]:
        """Return structured field rows, optionally limited to one page."""
        rows: list[FieldRow] = []
        if page is None:
            candidates: Iterable[int] = range(self.index.field_count)
        else:
            candidates = self.index.fields_for_page(page)
        for i in candidates:
            field_ref = self.index.field(i)
            rows.append(
                FieldRow(
                    index=i,
                    offset=field_ref.offset,
                    length=field_ref.length,
                    sf_id=field_ref.sf_id,
                    name=sf_name(field_ref.sf_id),
                    page=field_ref.page if field_ref.page >= 0 else None,
                    token=field_ref.token,
                    in_page=field_ref.in_page,
                )
            )
            if len(rows) >= limit + offset:
                break
        return rows[offset : offset + limit]

    def page_for_offset(self, offset: int) -> int | None:
        """Map a byte offset back to the page that contains it."""
        return self.index.page_for_offset(offset)

    def search_structure(self, query: str, limit: int = 200) -> list[FieldRow]:
        """Find structured fields by name, id or object name."""
        needle = query.strip().lower()
        if not needle:
            return []
        results: list[FieldRow] = []
        for i in range(self.index.field_count):
            field_ref = self.index.field(i)
            name = sf_name(field_ref.sf_id).lower()
            token = (field_ref.token or "").lower()
            hex_id = f"0x{field_ref.sf_id:06x}"
            if needle in name or needle in token or hex_id == needle:
                results.append(
                    FieldRow(
                        index=i,
                        offset=field_ref.offset,
                        length=field_ref.length,
                        sf_id=field_ref.sf_id,
                        name=sf_name(field_ref.sf_id),
                        page=field_ref.page if field_ref.page >= 0 else None,
                        token=field_ref.token,
                        in_page=field_ref.in_page,
                    )
                )
                if len(results) >= limit:
                    break
        return results

    def search_text(
        self, query: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Find a string in the encoded data stream.

        The AFP data stream holds EBCDIC text, so the query is encoded
        and scanned for. This finds the *encoded* bytes, which is what a
        production operator usually wants ("where does this reference
        number appear"), and it is honest about being a byte search
        rather than a glyph search.
        """
        needle = query.strip()
        if not needle:
            return []
        try:
            encoded = needle.encode("cp500")
        except UnicodeEncodeError:
            encoded = needle.encode("latin-1", errors="ignore")
        if not encoded:
            return []

        results: list[dict[str, Any]] = []
        with self.path.open("rb") as handle:
            window = b""
            window_start = 0
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                buffer = window + chunk
                position = buffer.find(encoded)
                while position != -1:
                    absolute = window_start + position
                    results.append(
                        {
                            "offset": absolute,
                            "page": self.index.page_for_offset(absolute),
                            "match": needle,
                        }
                    )
                    if len(results) >= limit:
                        return results
                    position = buffer.find(encoded, position + 1)
                keep = len(encoded) - 1
                window = buffer[-keep:] if keep else b""
                window_start += len(buffer) - len(window)
        return results

    def summary(self) -> dict[str, Any]:
        """A small description used by the API and the header bar."""
        return {
            "document_id": self.document_id,
            "filename": self.path.name,
            "path": str(self.path),
            "file_size": self.path.stat().st_size if self.path.exists() else 0,
            "page_count": self.page_count,
            "indexed_page_count": self.indexed_page_count,
            "documents": self.analysis.documents,
            "page_groups": list(self.index.page_groups),
            "medium_maps": sorted(self.analysis.medium_maps),
            "form_maps": list(self.analysis.form_maps),
            "preflight_status": self.preflight.status,
            "parse_complete": self.parse_complete,
            "parser": self.parser,
            "missing_resources": len(self.missing_resources),
            "parser_disagreements": len(self.parser_disagreements),
        }


class DocumentStore:
    """Keeps the open documents and their cached pages."""

    def __init__(self, cache: PageCache | None = None) -> None:
        self.cache = cache or PageCache()
        self._documents: dict[str, DocumentSession] = {}
        self._lock = threading.Lock()

    def open(
        self,
        path: Path,
        profile: PrinterProfile | None = None,
        form_defs: list[Path] | None = None,
        progress=None,
        adapter_result: Any = None,
        adapter_error: dict[str, Any] | None = None,
        resource_report: list[dict[str, Any]] | None = None,
        incomplete: bool = False,
        parser: str = "studio",
        check_cancelled=None,
    ) -> DocumentSession:
        """Index, analyse and preflight a document."""
        path = Path(path)
        check = check_cancelled or (lambda: None)
        check()
        index = build_index(path, progress=progress)
        check()
        analysis = analyse_media(index, path)
        check()

        external: dict[str, MediumMapDefinition] = {}
        for form_def in form_defs or ():
            check()
            external.update(load_form_definitions(Path(form_def)))

        resources = (
            resource_report
            if resource_report is not None
            else missing_resources(index, path)
        )

        disagreements: list[dict[str, Any]] = []
        parser_failures: list[dict[str, Any]] = []
        if adapter_result is not None:
            disagreements = compare_with_local(
                adapter_result,
                index.page_count,
                sorted(analysis.medium_maps),
            )
        if adapter_error is not None:
            parser_failures.append(dict(adapter_error))

        renderer = PageRenderer(path, index)
        preflight = run_preflight(
            path,
            index,
            analysis,
            profile=profile,
            external_definitions=external,
            missing_resources=resources,
            parser_disagreements=disagreements,
            parser_failures=parser_failures,
            incomplete=incomplete,
            implicit_pages=max(0, renderer.page_count - index.page_count),
            parser=parser,
        )
        check()

        session = DocumentSession(
            document_id=uuid.uuid4().hex,
            path=path,
            index=index,
            renderer=renderer,
            analysis=analysis,
            preflight=preflight,
            external_definitions=external,
            missing_resources=list(resources),
            parser_disagreements=disagreements,
            parser_failures=parser_failures,
            parser=parser,
            parse_complete=preflight.parse_complete,
        )
        with self._lock:
            self._documents[session.document_id] = session
        return session

    def get(self, document_id: str) -> DocumentSession | None:
        with self._lock:
            return self._documents.get(document_id)

    def close(self, document_id: str) -> bool:
        """Forget a document and drop its cached pages."""
        with self._lock:
            removed = self._documents.pop(document_id, None)
        if removed is None:
            return False
        self.cache.clear(document_id)
        return True

    def list(self) -> list[DocumentSession]:
        with self._lock:
            return list(self._documents.values())
