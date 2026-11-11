"""Tests for IOCA image segment decoding and page placement."""

import struct
import zlib
import io
from pathlib import Path

import pytest

from readafp.ioca import (
    IocaImage,
    cmyk_jpeg_bands,
    image_blob,
    iter_sdfs,
    parse_image_segment,
)


def _group4_fixture():
    from PIL import Image
    image = Image.new("1", (37, 23), 255)
    for y in range(23):
        for x in range(37):
            if x == 2 or y == 3 or (x + 2 * y) % 11 == 0:
                image.putpixel((x, y), 0)
    buffer = io.BytesIO()
    image.save(buffer, format="TIFF", compression="group4", tiffinfo={262: 0})
    data = buffer.getvalue()
    with Image.open(io.BytesIO(data)) as encoded:
        offsets, counts = encoded.tag_v2[273], encoded.tag_v2[279]
        assert len(offsets) == 1
        raw = data[offsets[0]:offsets[0] + counts[0]]
    return image, raw


def test_group4_ioca_preserves_every_pixel_and_white_black_polarity():
    from PIL import Image
    expected, raw = _group4_fixture()
    img = parse_image_segment(_segment(37, 23, 1, raw, compression=0x82))
    result = image_blob(img)
    assert result is not None
    mime, png = result
    assert mime == "image/png"
    with Image.open(io.BytesIO(png)) as actual:
        assert actual.size == expected.size
        assert actual.convert("1").tobytes() == expected.tobytes()


def test_group4_ioca_enforces_dimensions_and_element_depth():
    _, raw = _group4_fixture()
    for width, height, bits in ((0, 23, 1), (37, 0, 1), (65535, 65535, 1), (37, 23, 8)):
        assert image_blob(IocaImage(width=width, height=height, bits=bits, compression=0x82, data=raw)) is None
    assert image_blob(IocaImage(width=37, height=23, compression=0x82, data=b"")) is None
    assert image_blob(IocaImage(width=37, height=23, compression=0x82, data=raw, recording=3)) is None


@pytest.mark.parametrize(("compression", "tiff_codec", "mode"), [
    (0x80, "group3", "1"), (0x81, "group3", "1"), (0x09, "tiff_ccitt", "1"),
    (0x0C, "packbits", "1"), (0x0C, "packbits", "L"), (0x0C, "packbits", "RGB"),
    (0x0D, "tiff_lzw", "1"), (0x0D, "tiff_lzw", "L"), (0x0D, "tiff_lzw", "RGB"),
])
def test_additional_ioca_tiff_codecs_match_pixels(compression, tiff_codec, mode):
    from PIL import Image
    image = Image.new(mode, (37, 23), 255 if mode != "RGB" else (255, 240, 80))
    for xy in ((0, 0), (12, 7), (36, 22)):
        image.putpixel(xy, 0 if mode != "RGB" else (10, 70, 90))
    buffer = io.BytesIO()
    info = {262: 0} if mode == "1" else {}
    if compression == 0x81:
        info[292] = 1
    image.save(buffer, format="TIFF", compression=tiff_codec, tiffinfo=info)
    with Image.open(io.BytesIO(buffer.getvalue())) as encoded:
        start, count = encoded.tag_v2[273][0], encoded.tag_v2[279][0]
        assert len(encoded.tag_v2[273]) == 1
    bits = {"1": 1, "L": 8, "RGB": 24}[mode]
    result = image_blob(IocaImage(width=37, height=23, bits=bits, compression=compression,
                                 data=buffer.getvalue()[start:start + count]))
    assert result is not None
    with Image.open(io.BytesIO(result[1])) as actual:
        assert list(actual.getdata()) == list(image.getdata())


def test_uncompressed_bottom_up_rows_and_bit_order_are_honored():
    img = IocaImage(width=8, height=2, data=b"\x80\x01", recording=3, bit_order=1)
    result = image_blob(img)
    assert result is not None
    assert _png_pixels(result[1]) == b"\x00\x7f\x00\xfe"


