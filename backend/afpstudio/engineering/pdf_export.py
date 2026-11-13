"""Streaming, lossless raster PDF previews; never production AFP conversion.

Only one page raster is retained. PDF object offsets and page references are
the only writer state that grows with the document. SVG validation is imported
at call time so conversion may dispatch here without an import cycle.
"""
from array import array
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from time import perf_counter
from xml.etree.ElementTree import tostring
import zlib

from PIL import Image
import resvg_py

from afpstudio.security.paths import PathError, publish_temporary, safe_output_path


def _text(value, limit=512):
    """Bound diagnostic text and remove control characters, never stringify objects."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value[:limit] if c.isprintable())


def _safe_details(details):
    """Keep a small JSON-safe allowlist; exceptions must not expose raw SVG/errors."""
    result = {}
    if not isinstance(details, dict):
        return result
    for key in ("page", "warning_count", "issue_count"):
        value = details.get(key)
        if type(value) is int and 0 <= value <= 2**53:
            result[key] = value
    if isinstance(details.get("issues"), list):
        result["issues"] = [_safe_issue(issue) for issue in details["issues"][:128]
                            if isinstance(issue, dict)]
    return result


def _safe_issue(issue):
    result = {key: _text(issue[key], 80 if key != "message" else 512)
              for key in ("code", "category", "impact", "message") if key in issue}
    offset = issue.get("offset")
    if type(offset) is int and 0 <= offset <= 2**53:
        result["offset"] = offset
    return result


class PdfExportError(ValueError):
    """A fatal preview export error with bounded, JSON-safe diagnostic details."""

    def __init__(self, message="PDF preview export failed.", *, code="pdf_export_failed", details=None):
        super().__init__(_text(message))
        self.code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else "pdf_export_failed"
        self.details = _safe_details(details)


class PdfNeedsApproval(PdfExportError):
    """An omitted or uncertain preview requires explicit operator approval."""

    def __init__(self, page, issues):
        super().__init__("Page preview is incomplete; approval is required to export it.",
                         code="incomplete_preview",
                         details={"page": page, "issues": issues, "issue_count": len(issues)})


def _diagnostics(root):
    raw = root.attrib.get("data-render-issues", "[]")
    malformed = {"code": "INVALID_RENDER_DIAGNOSTICS", "category": "invalid_data",
                 "impact": "omission", "message": "Page render diagnostics are malformed or exceed the safety limit."}
    try:
        if len(raw) > 65536 or len(raw.encode("utf-8")) > 65536:
            return [malformed]
        issues = json.loads(raw)
        if not isinstance(issues, list) or len(issues) > 200:
            return [malformed]
        for issue in issues:
            if (not isinstance(issue, dict) or not isinstance(issue.get("code"), str)
                    or not issue["code"] or issue.get("impact") not in ("approximation", "omission")
                    or any(key in issue and not isinstance(issue[key], str)
                           for key in ("message", "category"))):
                return [malformed]
        result = [_safe_issue(issue) for issue in issues]
        # An incomplete status with no omission diagnostics cannot be trusted.
        if root.attrib.get("data-preview-status") == "incomplete" and not any(
                issue["impact"] == "omission" for issue in result):
            result.append(malformed)
        return result
    except (ValueError, RecursionError):
        return [malformed]


def _geometry(document, page, dpi):
    descriptor = document.analysis.page_descriptors.get(page)
    width_mm = getattr(descriptor, "width_mm", None)
    height_mm = getattr(descriptor, "height_mm", None)
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (width_mm, height_mm)):
        raise PdfExportError("Physical page dimensions are unknown; a reliable source descriptor is required.",
                             code="unknown_geometry", details={"page": page + 1})
    # Bound before multiplying to avoid overflow in hostile descriptors.
    if width_mm > 5001 * 25.4 / dpi or height_mm > 5001 * 25.4 / dpi:
        raise PdfExportError("Page exceeds the 5000-pixel side limit.", code="page_too_large",
                             details={"page": page + 1})
    width, height = round(width_mm / 25.4 * dpi), round(height_mm / 25.4 * dpi)
    if not (1 <= width <= 5000 and 1 <= height <= 5000) or width * height > 20_000_000:
        raise PdfExportError("Page exceeds the raster dimensions or 20-million-pixel limit.",
                             code="page_too_large", details={"page": page + 1})
    return width, height, width_mm * 72 / 25.4, height_mm * 72 / 25.4


def _invalid_render_data(issue):
    if issue.get("category") == "invalid_data" or issue["code"] in {
            "UNCLOSED_OBJECT", "UNCLOSED_RESOURCE", "PTOCA_MALFORMED", "IMAGE_DATA_PARTIAL",
            "MALFORMED_DESCRIPTOR", "MALFORMED_INCLUDE", "RENDER_LIMIT", "GOCA_MALFORMED"}:
        return True
    if issue["code"] != "GOCA_PARTIAL":
        return False
    # GOCA currently combines unsupported orders and corruption under one code.
    # Match only fixed decoder corruption diagnostics; unsupported stays approvable.
    message = issue.get("message", "")
    return message in {
        "Unrecognized GOCA segment bytes were skipped.",
        "A GOCA segment header is truncated.", "A GOCA segment is truncated.",
        "A GOCA drawing order is truncated.", "An extended GOCA drawing order is truncated.",
    } or re.fullmatch(r"GOCA drawing order 0x[0-9A-Fa-f]{4} could not be decoded\.", message) is not None


def _number(value):
    return format(value, ".10f").rstrip("0").rstrip(".")


class _PdfWriter:
    """PDF 1.4 with RGB Flate image streams, indirect lengths and classic xref."""

    def __init__(self, output):
        self.output = output
        self.offsets = array("Q", [0, 0, 0, 0])  # catalog, pages, info reserved
        self.pages = array("Q")
        output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    def _begin(self, object_id):
        self.offsets[object_id] = self.output.tell()
        self.output.write(f"{object_id} 0 obj\n".encode("ascii"))

    def _object(self, object_id, data):
        self._begin(object_id)
        self.output.write(data)
        self.output.write(b"\nendobj\n")

    def add_page(self, image, width_points, height_points):
        image_id = len(self.offsets)
        length_id, content_id, page_id = image_id + 1, image_id + 2, image_id + 3
        self.offsets.extend([0] * 4)
        self._begin(image_id)
        width, height = image.size
        self.output.write((f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                           f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                           f"/Length {length_id} 0 R >>\nstream\n").encode("ascii"))
        start = self.output.tell()
        compressor = zlib.compressobj()
        rows = max(1, 1024 * 1024 // (width * 3))
        for top in range(0, height, rows):
            band = image.crop((0, top, width, min(height, top + rows)))
            try:
                self.output.write(compressor.compress(band.tobytes()))
            finally:
                band.close()
        self.output.write(compressor.flush())
        length = self.output.tell() - start
        self.output.write(b"\nendstream\nendobj\n")
        self._object(length_id, str(length).encode("ascii"))
        w, h = _number(width_points), _number(height_points)
        commands = f"q\n{w} 0 0 {h} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
        self._object(content_id, f"<< /Length {len(commands)} >>\nstream\n".encode("ascii")
                     + commands + b"endstream")
        self._object(page_id, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
                              f"/Resources << /XObject << /Im0 {image_id} 0 R >> >> "
                              f"/Contents {content_id} 0 R >>").encode("ascii"))
        self.pages.append(page_id)
        self.output.flush()

    def finish(self, warning_count):
        self._object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        self._begin(2)
        self.output.write(f"<< /Type /Pages /Count {len(self.pages)} /Kids [".encode("ascii"))
        for page_id in self.pages:
            self.output.write(f"{page_id} 0 R ".encode("ascii"))
        self.output.write(b"] >>\nendobj\n")
        # Fixed ASCII metadata only: source and customer text never enter PDF syntax.
        subject = (b"Preview only - not production validated; warnings accepted"
                   if warning_count else b"Preview only - not production validated")
        self._object(3, b"<< /Title (AFP raster preview - not production validated) "
                     b"/Producer (AFP Studio preview exporter) /Subject (" + subject + b") >>")
        xref = self.output.tell()
        self.output.write(f"xref\n0 {len(self.offsets)}\n0000000000 65535 f \n".encode("ascii"))
        for offset in self.offsets[1:]:
            if offset >= 10_000_000_000:
                raise PdfExportError("PDF exceeds the classic cross-reference size limit.", code="pdf_too_large")
            self.output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
        self.output.write((f"trailer\n<< /Size {len(self.offsets)} /Root 1 0 R /Info 3 0 R >>\n"
                           f"startxref\n{xref}\n%%EOF\n").encode("ascii"))


def export_pdf(document, target, pages=None, *, dpi=150, overwrite=False,
               allow_incomplete=False, check_cancelled=None, progress=None, publisher=None):
    """Atomically export all selected zero-based sorted pages as raster previews.

    ``progress(ratio)`` reports each flushed page. Cancellation/source exceptions
    propagate unchanged. ``publisher(destination, action)`` must call action
    synchronously, just as in convert_preview. Warnings/pages are one-based;
    warning_count counts every issue, while warnings retains at most 200 entries.
    """
    started = perf_counter()
    if type(dpi) is not int or not 72 <= dpi <= 300:
        raise PdfExportError("Select an integer resolution from 72 to 300 dpi.", code="invalid_dpi")
    if type(overwrite) is not bool or type(allow_incomplete) is not bool:
        raise PdfExportError("PDF confirmation options must be booleans.", code="invalid_options")
    check = check_cancelled or (lambda: None)

    def checkpoint():
        check()
        document.check_sources()

    checkpoint()
    if not document.parse_complete or getattr(document.index, "truncated", False):
        raise PdfExportError("Structurally incomplete source documents cannot be exported.", code="incomplete_source")
    count = document.page_count
    if pages is None:
        selected = range(count)
    elif (not isinstance(pages, list) or not pages
          or any(type(p) is not int or not 0 <= p < count for p in pages)
          or any(a >= b for a, b in zip(pages, pages[1:]))):
        raise PdfExportError("Select a nonempty, sorted list of distinct zero-based pages.", code="invalid_pages")
    else:
        selected = list(pages)
    if not selected:
        raise PdfExportError("No pages are available for export.", code="invalid_pages")

    # Apply the final suffix BEFORE source/alias and overwrite checks.
    try:
        target = Path(target).with_suffix(".pdf")
        roots = [target.parent.resolve()]
        def validate_destination():
            for source in document.source_signatures or {document.path: None}:
                if safe_output_path(target, roots, source=source) != target:
                    raise PathError("Output location changed before publishing.")
        # Resolve initially; the commit-time check must require the same location.
        target = safe_output_path(target, roots)
        validate_destination()
    except (PathError, ValueError, OSError) as exc:
        raise PdfExportError("Output must be a safe PDF path distinct from every source.", code="unsafe_output") from exc
    if target.exists() and not overwrite:
        raise PdfExportError("Output exists; explicit replacement confirmation is required.", code="output_exists")

    # Lazy by design: conversion is permitted to import this module for dispatch.
    from afpstudio.engineering.conversion import validate_svg

    temporary = None
    warnings = []
    warning_count = 0
    warning_pages = []
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".afp-pdf-", suffix=".part", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as output:
            writer = _PdfWriter(output)
            for done, page in enumerate(selected, 1):
                checkpoint()
                width, height, width_points, height_points = _geometry(document, page, dpi)
                try:
                    svg = document.page_svg(page)
                except Exception as exc:
                    checkpoint()
                    raise PdfExportError("Page renderer failed.", code="render_failed", details={"page": page + 1}) from exc
                checkpoint()
                try:
                    root = validate_svg(svg)
                except Exception as exc:
                    raise PdfExportError("Page SVG failed safety or image validation.", code="invalid_svg",
                                         details={"page": page + 1}) from exc
                issues = _diagnostics(root)
                if any(issue["code"] == "INVALID_RENDER_DIAGNOSTICS" for issue in issues):
                    raise PdfExportError("Page render diagnostics are malformed or exceed the safety limit.",
                                         code="invalid_render_diagnostics", details={"page": page + 1})
                if any(_invalid_render_data(issue) for issue in issues):
                    raise PdfExportError("Page render data is malformed, truncated or incomplete.",
                                         code="invalid_render_data", details={"page": page + 1, "issues": issues})
                if any(issue["code"] == "IMAGE_NOT_RENDERED" for issue in issues):
                    raise PdfExportError("A source image could not be decoded or placed.", code="image_decode_failed",
                                         details={"page": page + 1, "issues": issues})
                if any(issue["impact"] == "omission" for issue in issues) and not allow_incomplete:
                    raise PdfNeedsApproval(page + 1, issues)
                if issues:
                    warning_count += len(issues)
                    warning_pages.append(page + 1)
                    warnings.extend(dict(issue, page=page + 1) for issue in issues[:200 - len(warnings)])
                try:
                    # resvg fits width/height to the intrinsic SVG aspect ratio.
                    # Pin its viewport to the verified physical page raster first.
                    root.set("width", str(width))
                    root.set("height", str(height))
                    root.set("preserveAspectRatio", "none")
                    svg = tostring(root, encoding="unicode")
                    png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height,
                                               background="#ffffff", log_information=False)
                    with io.BytesIO(png) as buffer, Image.open(buffer) as decoded:
                        try:
                            if decoded.size != (width, height):
                                raise ValueError("Unexpected raster dimensions")
                            image = decoded.convert("RGB")
                            try:
                                writer.add_page(image, width_points, height_points)
                            finally:
                                image.close()
                        finally:
                            decoded.close()
                    del png, svg, root
                except Exception as exc:
                    raise PdfExportError("Page raster rendering, decoding or writing failed.", code="raster_failed",
                                         details={"page": page + 1}) from exc
                checkpoint()
                if progress:
                    progress(done / len(selected))
            writer.finish(warning_count)
            output.flush()
            os.fsync(output.fileno())
        checkpoint()
        size = temporary.stat().st_size

        def commit():
            checkpoint()
            validate_destination()
            publish_temporary(temporary, target, overwrite=overwrite)

        (publisher or (lambda destination, action: action()))(target, commit)
        return {"path": str(target), "format": "pdf", "pages": [p + 1 for p in selected],
                "bytes": size, "dpi": dpi, "fidelity": "preview_not_production_validated",
                "page_count": len(selected), "elapsed_seconds": perf_counter() - started,
                "status": "completed_with_warnings" if warning_count else "completed",
                "warnings": warnings, "warning_count": warning_count, "warning_pages": warning_pages}
    except FileExistsError as exc:
        raise PdfExportError("Output appeared during export; explicit replacement confirmation is required.",
                             code="output_exists") from exc
    except PathError as exc:
        raise PdfExportError("Output location is no longer safe.", code="unsafe_output") from exc
    except OSError as exc:
        raise PdfExportError("PDF output could not be written or published.", code="output_failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
