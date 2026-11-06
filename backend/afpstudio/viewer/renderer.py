"""Lazy, single page rendering.

readAFP's renderer builds its page model by walking the whole document
once, because fonts, code pages, overlays and named images are declared
before the pages that use them. Rendering all pages up front is fine for
a letter and hopeless for a 20 000 page print job, so this module
replays only the state a single page actually depends on:

* every field inside a replay scope (resource group, page segment,
  overlay, object container, active environment group),
* every document level field, and
* the fields of the page being rendered.

Documents also differ in how they bracket pages. Most use Begin Page and
End Page; some older generators put presentation text straight into the
data stream, and readAFP paginates that content itself. Both shapes are
supported: bracketed pages are rendered on demand, and unbracketed
content is paginated once and cached.

The result is compared against the upstream whole-document render by the
differential test, which is what keeps the two paths honest.
"""

from __future__ import annotations

from bisect import bisect_left
from pathlib import Path

from readafp.parser import StructuredField
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg

from .index import ALWAYS_INCLUDE, PTX, DocumentIndex


class RenderError(Exception):
    """Raised when a page cannot be rendered."""


def select_global_fields(index: DocumentIndex) -> list[int]:
    """Return the fields that contribute document-wide state."""
    return [
        i
        for i in range(index.field_count)
        if index.scopes[i]
        or not index.in_page[i]
        or index.sf_ids[i] in ALWAYS_INCLUDE
    ]


def select_replay_fields(index: DocumentIndex, page: int) -> list[int]:
    """Return the field indexes needed to render ``page``."""
    selected: list[int] = []
    for i in range(index.field_count):
        if index.scopes[i] or not index.in_page[i]:
            # State scope, or document level (outside every page bracket).
            selected.append(i)
        elif index.pages[i] == page:
            selected.append(i)
        elif index.sf_ids[i] in ALWAYS_INCLUDE:
            selected.append(i)
    return selected


def _read_fields(
    handle, index: DocumentIndex, selection: list[int]
) -> list[StructuredField]:
    """Read the selected fields, one seek per contiguous run."""
    fields: list[StructuredField] = []
    if sum(index.lengths[i] for i in selection) > 128 * 1024 * 1024:
        raise RenderError("Page replay exceeds the 128 MiB input limit. This page cannot be previewed safely.")
    i = 0
    total = len(selection)
    while i < total:
        j = i
        while (
            j + 1 < total
            and index.offsets[selection[j + 1]]
            == index.offsets[selection[j]] + index.lengths[selection[j]]
        ):
            j += 1

        first = selection[i]
        last = selection[j]
        start = index.offsets[first]
        end = index.offsets[last] + index.lengths[last]
        handle.seek(start)
        chunk = handle.read(end - start)
        for k in range(i, j + 1):
            fi = selection[k]
            offset = index.offsets[fi]
            length = index.lengths[fi]
            payload = chunk[offset - start : offset - start + length]
            fields.append(
                StructuredField(
                    offset=offset,
                    sf_id=index.sf_ids[fi],
                    flags=index.flags[fi],
                    sequence=index.sequences[fi],
                    data=payload[9:],
                )
            )
        i = j + 1
    return fields