def test_image_geometry_limits_apply_before_decoding():
    assert image_blob(IocaImage(width=-1, height=1, data=b"\xff")) is None
    assert image_blob(IocaImage(width=65535, height=65535, bits=24, data=b"\xff")) is None


@pytest.mark.parametrize("black_first_row", [False, True])
def test_ibm_mmr_first_1d_line_followed_by_2d_lines(black_first_row):
    from PIL import Image
    image = Image.new("1", (8, 4), 255)
    if black_first_row:
        for x in range(8):
            image.putpixel((x, 0), 0)
    image.putpixel((2, 1), 0)
    image.putpixel((5, 2), 0)
    buffer = io.BytesIO()
    image.save(buffer, format="TIFF", compression="group4", tiffinfo={262: 0})
    with Image.open(io.BytesIO(buffer.getvalue())) as tiff:
        start, count = tiff.tag_v2[273][0], tiff.tag_v2[279][0]
    bits = "".join(f"{b:08b}" for b in buffer.getvalue()[start:start + count])
    first_bits = "00100110101000101" if black_first_row else "1"
    assert bits.startswith(first_bits)
    # T.4 white run of 8 pels = 10011. IBM MMR starts EOL+1 (1D),
    # switches with EOL+0, then carries the remaining 2D-coded rows.
    first_1d = "00110101000101" if black_first_row else "10011"
    mmr = "0000000000011" + first_1d + "0000000000010" + bits[len(first_bits):]
    data = int(mmr.ljust((len(mmr) + 7) // 8 * 8, "0"), 2).to_bytes((len(mmr) + 7) // 8, "big")
    result = image_blob(IocaImage(width=8, height=4, compression=1, data=data))
    assert result is not None
    with Image.open(io.BytesIO(result[1])) as actual:
        assert actual.tobytes() == image.tobytes()


def test_blank_named_segment_uses_resource_wrapper_and_ips_offsets():
    from readafp.parser import iter_fields
    def sf(identifier, payload=b""):
        return b"\x5a" + struct.pack(">H", 8 + len(payload)) + identifier.to_bytes(3, "big") + bytes(3) + payload
    name = "SEGMENT".ljust(8).encode("cp500")
    raw = (sf(0xD3A8A8) + sf(0xD3A8CE, name) + sf(0xD3A85F, b"\x40" * 8)
           + sf(0xD3A8FB) + sf(0xD3EEFB, _segment(8, 2, 1, b"\x80\x01"))
           + sf(0xD3A9FB) + sf(0xD3A95F) + sf(0xD3A9CE) + sf(0xD3A8AF)
           + sf(0xD3AF5F, name + (123).to_bytes(3, "big", signed=True) + (-45).to_bytes(3, "big", signed=True))
           + sf(0xD3A9AF) + sf(0xD3A9A8))
    page, = extract_pages(list(iter_fields(raw)))
    assert len(page.images) == 1
    assert (page.images[0].x, page.images[0].y) == (123, -45)



def test_group4_image_is_placed_on_the_page_and_emitted_in_svg():
    from readafp.parser import iter_fields

    def sf(identifier, payload=b""):
        return b"\x5a" + struct.pack(">H", 8 + len(payload)) + identifier.to_bytes(3, "big") + bytes(3) + payload

    _, raw = _group4_fixture()
    afp = (sf(0xD3A8A8) + sf(0xD3A8AF) + sf(0xD3A8FB)
           + sf(0xD3EEFB, _segment(37, 23, 1, raw, compression=0x82))
           + sf(0xD3A9FB) + sf(0xD3A9AF) + sf(0xD3A9A8))
    page, = extract_pages(list(iter_fields(afp)))
    assert len(page.images) == 1
    assert page.images[0].width == 37 * page.units_per_inch // 72
    assert page.images[0].height == 23 * page.units_per_inch // 72
    assert "data:image/png;base64," in page_to_svg(page)
from readafp.parser import parse_file
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg

TESTDATA = Path(__file__).parent.parent / "testdata"
FOP_IMAGES = TESTDATA / "fop-pairs" / "images.afp"
BIM_SAMPLE = TESTDATA / "github-samples" / "afplib" / "bim.afp"
IPD_SPAN = TESTDATA / "github-samples" / "afplib" / "IPDSpan.afp"
RESOURCE_ONLY = TESTDATA / "github-samples" / "fop" / "resource_name_match.afp"


def _segment(
    width: int,
    height: int,
    bits: int,
    pixel_data: bytes,
    compression: int = 0x03,
) -> bytes:
    """Build a minimal IOCA image segment around the given pixel data."""
    return (
        bytes.fromhex("7000" "9101ff")
        + bytes([0x94, 9, 0])
        + struct.pack(">HHHH", 720, 720, width, height)
        + bytes([0x95, 2, compression, 0x01])
        + bytes([0x96, 1, bits])
        + b"\xfe\x92"
        + struct.pack(">H", len(pixel_data))
        + pixel_data
        + bytes.fromhex("9300" "7100")
    )


def _png_size(blob: bytes) -> tuple:
    assert blob.startswith(b"\x89PNG")
    return struct.unpack(">II", blob[16:24])


def _png_pixels(blob: bytes) -> bytes:
    """Inflate the IDAT raster (single chunk, as our encoder writes it)."""
    pos = 8
    raster = b""
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos : pos + 4])
        tag = blob[pos + 4 : pos + 8]
        if tag == b"IDAT":
            raster += blob[pos + 8 : pos + 8 + length]
        pos += 12 + length
    return zlib.decompress(raster)


