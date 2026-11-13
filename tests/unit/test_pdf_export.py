"""Streaming raster PDF contract, read independently with pypdf and MuPDF."""
import importlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

import pymupdf as fitz
import pytest
from PIL import Image
from pypdf import PdfReader

from afpstudio.document_model.model import DocumentSession, DocumentStore, SourceChangedError, source_signature
from afpstudio.testing import synthetic as sf


@pytest.fixture
def engine():
    # Import inside the fixture so the initial red run identifies the missing engine.
    assert importlib.util.find_spec("afpstudio.engineering.pdf_export"), "PDF engine is not implemented"
    return importlib.import_module("afpstudio.engineering.pdf_export")


def svg(fill=None, issues=None, diagnostic=None):
    attr = ""
    if issues is not None or diagnostic is not None:
        attr = " data-render-issues=" + quoteattr(diagnostic if diagnostic is not None else json.dumps(issues))
    content = f'<rect width="100" height="100" fill="{fill}"/>' if fill else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"{attr}>{content}</svg>'


class Document:
    """Small lazy document; signatures use the real document source checker."""
    check_sources = DocumentSession.check_sources

    def __init__(self, tmp_path, count=1, content=None, size=(25.4, 12.7)):
        self.path = tmp_path / "source.afp"
        self.path.write_bytes(b"original AFP evidence")
        self.source_signatures = {self.path: source_signature(self.path)}
        self.parse_complete = True
        self.index = SimpleNamespace(page_count=count, truncated=False)
        self.page_count = count
        self.analysis = SimpleNamespace(page_descriptors={
            p: SimpleNamespace(width_mm=size[0], height_mm=size[1]) for p in range(count)
        })
        self.content = content or (lambda p: svg())
        self.rendered = []

    def page_svg(self, page):
        self.rendered.append(page)
        return self.content(page)


def no_parts(tmp_path):
    assert not list(tmp_path.glob("*.part"))


def test_many_pages_lossless_rgb_exact_media_boxes_and_metadata(engine, tmp_path):
    doc = Document(tmp_path, 25, lambda p: svg("#ff0000" if p % 2 else None))
    doc.analysis.page_descriptors[1] = SimpleNamespace(width_mm=12.7, height_mm=25.4)
    progress = []
    result = engine.export_pdf(doc, tmp_path / "preview.tmp", dpi=72, progress=progress.append)
    output = tmp_path / "preview.pdf"
    reader = PdfReader(output, strict=True)
    assert len(reader.pages) == 25
    assert reader.pdf_header == "%PDF-1.4"
    assert tuple(float(x) for x in reader.pages[0].mediabox) == (0, 0, 72, 36)
    assert tuple(float(x) for x in reader.pages[1].mediabox) == (0, 0, 36, 72)
    for number, page in enumerate(reader.pages):
        image, = page["/Resources"]["/XObject"].values()
        image = image.get_object()
        assert image["/Filter"] == "/FlateDecode"
        assert image["/ColorSpace"] == "/DeviceRGB"
        assert image["/BitsPerComponent"] == 8
        assert image.get_data() == (b"\xff\x00\x00" if number % 2 else b"\xff\xff\xff") * (72 * 36)
    assert "preview" in reader.metadata.title.lower()
    assert "not production validated" in str(reader.metadata).lower()
    assert result == {"path": str(output), "format": "pdf", "pages": list(range(1, 26)),
                      "bytes": output.stat().st_size, "dpi": 72,
                      "fidelity": "preview_not_production_validated", "page_count": 25,
                      "elapsed_seconds": result["elapsed_seconds"], "status": "completed",
                      "warnings": [], "warning_count": 0, "warning_pages": []}
    assert result["elapsed_seconds"] >= 0
    assert progress[-1] == 1 and progress == sorted(progress)
    assert doc.rendered == list(range(25))
    assert doc.path.read_bytes() == b"original AFP evidence"
    with fitz.open(output) as pdf:
        assert pdf.page_count == 25
        assert pdf[1].get_pixmap().pixel(5, 5) == (255, 0, 0)
    no_parts(tmp_path)


