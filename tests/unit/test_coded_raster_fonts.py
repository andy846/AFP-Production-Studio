from readafp.parser import StructuredField
from readafp.ptoca import extract_pages
from afpstudio.viewer.index import build_index
from afpstudio.viewer.renderer import PageRenderer


def _name(value):
    return value.ljust(8).encode("cp500")


def _field(sf_id, data=b""):
    return StructuredField(offset=0, sf_id=sf_id, flags=0, sequence=0, data=data)


def _font_fields():
    fnc = bytes.fromhex("0105000000000bb80bb8") + bytes(5) + b"\x1c"
    fni = bytearray(_name("GLYPH001") + bytes(20))
    fni[8:10] = b"\x00\x06"  # inline increment in 300 dpi pels
    fni[18:20] = b"\x00\x01"  # A-space
    fni[26:28] = b"\x00\x05"  # top edge is five pels above baseline
    return [
        _field(0xD3A8CE, _name("X0TEST")),
        _field(0xD3A88A),
        _field(0xD3A78A, b"\x19\x01"),
        _field(0xD38C8A, _name("C0TEST") + _name("T1TEST") + bytes(8) + b"\x41"),
        _field(0xD3A98A),
        _field(0xD3A9CE),
        _field(0xD3A887, _name("T1TEST")),
        _field(0xD3A787, bytes(9) + b"\x0a\x40\x40"),
        _field(0xD38C87, _name("GLYPH001") + b"\x00\x42"),
        _field(0xD3A987),
        _field(0xD3A889, _name("C0TEST")),
        _field(0xD3A789, fnc),
        _field(0xD38C89, bytes(fni)),
        _field(0xD3A289, bytes.fromhex("0002000200000000")),
        _field(0xD3EE89, b"\xe0\xa0\xe0"),
        _field(0xD3A989),
    ]


def _page(data):
    null = b"\xff\xff" + bytes(6)
    return _font_fields() + [
        _field(0xD3A8AF),
        _field(0xD3A6AF, bytes.fromhex("00000bb80bb80009c4000db6")),
        _field(0xD3B18A, b"\x1c\0\0\0\x02\0\0\0" + _name("X1TEST") + null * 2),
        _field(0xD3EE9B, b"\x2b\xd3\x03\xf1\x02\x04\xc7\x00\x0a"
                        b"\x04\xd3\x00\x14" + bytes([len(data) + 2, 0xDA]) + data),
        _field(0xD3A9AF),
    ]


def test_double_byte_font_draws_embedded_shapes_at_fixed_pel_metrics():
    page, = extract_pages(_page(b"\x41\x42\x41\x42"))
    assert [(image.x, image.y, image.width, image.height)
            for image in page.images] == [(11, 15, 3, 3), (17, 15, 3, 3)]
    assert not page.texts
    assert not page.text_layer
    assert page.coded_text_incomplete


def test_unmapped_pair_and_trailing_byte_do_not_fall_back_to_single_byte_text():
    page, = extract_pages(_page(b"\x41\x42\x42\x42\x41"))
    assert len(page.images) == 1
    assert [run.text for run in page.texts] == ["□", "□"]
    assert all(run.notes for run in page.texts)
    assert not page.plain_text


def test_lazy_viewer_reports_partial_positioned_text_for_bitmap_only_font(tmp_path):
    source = tmp_path / "synthetic-cjk.afp"
    source.write_bytes(b"".join(
        b"\x5a" + (8 + len(f.data)).to_bytes(2, "big")
        + f.sf_id.to_bytes(3, "big") + b"\0\0\0" + f.data
        for f in _page(b"\x41\x42\x42\x42")
    ))
    viewer = PageRenderer(source, build_index(source))
    svg = viewer.render(0)
    assert "data:image/png;base64," in svg
    assert "unmapped or incomplete character" in svg
    text = viewer.text_runs(0, codepage="cp500")
    assert text["items"] == []
    assert text["complete"] is False
    assert text["warnings"] == 1
