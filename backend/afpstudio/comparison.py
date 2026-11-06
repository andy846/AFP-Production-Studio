"""Local, bounded AFP comparison. A preview match is not print verification."""

import csv
import hashlib
import io
import json
import threading
from collections import Counter
from dataclasses import dataclass, field

from PIL import Image, ImageChops
from openpyxl import Workbook
import resvg_py

from afpstudio.engineering.conversion import ConversionError, validate_svg
from afpstudio.viewer.renderer import RenderError, TextSearchReplay

MAX_PAGES = 20_000


class ComparisonError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class PageEvidence:
    raw: str | None
    offset: int | None
    size: tuple
    media: dict
    text: str | None = None
    positions: str | None = None
    excerpt: str = ""
    text_offset: int | None = None


def collect(document, codepage, progress):
    index = document.index
    total = document.page_count
    replay = TextSearchReplay(document.path, index)
    media = {row.page_index: row for row in document.preflight.pages}
    rows = []
    decode_more = True
    with document.path.open("rb") as handle:
        for i in range(min(total, MAX_PAGES)):
            progress(i, total)
            item = media.get(i)
            pgd = document.analysis.page_descriptors.get(i)
            size = (pgd.width_mm, pgd.height_mm) if pgd else (None, None)
            evidence = PageEvidence(None, None, size, {
                "requested": {key: getattr(item.requested, key) for key in
                    ("medium_map", "form_map", "media_type", "media_destination", "duplex", "copies")},
                "resolved": {key: getattr(item.resolved, key) for key in ("stock_name", "tray", "duplex")}
            } if item else {})
            if i < index.page_count:
                evidence.offset = index.page_starts[i]
                handle.seek(evidence.offset)
                remaining = index.page_ends[i] - evidence.offset
                h = hashlib.sha256()
                while remaining > 0:
                    progress(i, total)
                    chunk = handle.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ComparisonError("Source changed while reading a page.")
                    h.update(chunk)
                    remaining -= len(chunk)
                evidence.raw = h.hexdigest()
                decode_more = decode_more and replay.allow_page(i)
                if decode_more:
                    try:
                        runs = document.renderer.text_runs(i, codepage=codepage, replay=replay)
                    except RenderError:
                        runs = None
                    if runs and runs["complete"]:
                        items = runs["items"]
                        text = " ".join(" ".join(row["text"].split()) for row in items).strip()
                        evidence.text = _digest(text)
                        evidence.excerpt = text[:160]
                        evidence.text_offset = next((row["offset"] for row in items if row["offset"] is not None), None)
                        scale = 25.4 / runs["units_per_inch"]
                        evidence.positions = _digest([
                            (round(row["x"] * scale, 2), round(row["y"] * scale, 2), row["orientation"])
                            for row in items
                        ])
            rows.append(evidence)
    document.check_sources()
    return rows


def _unique(values):
    counts = Counter(value for value in values if value is not None)
    return {value: i for i, value in enumerate(values, 1) if value is not None and counts[value] == 1}