def test_real_document_descriptor_and_renderer(engine, tmp_path):
    source = tmp_path / "real.afp"
    source.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(25.4, 12.7) + sf.epg() + sf.edt())
    doc = DocumentStore().open(source)
    result = engine.export_pdf(doc, tmp_path / "real.pdf")
    assert result["dpi"] == 150
    page = PdfReader(result["path"]).pages[0]
    assert tuple(float(v) for v in page.mediabox) == (0, 0, 72, 36)


@pytest.mark.parametrize("pages", [[], [1, 0], [0, 0], [True], [-1], [2], (0,), "0"])
def test_invalid_selections_do_not_render_or_publish(engine, tmp_path, pages):
    doc = Document(tmp_path, 2)
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, tmp_path / "bad.pdf", pages)
    assert doc.rendered == []
    assert not (tmp_path / "bad.pdf").exists()


@pytest.mark.parametrize("dpi", [71, 301, True, 150.0, "150", None])
def test_dpi_rejected_before_render(engine, tmp_path, dpi):
    doc = Document(tmp_path)
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, tmp_path / "bad.pdf", dpi=dpi)
    assert not doc.rendered


@pytest.mark.parametrize("dpi", [72, 150, 200, 300])
def test_selected_pages_and_supported_dpi(engine, tmp_path, dpi):
    doc = Document(tmp_path, 4)
    result = engine.export_pdf(doc, tmp_path / "out.pdf", [1, 3], dpi=dpi)
    assert result["pages"] == [2, 4]
    assert result["page_count"] == 2
    assert doc.rendered == [1, 3]
    image = next(iter(PdfReader(result["path"]).pages[0]["/Resources"]["/XObject"].values())).get_object()
    assert (image["/Width"], image["/Height"]) == (dpi, round(dpi / 2))


@pytest.mark.parametrize("size", [(None, 10), (0, 10), (-1, 10), (float("nan"), 10),
                                   (float("inf"), 10), (2000, 10), (1600, 1600), (0.001, 10)])
def test_unreliable_or_oversized_geometry_is_never_overridable(engine, tmp_path, size):
    doc = Document(tmp_path, size=size)
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, tmp_path / "bad.pdf", dpi=72, allow_incomplete=True)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert not (tmp_path / "bad.pdf").exists()
    no_parts(tmp_path)


def test_implicit_pages_require_source_geometry(engine, tmp_path):
    doc = Document(tmp_path, 2)
    doc.index.page_count = 1
    del doc.analysis.page_descriptors[1]
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, tmp_path / "missing.pdf", allow_incomplete=True)
    assert not (tmp_path / "missing.pdf").exists()
    doc.analysis.page_descriptors[1] = SimpleNamespace(width_mm=25.4, height_mm=12.7)
    assert engine.export_pdf(doc, tmp_path / "reliable.pdf")["pages"] == [1, 2]


def test_structural_incompleteness_is_fatal(engine, tmp_path):
    doc = Document(tmp_path)
    doc.parse_complete = False
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, tmp_path / "bad.pdf", allow_incomplete=True)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert not doc.rendered


def test_omissions_require_approval_but_approximations_are_warnings(engine, tmp_path):
    omission = {"code": "MISSING_RESOURCE", "category": "external_resource", "impact": "omission", "message": "Missing image"}
    approximate = {"code": "FONT_APPROXIMATION", "category": "approximation", "impact": "approximation", "message": "Font substitution"}
    doc = Document(tmp_path, 2, lambda p: svg(issues=[approximate if p == 0 else omission]))
    with pytest.raises(engine.PdfNeedsApproval) as error:
        engine.export_pdf(doc, tmp_path / "out.pdf")
    assert error.value.code == "incomplete_preview"
    assert error.value.details["page"] == 2
    assert error.value.details["issues"][0]["code"] == "MISSING_RESOURCE"
    assert not (tmp_path / "out.pdf").exists()
    no_parts(tmp_path)
    result = engine.export_pdf(doc, tmp_path / "out.pdf", allow_incomplete=True)
    assert result["status"] == "completed_with_warnings"
    assert result["warning_count"] == 2 and result["warning_pages"] == [1, 2]
    assert [w["page"] for w in result["warnings"]] == [1, 2]
    assert len(PdfReader(result["path"]).pages) == 2
    assert "warnings" in str(PdfReader(result["path"]).metadata).lower()
    assert "Missing image" not in str(PdfReader(result["path"]).metadata)
    assert engine.export_pdf(doc, tmp_path / "approx.pdf", [0])["status"] == "completed_with_warnings"


