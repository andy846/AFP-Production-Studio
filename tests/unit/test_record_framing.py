import io

import pytest

from afpstudio.document_model.records import RecordError, iter_records


def test_standard_records_preserve_bytes_and_offsets():
    field = b"\x5a\x00\x08\xd3\xa8\xaf\x00\x00\x00"
    records = list(iter_records(io.BytesIO(field * 2), "standard"))
    assert [record.offset for record in records] == [0, 9]
    assert b"".join(record.payload for record in records) == field * 2


def test_rdw_records_preserve_payload_and_physical_offsets():
    payload = b"\x00\x08\xd3\xa8\xaf\x00\x00\x00"
    record = b"\x00\x0c\x00\x00" + payload
    records = list(iter_records(io.BytesIO(record * 2), "rdw"))
    assert [(item.offset, item.payload_offset) for item in records] == [(0, 4), (12, 16)]
    assert [item.payload for item in records] == [payload, payload]


@pytest.mark.parametrize("data, framing, code", [
    (b"\x5a\x00\x07", "standard", "invalid_length"),
    (b"\x5a\x00\x08\xd3", "standard", "truncated_record"),
    (b"\x00\x03\x00\x00", "rdw", "invalid_rdw_length"),
    (b"\x00\x04\x01\x00", "rdw", "unsupported_spanned_record"),
    (b"\x00\x09\x00\x00\x01", "rdw", "truncated_record"),
    (b"\x41", "standard", "unsupported_record"),
])
def test_bad_framing_fails_closed(data, framing, code):
    with pytest.raises(RecordError) as error:
        list(iter_records(io.BytesIO(data), framing))
    assert error.value.code == code
    assert error.value.offset == 0
