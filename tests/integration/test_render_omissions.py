"""Every known skip path must identify the page's incomplete preview."""
import json
from xml.etree import ElementTree as ET

import pytest

from afpstudio.testing import synthetic as sf
from readafp.parser import iter_fields
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg


@pytest.mark.parametrize(("content", "expected"), [
    (sf.structured_field(0xD3EEFA, b"unknown"), "UNKNOWN_STRUCTURED_FIELD"),
    (sf.structured_field(0xD3AF5F, b"bad"), "MALFORMED_INCLUDE"),
    (sf.structured_field(0xD3AFD8, b"bad"), "MALFORMED_INCLUDE"),
    (sf.structured_field(0xD3EE9B, b"\x2b\xd3\x06\xdaX"), "PTOCA_MALFORMED"),
    (sf.structured_field(0xD3A8FB), "UNCLOSED_OBJECT"),
    (sf.structured_field(0xD3A8EB) + sf.structured_field(0xD3A9EB), "BARCODE_NOT_RENDERED"),
])
def test_skip_paths_produce_an_incomplete_preview(content, expected):
    raw = sf.bdt() + sf.bpg() + content + sf.epg() + sf.edt()
    page, = extract_pages(list(iter_fields(raw)))
    root = ET.fromstring(page_to_svg(page))
    issues = json.loads(root.attrib.get("data-render-issues", "[]"))
    assert expected in {item["code"] for item in issues}
    assert root.attrib["data-preview-status"] == "incomplete"
    assert all(item["category"] for item in issues)


def test_warning_limit_has_an_explicit_overflow_notice():
    content = b"".join(sf.structured_field(0xD3EEFA, b"?") for _ in range(140))
    page, = extract_pages(list(iter_fields(sf.bdt() + sf.bpg() + content + sf.epg() + sf.edt())))
    root = ET.fromstring(page_to_svg(page))
    issues = json.loads(root.attrib["data-render-issues"])
    assert len(issues) <= 128
    assert any(item["code"] == "ADDITIONAL_RENDER_ISSUES" for item in issues)


def test_nested_resource_without_end_does_not_silently_discard_parent_content():
    raw = (sf.bdt() + sf.bpg() + sf.ptx_text([("Before", 480)])
           + sf.structured_field(0xD3A85F, sf.encode_name("OPEN"))
           + sf.epg() + sf.edt())
    page, = extract_pages(list(iter_fields(raw)))
    assert "Before" in page.plain_text
    assert any(issue["code"] == "UNCLOSED_RESOURCE" for issue in page.render_issues)


def test_truncated_ioca_payload_is_reported_even_if_pixels_can_be_recovered():
    image = (b"\x94\x09\x00\x02\xd0\x02\xd0\x00\x08\x00\x02"
             b"\xfe\x92\x00\x05\x80\x01")
    raw = (sf.bdt() + sf.bpg() + sf.structured_field(0xD3A8FB)
           + sf.structured_field(0xD3EEFB, image) + sf.structured_field(0xD3A9FB)
           + sf.epg() + sf.edt())
    page, = extract_pages(list(iter_fields(raw)))
    assert page.images
    assert any(issue["code"] == "IMAGE_DATA_PARTIAL" for issue in page.render_issues)


def test_implicit_page_pagination_preserves_omission_warnings():
    raw = (sf.bdt() + sf.ptx_text([("Implicit body", 480)])
           + sf.structured_field(0xD3EE9B, b"\x2b\xd3\x06\xdaX") + sf.edt())
    page, = extract_pages(list(iter_fields(raw)))
    assert any(issue["code"] == "PTOCA_MALFORMED" for issue in page.render_issues)