@pytest.mark.parametrize("diagnostic", ["{", "{}", "[null]", '[{"code":"X","impact":"surprise"}]',
                                             '[{"code":"X","impact":"approximation","message":[]}]',
                                             "[" * 1500, " " * 70000],
                         ids=["json", "object", "null", "impact", "message", "depth", "length"])
@pytest.mark.parametrize("allow_incomplete", [False, True])
def test_malformed_diagnostics_block_instead_of_silently_becoming_complete(engine, tmp_path, diagnostic, allow_incomplete):
    doc = Document(tmp_path, content=lambda p: svg(diagnostic=diagnostic))
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, tmp_path / "out.pdf", allow_incomplete=allow_incomplete)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert not (tmp_path / "out.pdf").exists()
    no_parts(tmp_path)


def test_warning_storage_is_bounded_but_counts_are_not_lost(engine, tmp_path):
    issue = {"code": "FONT_APPROXIMATION", "impact": "approximation", "message": "x" * 5000 + "\x00"}
    doc = Document(tmp_path, 205, lambda p: svg(issues=[issue]), size=(2.54, 2.54))
    result = engine.export_pdf(doc, tmp_path / "out.pdf", dpi=72)
    assert len(result["warnings"]) == 200
    assert result["warning_count"] == 205
    assert result["warning_pages"] == list(range(1, 206))
    assert max(len(w["message"]) for w in result["warnings"]) <= 512
    assert all("\x00" not in warning["message"] for warning in result["warnings"])


@pytest.mark.parametrize("content", [
    '<svg><script>alert(1)</script></svg>',
    '<svg><image href="file:///secret.png"/></svg>',
    '<svg><image href="data:image/png;base64,bm90IGEgcG5n"/></svg>',
])
def test_unsafe_svg_and_invalid_embedded_images_are_fatal(engine, tmp_path, content):
    doc = Document(tmp_path, content=lambda p: content)
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, tmp_path / "bad.pdf", allow_incomplete=True)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    no_parts(tmp_path)


@pytest.mark.parametrize("failure", ["render", "decode", "wrong_size", "page_svg"])
def test_page_failures_preserve_existing_output(engine, tmp_path, monkeypatch, failure):
    doc = Document(tmp_path, 2)
    output = tmp_path / "out.pdf"
    output.write_bytes(b"KEEP")
    def fail(**kwargs):
        if failure == "render":
            raise RuntimeError("private customer text" * 5000)
        if failure == "decode":
            return b"bad png"
        buffer = io.BytesIO()
        with Image.new("RGB", (1, 1)) as image:
            image.save(buffer, "PNG")
        return buffer.getvalue()
    if failure == "page_svg":
        def broken(p):
            raise RuntimeError("private customer text" * 5000)
        doc.content = broken
    else:
        monkeypatch.setattr(engine.resvg_py, "svg_to_bytes", fail)
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, output, overwrite=True, allow_incomplete=True)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert "private customer" not in str(error.value)
    assert len(json.dumps(error.value.details)) < 100000
    assert output.read_bytes() == b"KEEP"
    no_parts(tmp_path)


def test_final_extension_and_all_sources_protected(engine, tmp_path):
    doc = Document(tmp_path)
    protected = tmp_path / "resource.pdf"
    protected.write_bytes(b"RESOURCE")
    doc.source_signatures[protected] = source_signature(protected)
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, protected.with_suffix(".other"), overwrite=True)
    assert protected.read_bytes() == b"RESOURCE"
    alias = tmp_path / "alias.pdf"
    os.link(doc.path, alias)
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, alias, overwrite=True)
    assert doc.path.read_bytes() == b"original AFP evidence"


def test_existing_output_and_publish_race_need_explicit_overwrite(engine, tmp_path):
    doc = Document(tmp_path)
    output = tmp_path / "out.pdf"
    output.write_bytes(b"KEEP")
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, output)
    def race(destination, action):
        assert destination == output
        output.write_bytes(b"RACE")
        action()
    output.unlink()
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, output, publisher=race)
    assert output.read_bytes() == b"RACE"
    engine.export_pdf(doc, output, overwrite=True)
    assert len(PdfReader(output).pages) == 1
    no_parts(tmp_path)