class PageRenderer:
    """Renders single pages on demand from an indexed document."""

    def __init__(
        self,
        path: Path,
        index: DocumentIndex,
        codepage: str = "cp500",
        embed_small_fonts: bool = False,
    ) -> None:
        self.path = Path(path)
        self.index = index
        self.codepage = codepage
        self.embed_small_fonts = embed_small_fonts
        self._extra: list[str] | None = None

    # -- page inventory --------------------------------------------------

    def _extra_pages(self) -> list[str]:
        """Pages that are not bracketed by Begin Page / End Page."""
        if self._extra is not None:
            return self._extra
        selection = select_global_fields(self.index)
        has_unbracketed_text = any(
            self.index.sf_ids[i] == PTX and not self.index.in_page[i]
            for i in selection
        )
        if self.index.page_count > 0 and not has_unbracketed_text:
            self._extra = []
            return self._extra
        with self.path.open("rb") as handle:
            fields = _read_fields(handle, self.index, selection)
        pages = extract_pages(fields, self.codepage, self.embed_small_fonts)
        self._extra = [page_to_svg(p) for p in pages]
        return self._extra

    @property
    def page_count(self) -> int:
        """Total number of pages the viewer will show."""
        return self.index.page_count + len(self._extra_pages())

    # -- rendering -------------------------------------------------------

    def text_runs(self, page: int, *, codepage: str, global_fields: list[int] | None = None):
        """Positioned preview text; offsets identify PTX fields, not glyph bytes."""
        if codepage not in ("cp037", "cp500"):
            raise ValueError("Select a supported fallback code page explicitly.")
        if page < 0 or page >= self.index.page_count:
            raise RenderError("Text extraction currently requires an explicitly bracketed page.")
        if global_fields is None:
            selection = select_replay_fields(self.index, page)
        else:
            # The document search computes shared fields once. Merge only the
            # selected page's bracket while retaining the physical field order.
            bounds = self.index.fields_for_page(page)
            low = bisect_left(global_fields, bounds.start)
            high = bisect_left(global_fields, bounds.stop, lo=low)
            selection = global_fields[:low]
            selection.extend(i for i in bounds if self.index.pages[i] == page
                             or self.index.scopes[i] or not self.index.in_page[i]
                             or self.index.sf_ids[i] in ALWAYS_INCLUDE)
            selection.extend(global_fields[high:])
        with self.path.open("rb") as handle:
            fields = _read_fields(handle, self.index, selection)
        pages = extract_pages(fields, codepage, self.embed_small_fonts)
        if not pages:
            raise RenderError("No page text model was produced.")
        model = pages[0]
        runs = [run for run in model.texts + model.text_layer if not run.preview_only]
        items = [{"text": run.text, "x": run.x, "y": run.y, "font_id": run.font_id,
            "font_family": run.font_family, "font_size": run.font_size, "orientation": run.orientation,
            "page": page + 1, "offset": run.src, "offset_kind": "structured_field"} for run in runs]
        warnings = (["Some embedded glyphs have no reliable Unicode text. "
                     "See the page preview for their visible shapes or missing glyph markers."]
                    if model.coded_text_incomplete else [])
        return {"items": items, "basis": "readAFP_preview", "fallback_codepage": codepage,
                "codepage_basis": "embedded_mapping_or_operator_fallback",
                "units": "page_logical_units", "units_per_inch": model.units_per_inch,
                "complete": not (model.truncated or self.index.truncated or model.coded_text_incomplete),
                "warnings": len(warnings), "warning_details": warnings,
                "coverage": "preview_text_runs_not_production_verified"}

    def render(self, page: int) -> str:
        """Return the SVG for the given zero based page number."""
        if page < 0:
            raise RenderError(f"Page {page} is outside the document.")
        if page < self.index.page_count:
            selection = select_replay_fields(self.index, page)
            with self.path.open("rb") as handle:
                fields = _read_fields(handle, self.index, selection)
            pages = extract_pages(fields, self.codepage, self.embed_small_fonts)
            if not pages:
                raise RenderError("No renderable page was produced.")
            # Only the requested page bracket survives the replay, so the
            # bracketed page is the first entry.
            return page_to_svg(pages[0])

        extra = self._extra_pages()
        offset = page - self.index.page_count
        if offset >= len(extra):
            raise RenderError(f"Page {page} is outside the document.")
        return extra[offset]

    def render_all_eager(self, limit: int | None = None) -> list[str]:
        """Render every page the way readAFP does, for comparison only."""
        with self.path.open("rb") as handle:
            fields = _read_fields(
                handle, self.index, list(range(self.index.field_count))
            )
        pages = extract_pages(fields, self.codepage, self.embed_small_fonts)
        if limit is not None:
            pages = pages[:limit]
        return [page_to_svg(p) for p in pages]