def align(left, right, manual=()):
    """Use unique anchors, reject crossings, and leave unequal gaps unpaired."""
    anchors = sorted(tuple(pair) for pair in manual)
    if any(not (1 <= a <= len(left) and 1 <= b <= len(right)) for a, b in anchors):
        raise ComparisonError("Manual anchor page is outside the comparison.")
    if any(a >= c or b >= d for (a, b), (c, d) in zip(anchors, anchors[1:])):
        raise ComparisonError("Manual anchors must be unique and in the same page order.")
    manual_limits = [(0, 0), *anchors, (len(left) + 1, len(right) + 1)]
    candidates = []
    for attr, kind in (("raw", "original"), ("text", "text")):
        a = _unique([getattr(row, attr) if row.text is None or row.excerpt else None for row in left])
        b = _unique([getattr(row, attr) if row.text is None or row.excerpt else None for row in right])
        candidates.append(sorted((a[key], b[key], kind) for key in a.keys() & b.keys()))

    def ordered(items, limits):
        """Accept only candidates inside monotone segments; discard crossing clusters."""
        from bisect import bisect_left
        xs = [a for a, _ in limits]
        groups = {}
        for a, b, kind in items:
            j = bisect_left(xs, a)
            if limits[j - 1][0] < a < limits[j][0] and limits[j - 1][1] < b < limits[j][1]:
                groups.setdefault(j, []).append((a, b, kind))
        for group in groups.values():
            suffix_min = [float("inf")] * (len(group) + 1)
            for i in reversed(range(len(group))):
                suffix_min[i] = min(suffix_min[i + 1], group[i][1])
            prefix_max = 0
            for i, entry in enumerate(group):
                b = entry[1]
                if prefix_max < b < suffix_min[i + 1]:
                    yield entry
                prefix_max = max(prefix_max, b)

    original = list(ordered(candidates[0], manual_limits))
    text_limits = sorted([*manual_limits, *((a, b) for a, b, _ in original)])
    chosen = sorted([*((a, b, "manual") for a, b in anchors), *original,
                     *ordered(candidates[1], text_limits)])
    out = []
    a = b = 1
    for end_a, end_b, kind in [*chosen, (len(left) + 1, len(right) + 1, "")]:
        if end_a - a == end_b - b:
            out.extend((x, y, "review") for x, y in zip(range(a, end_a), range(b, end_b)))
        else:
            out.extend((x, None, "left_only") for x in range(a, end_a))
            out.extend((None, y, "right_only") for y in range(b, end_b))
        if kind:
            out.append((end_a, end_b, kind))
        a, b = end_a + 1, end_b + 1
    return out


def compare_rows(left, right, manual):
    rows = []
    for a, b, basis in align(left, right, manual):
        old = left[a - 1] if a else None
        new = right[b - 1] if b else None
        changes = []
        unknown = []
        if old and new:
            if old.text is None or new.text is None:
                unknown.append("decoded text")
            elif old.text != new.text:
                changes.append("text")
            elif old.positions != new.positions:
                changes.append("position")
            if None in old.size or None in new.size:
                unknown.append("page size")
            elif old.size != new.size:
                changes.append("size")
            for level in ("requested", "resolved"):
                if not old.media or not new.media:
                    unknown.append(level + " media")
                    continue
                for key, value in old.media[level].items():
                    other = new.media[level][key]
                    if value != other:
                        changes.append(level + "." + key)
                    if value is None or other is None:
                        unknown.append(level + "." + key)
            if basis == "review":
                unknown.append("page pairing")
        else:
            unknown.append("page pairing")
        rows.append({
            "left": a, "right": b, "basis": basis, "changes": changes, "unknown": unknown,
            "left_offset": (old.text_offset if old.text_offset is not None else old.offset) if old else None,
            "right_offset": (new.text_offset if new.text_offset is not None else new.offset) if new else None,
            "left_excerpt": old.excerpt if old and "text" in changes else "",
            "right_excerpt": new.excerpt if new and "text" in changes else "",
            "left_size": old.size if old else None, "right_size": new.size if new else None,
            "left_media": old.media if old else {}, "right_media": new.media if new else {},
        })
    return rows


@dataclass
class Comparison:
    documents: tuple
    evidence: tuple
    codepage: str
    rows: list
    anchors: list = field(default_factory=list)
    revision: int = 1
    visual: tuple | None = None
    visual_results: dict = field(default_factory=dict)

    def sources(self):
        return [{"id": doc.document_id, "name": doc.path.name, "sha256": doc.preflight.file_digest,
                 "pages": doc.page_count, "indexed_complete": doc.parse_complete,
                 "covered_pages": len(evidence)} for doc, evidence in zip(self.documents, self.evidence)]


class ComparisonStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {}

    def put(self, key, comparison):
        with self.lock:
            self.items[key] = comparison
            while len(self.items) > 4:
                self.items.pop(next(iter(self.items)))

    def discard(self, document_id):
        with self.lock:
            for key in [key for key, item in self.items.items()
                        if any(doc.document_id == document_id for doc in item.documents)]:
                del self.items[key]


def visual_difference(old, new, a, b, size, cache, check):
    if not size or None in size or any(value <= 0 for value in size):
        raise ComparisonError("Physical page dimensions are unknown.")
    w, h = (round(value / 25.4 * 144) for value in size)
    if not 1 <= w <= 5000 or not 1 <= h <= 5000 or w * h > 4_000_000:
        raise ComparisonError("Page exceeds the preview difference size limit.")
    images = []
    for doc, page in ((old, a), (new, b)):
        check()
        svg = doc.page_svg(page - 1, cache)
        validate_svg(svg)
        png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h,
                                   background="#ffffff", log_information=False)
        with Image.open(io.BytesIO(png)) as img:
            images.append(img.convert("RGB"))
    check()
    r, g, b = ImageChops.difference(*images).split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda n: 255 if n else 0)
    count = w * h - mask.histogram()[0]
    overlay = Image.composite(Image.new("RGB", (w, h), "#d52685"), images[1], mask)
    output = io.BytesIO()
    overlay.save(output, "PNG")
    return output.getvalue(), {"changed_pixels": count, "total_pixels": w * h, "dpi": 144}


def comparison_report(sources, rows, codepage, fmt, include_text, visuals):
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
            return "'" + value
        return value

    names = ["Old file", "Old SHA-256", "New file", "New SHA-256", "Old page", "New page", "Pairing",
             "Changes", "Unverified", "Old offset", "New offset", "Fallback code page", "Visual preview",
             "Old size (mm)", "New size (mm)", "Old media", "New media", "Actual media",
             "Old coverage", "New coverage"]
    if include_text:
        names += ["Old preview excerpt", "New preview excerpt"]
    if fmt == "csv":
        stream = io.StringIO(newline="")
        append = csv.writer(stream).writerow
    else:
        workbook = Workbook(write_only=True)
        summary = workbook.create_sheet("Summary")
        summary.append(["Source", "File", "SHA-256", "Pages", "Covered", "Index complete"])
        for label, item in zip(("Old", "New"), sources):
            summary.append([label, safe(item["name"]), item["sha256"], item["pages"],
                            item["covered_pages"], item["indexed_complete"]])
        summary.append(["Actual printer media", "Unknown"])
        summary.append(["Raster preview", "Studio preview only; not RIP verified"])
        append = workbook.create_sheet("Differences").append
    append(names)
    for i, row in enumerate(rows):
        visual = visuals.get(i)
        state = ("Changed pixels: " + str(visual["changed_pixels"]) + " / " + str(visual["total_pixels"])
                 if visual else "Not compared")
        record = [sources[0]["name"], sources[0]["sha256"], sources[1]["name"], sources[1]["sha256"],
                  row["left"], row["right"], row["basis"], ", ".join(row["changes"]),
                  ", ".join(row["unknown"]), row["left_offset"], row["right_offset"], codepage, state,
                  json.dumps(row["left_size"]), json.dumps(row["right_size"]),
                  json.dumps(row["left_media"], ensure_ascii=False), json.dumps(row["right_media"], ensure_ascii=False),
                  "Unknown", *[f'{doc["covered_pages"]} of {doc["pages"]} indexed'
                               + (" (incomplete source)" if not doc["indexed_complete"] else "") for doc in sources]]
        if include_text:
            record += [row["left_excerpt"], row["right_excerpt"]]
        append([safe(value) for value in record])
    if fmt == "csv":
        return stream.getvalue().encode("utf-8-sig")
    binary = io.BytesIO()
    workbook.save(binary)
    return binary.getvalue()