@pytest.mark.parametrize("when", ["before", "after_page", "publisher"])
@pytest.mark.parametrize("cause", ["cancel", "source"])
def test_cancel_or_changed_source_cannot_commit(engine, tmp_path, when, cause):
    doc = Document(tmp_path, 3)
    output = tmp_path / "out.pdf"
    output.write_bytes(b"KEEP")
    cancelled = False
    class Cancelled(RuntimeError):
        pass
    def trigger():
        nonlocal cancelled
        if cause == "cancel":
            cancelled = True
        else:
            doc.path.write_bytes(b"source changed")
    def check():
        if cancelled:
            raise Cancelled()
    def progress(ratio):
        if when == "after_page":
            trigger()
    def publisher(destination, action):
        if when == "publisher":
            trigger()
        action()
    if when == "before":
        trigger()
    with pytest.raises(Cancelled if cause == "cancel" else SourceChangedError):
        engine.export_pdf(doc, output, overwrite=True, check_cancelled=check,
                          progress=progress, publisher=publisher)
    assert output.read_bytes() == b"KEEP"
    if when == "after_page":
        assert doc.rendered == [0]
    no_parts(tmp_path)


def test_pages_are_flushed_and_images_closed_before_next_render(engine, tmp_path, monkeypatch):
    doc = Document(tmp_path, 3)
    original = Image.Image.convert
    previous = []
    def convert(image, *args, **kwargs):
        converted = original(image, *args, **kwargs)
        previous.append(converted)
        return converted
    monkeypatch.setattr(Image.Image, "convert", convert)
    def content(p):
        if p:
            parts = list(tmp_path.glob("*.part"))
            assert len(parts) == 1
            assert b"/Subtype /Image" in parts[0].read_bytes()
            for image in previous:
                with pytest.raises(ValueError):
                    image.getpixel((0, 0))
        return svg("blue")
    doc.content = content
    engine.export_pdf(doc, tmp_path / "out.pdf")
    for image in previous:
        with pytest.raises(ValueError):
            image.getpixel((0, 0))


def test_no_aggregate_pixel_limit(engine, tmp_path):
    doc = Document(tmp_path, 21, size=(254, 254))
    result = engine.export_pdf(doc, tmp_path / "out.pdf", dpi=150)
    assert result["page_count"] == 21  # 47.25 million aggregate pixels.
    assert len(PdfReader(result["path"]).pages) == 21


def test_failed_source_image_decode_is_not_approvable(engine, tmp_path):
    issue = {"code": "IMAGE_NOT_RENDERED", "impact": "omission", "message": "Image decode failed"}
    doc = Document(tmp_path, content=lambda p: svg(issues=[issue]))
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, tmp_path / "bad.pdf", allow_incomplete=True)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert error.value.details["page"] == 1
    assert not (tmp_path / "bad.pdf").exists()
    no_parts(tmp_path)


def test_exact_side_and_pixel_limits(engine, tmp_path):
    # 5000 * 4000 = 20 million pixels; both limits are inclusive.
    doc = Document(tmp_path, size=(1270, 1016))
    result = engine.export_pdf(doc, tmp_path / "edge.pdf", dpi=100)
    image = next(iter(PdfReader(result["path"]).pages[0]["/Resources"]["/XObject"].values())).get_object()
    assert (image["/Width"], image["/Height"]) == (5000, 4000)
    doc.analysis.page_descriptors[0].height_mm = 1016.254
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, tmp_path / "over.pdf", dpi=100)
    assert not (tmp_path / "over.pdf").exists()


def test_fractional_dimensions_and_diagnostic_text_never_becomes_pdf_syntax(engine, tmp_path):
    issue = {"code": "FONT_APPROXIMATION", "impact": "approximation", "message": ") /OpenAction (customer secret"}
    doc = Document(tmp_path, content=lambda p: svg("#123456", issues=[issue]), size=(10, 7))
    result = engine.export_pdf(doc, tmp_path / "out.pdf", dpi=300)
    page = PdfReader(result["path"], strict=True).pages[0]
    assert float(page.mediabox.width) == pytest.approx(28.3464566929, abs=1e-9)
    assert float(page.mediabox.height) == pytest.approx(19.8425196850, abs=1e-9)
    assert b"customer secret" not in Path(result["path"]).read_bytes()
    with fitz.open(result["path"]) as pdf:
        assert pdf[0].get_pixmap().pixel(3, 3) == (18, 52, 86)