def test_iter_sdfs_long_and_extended() -> None:
    data = bytes.fromhex("9101ff") + b"\xfe\x92\x00\x03abc" + b"\x93\x00"
    sdfs = list(iter_sdfs(data))
    assert sdfs == [(0x91, b"\xff"), (0xFE92, b"abc"), (0x93, b"")]


def test_iter_sdfs_zero_length_image_data_takes_rest() -> None:
    # afplib's IPDSpan fixture writes FE92 0000 and carries the pixel
    # bytes in the next IPD field with no further framing.
    data = b"\x96\x01\x01" + b"\xfe\x92\x00\x00" + b"\x01\x02\x03\x04"
    sdfs = dict(iter_sdfs(data))
    assert sdfs[0xFE92] == b"\x01\x02\x03\x04"


def test_parse_image_segment_parameters() -> None:
    img = parse_image_segment(_segment(4, 2, 8, bytes(8)))
    assert (img.width, img.height) == (4, 2)
    assert (img.hres, img.vres) == (720, 720)
    assert img.compression == 0x03
    assert img.bits == 8
    assert img.data == bytes(8)


def test_parse_image_segment_concatenates_data_fields() -> None:
    seg = (
        b"\x95\x02\x03\x01"
        + b"\xfe\x92\x00\x02ab"
        + b"\xfe\x92\x00\x02cd"
    )
    assert parse_image_segment(seg).data == b"abcd"


def test_parse_image_segment_bands_strip_headers() -> None:
    # Band Image Data: BANDNUM(1) reserved(2) then data; continuation
    # SDFs for the same band repeat the header.
    seg = (
        b"\x95\x02\x83\x01"
        + b"\xfe\x9c\x00\x05\x01\x00\x00ab"
        + b"\xfe\x9c\x00\x05\x01\x00\x00cd"
        + b"\xfe\x9c\x00\x05\x02\x00\x00ef"
    )
    img = parse_image_segment(seg)
    assert img.bands == {1: b"abcd", 2: b"ef"}


