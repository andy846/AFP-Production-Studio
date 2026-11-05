import io

import pytest
from PIL import Image

from afpstudio.document_model.model import DocumentStore
from afpstudio.engineering.conversion import convert_preview, validate_svg, ConversionError
from afpstudio.testing import synthetic


@pytest.mark.parametrize("fmt,magic", [("png", b"\x89PNG"), ("pdf", b"%PDF"), ("tiff", b"II")])
def test_local_preview_conversion_has_valid_output_and_preserves_source(tmp_path, fmt, magic):
    path = tmp_path / "job.afp"
    raw = synthetic.demo_job()
    path.write_bytes(raw)
    output = tmp_path / ("preview." + fmt)
    document = DocumentStore().open(path)
    result = convert_preview(document, output, fmt, [0], dpi=72)
    assert output.read_bytes().startswith(magic)
    assert result["fidelity"] == "preview_not_production_validated"
    assert path.read_bytes() == raw
    if fmt != "pdf":
        with Image.open(output) as image:
            assert image.width > 500 and image.height > 700
            assert image.getextrema() != ((255, 255),) * 3


@pytest.mark.parametrize("svg", [
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///secret.png"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/a.png"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><style>@import "https://example.com";</style></svg>',
    '<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///secret">]><svg>&e;</svg>',
])
def test_svg_cannot_read_files_or_contact_remote_resources(svg):
    with pytest.raises(ConversionError):
        validate_svg(svg)


def test_invalid_range_or_existing_output_is_not_published(tmp_path):
    path = tmp_path / "job.afp"
    path.write_bytes(synthetic.simplex_document(2))
    document = DocumentStore().open(path)
    output = tmp_path / "preview.png"
    with pytest.raises(ConversionError):
        convert_preview(document, output, "png", [200])
    assert not output.exists()
    output.write_bytes(b"KEEP")
    with pytest.raises(ConversionError):
        convert_preview(document, output, "png", [0])
    assert output.read_bytes() == b"KEEP"