def test_write_failure_closes_image_and_removes_temporary(engine, tmp_path, monkeypatch):
    doc = Document(tmp_path)
    output = tmp_path / "out.pdf"
    output.write_bytes(b"KEEP")
    retained = []
    def broken(writer, image, width_points, height_points):
        retained.append(image)
        raise OSError("disk full")
    monkeypatch.setattr(engine._PdfWriter, "add_page", broken)
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, output, overwrite=True)
    assert output.read_bytes() == b"KEEP"
    with pytest.raises(ValueError):
        retained[0].getpixel((0, 0))
    no_parts(tmp_path)


def test_legacy_dispatch_can_import_engine_before_validator(engine, tmp_path):
    # A fresh interpreter imports the engine before conversion, as dispatch will.
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([
        str(root / "backend"), str(root / "backend/vendor/readafp/src")]))
    code = ("from afpstudio.engineering.pdf_export import export_pdf; "
            "from afpstudio.engineering.conversion import validate_svg; "
            "assert callable(export_pdf) and callable(validate_svg)")
    subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True)


def test_safe_bounded_error_details(engine):
    error = engine.PdfExportError("bad\x00" * 5000, code="invalid!" * 100,
                                  details={"page": 1, "svg": "customer" * 10000,
                                           "issues": [{"message": "secret\x00" * 5000}] * 1000})
    assert len(str(error)) <= 512 and "\x00" not in str(error)
    assert "svg" not in error.details
    assert len(error.details["issues"]) <= 128
    assert len(json.dumps(error.details)) < 100000
    assert len(error.code) <= 64


@pytest.mark.parametrize("option,value", [("overwrite", "false"), ("overwrite", 1),
                                          ("allow_incomplete", "false"), ("allow_incomplete", 1)])
def test_confirmation_flags_require_actual_booleans(engine, tmp_path, option, value):
    issue = {"code": "MISSING_RESOURCE", "impact": "omission"}
    doc = Document(tmp_path, content=lambda p: svg(issues=[issue]))
    output = tmp_path / "out.pdf"
    output.write_bytes(b"KEEP")
    kwargs = {"overwrite": True, "allow_incomplete": True, option: value}
    with pytest.raises(engine.PdfExportError):
        engine.export_pdf(doc, output, **kwargs)
    assert output.read_bytes() == b"KEEP"
    assert not doc.rendered


@pytest.mark.parametrize("content,expected", [
    (sf.structured_field(0xD3A8FB), "UNCLOSED_OBJECT"),
    (sf.structured_field(0xD3EE9B, b"\x2b\xd3\x06\xdaX"), "PTOCA_MALFORMED"),
    (sf.structured_field(0xD3A8FB)
     + sf.structured_field(0xD3EEFB, b"\x94\x09\x00\x02\xd0\x02\xd0\x00\x08\x00\x02"
                                      b"\xfe\x92\x00\x05\x80\x01")
     + sf.structured_field(0xD3A9FB), "IMAGE_DATA_PARTIAL"),
], ids=["unclosed-object", "malformed-ptoca", "truncated-image"])
@pytest.mark.parametrize("existing", [False, True])
def test_real_invalid_render_data_cannot_be_approved(engine, tmp_path, content, expected, existing):
    from xml.etree.ElementTree import fromstring
    source = tmp_path / "invalid.afp"
    original = sf.bdt() + sf.bpg() + sf.pgd(25.4, 12.7) + content + sf.epg() + sf.edt()
    source.write_bytes(original)
    doc = DocumentStore().open(source)
    assert doc.parse_complete  # Structural completion does not prove valid render data.
    issues = json.loads(fromstring(doc.page_svg(0)).attrib["data-render-issues"])
    assert any(issue["code"] == expected and issue["category"] == "invalid_data" for issue in issues)
    output = tmp_path / "out.pdf"
    if existing:
        output.write_bytes(b"KEEP")
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, output, allow_incomplete=True, overwrite=existing)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert error.value.code == "invalid_render_data"
    assert error.value.details["page"] == 1
    assert expected in {issue["code"] for issue in error.value.details["issues"]}
    assert output.read_bytes() == b"KEEP" if existing else not output.exists()
    assert source.read_bytes() == original
    no_parts(tmp_path)