def test_image_blob_bilevel_inverts_to_png() -> None:
    # 8x2: all-ones rows (IOCA 1 = black mark) -> PNG gray 0 = black.
    img = parse_image_segment(_segment(8, 2, 1, b"\xff\xff"))
    mime, blob = image_blob(img)
    assert mime == "image/png"
    assert _png_size(blob) == (8, 2)
    # Filter byte 0 then one raster byte per row; black = 0x00.
    assert _png_pixels(blob) == b"\x00\x00\x00\x00"


def test_image_blob_grayscale_png() -> None:
    img = parse_image_segment(_segment(2, 2, 8, bytes([0, 85, 170, 255])))
    mime, blob = image_blob(img)
    assert mime == "image/png"
    assert _png_size(blob) == (2, 2)
    assert _png_pixels(blob) == b"\x00\x00\x55\x00\xaa\xff"


def test_image_blob_jpeg_passthrough() -> None:
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    img = parse_image_segment(_segment(2, 2, 24, jpeg, compression=0x83))
    assert image_blob(img) == ("image/jpeg", jpeg)


def test_image_blob_rejects_unknown_compression() -> None:
    img = parse_image_segment(_segment(8, 2, 1, b"\xff\xff", compression=0xFE))
    assert image_blob(img) is None


def test_image_blob_rejects_short_data() -> None:
    img = parse_image_segment(_segment(100, 100, 8, bytes(10)))
    assert image_blob(img) is None


def test_cmyk_jpeg_bands_requires_four_jpeg_planes() -> None:
    img = parse_image_segment(
        b"\x95\x02\x83\x01"
        + b"".join(
            b"\xfe\x9c\x00\x07" + bytes([n, 0, 0]) + b"\xff\xd8jp"
            for n in (1, 2, 3, 4)
        )
    )
    bands = cmyk_jpeg_bands(img)
    assert bands == [b"\xff\xd8jp"] * 4
    img.bands.pop(4)
    assert cmyk_jpeg_bands(img) is None


def test_fop_pairs_images_placed_on_pages() -> None:
    if not FOP_IMAGES.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(FOP_IMAGES)))
    assert len(pages) == 5
    assert len(pages[0].images) == 5
    first = pages[0].images[0]
    # IOB places RES00001 at (706, 460); its 737x320 object area is
    # declared in the same 240 upi units as the page.
    assert (first.x, first.y) == (706, 460)
    assert (first.width, first.height) == (737, 320)
    assert first.mime == "image/png"
    assert _png_size(first.data) == (221, 96)


def test_ipdspan_standalone_object_becomes_page() -> None:
    if not IPD_SPAN.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(IPD_SPAN)))
    assert len(pages) == 1
    assert (pages[0].width, pages[0].height) == (1344, 30)
    assert pages[0].images[0].mime == "image/png"
    assert _png_size(pages[0].images[0].data) == (1344, 30)


def test_resource_only_file_shows_its_image() -> None:
    if not RESOURCE_ONLY.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(RESOURCE_ONLY)))
    assert len(pages) == 1
    assert _png_size(pages[0].images[0].data) == (608, 200)


def test_bim_cmyk_planes_render_as_composite() -> None:
    if not BIM_SAMPLE.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(BIM_SAMPLE)))
    assert len(pages) == 1
    img = pages[0].images[0]
    assert img.bands is not None and len(img.bands) == 4
    assert all(b.startswith(b"\xff\xd8") for b in img.bands)
    svg = page_to_svg(pages[0])
    assert svg.count("<image") == 4
    assert 'filter="url(#ink-k)"' in svg
    assert "mix-blend-mode:multiply" in svg


def test_plain_image_svg_has_data_url() -> None:
    if not FOP_IMAGES.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(FOP_IMAGES)))
    svg = page_to_svg(pages[0])
    assert svg.count('href="data:image/png;base64,') == 5
    assert "ink-c" not in svg  # no CMYK filters when nothing is banded