@pytest.mark.parametrize("code,category", [
    ("FUTURE_INVALID_DATA", "invalid_data"),
    ("UNCLOSED_OBJECT", "unsupported"),
    ("UNCLOSED_RESOURCE", "unsupported"),
    ("PTOCA_MALFORMED", "unsupported"),
    ("IMAGE_DATA_PARTIAL", "unsupported"),
    ("MALFORMED_DESCRIPTOR", "unsupported"),
    ("MALFORMED_INCLUDE", "unsupported"),
    ("RENDER_LIMIT", "unsupported"),
])
def test_invalid_or_truncated_diagnostic_codes_cannot_be_approved(engine, tmp_path, code, category):
    issue = {"code": code, "category": category, "impact": "omission"}
    doc = Document(tmp_path, content=lambda p: svg(issues=[issue]))
    with pytest.raises(engine.PdfExportError) as error:
        engine.export_pdf(doc, tmp_path / "bad.pdf", allow_incomplete=True)
    assert not isinstance(error.value, engine.PdfNeedsApproval)
    assert error.value.code == "invalid_render_data"
    assert error.value.details == {"page": 1, "issues": [issue]}
    assert not (tmp_path / "bad.pdf").exists()
    no_parts(tmp_path)


@pytest.mark.parametrize("tail,corrupt", [(b"\xc1\x08\x00", True), (b"\x0c\x00", False)],
                         ids=["truncated-order", "unsupported-order"])
def test_real_goca_corruption_is_fatal_but_unsupported_orders_can_be_approved(engine, tmp_path, tail, corrupt):
    import struct
    from xml.etree.ElementTree import fromstring
    gdd = b"\xf6\x12" + b"\x00" * 4 + struct.pack(">HHHhhhh", 1000, 1000, 0, 0, 1000, 0, 800)
    orders = b"\xc1\x08" + struct.pack(">hhhh", 0, 0, 100, 100) + tail
    gad = b"\x70\x0cGOCA\x00\x00" + len(orders).to_bytes(2, "big") + b"GOCA" + orders
    content = (sf.structured_field(0xD3A8BB) + sf.structured_field(0xD3A6BB, gdd)
               + sf.structured_field(0xD3EEBB, gad) + sf.structured_field(0xD3A9BB))
    source = tmp_path / "graphic.afp"
    source.write_bytes(sf.bdt() + sf.bpg() + sf.pgd(25.4, 12.7) + content + sf.epg() + sf.edt())
    doc = DocumentStore().open(source)
    issues = json.loads(fromstring(doc.page_svg(0)).attrib["data-render-issues"])
    assert any(issue["code"] == "GOCA_PARTIAL" and issue["category"] == "unsupported" for issue in issues)
    output = tmp_path / "graphic.pdf"
    if corrupt:
        assert any("truncated" in issue["message"] for issue in issues)
        with pytest.raises(engine.PdfExportError) as error:
            engine.export_pdf(doc, output, allow_incomplete=True)
        assert error.value.code == "invalid_render_data"
        assert error.value.details["page"] == 1
        assert error.value.details["issues"]
        assert not output.exists()
    else:
        with pytest.raises(engine.PdfNeedsApproval):
            engine.export_pdf(doc, output)
        result = engine.export_pdf(doc, output, allow_incomplete=True)
        assert result["status"] == "completed_with_warnings"
        assert len(PdfReader(output).pages) == 1
    no_parts(tmp_path)


def test_unsupported_graphic_not_rendered_still_allows_approval(engine, tmp_path):
    issue = {"code": "GRAPHIC_NOT_RENDERED", "category": "unsupported", "impact": "omission"}
    doc = Document(tmp_path, content=lambda p: svg(issues=[issue]))
    result = engine.export_pdf(doc, tmp_path / "unsupported.pdf", allow_incomplete=True)
    assert result["status"] == "completed_with_warnings"
