"""The streaming contract fails closed on truncation and tampering."""
import hashlib
import io
import json

import pytest

from afpstudio.parser_adapters.contract import AdapterError
from afpstudio.parser_adapters.jsonl import read_jsonl


def stream(*, complete=True):
    fields = [{"type": "field", "seq": 0, "offset": 0, "length": 9,
               "sf_id": 0xD3A8A8, "page": None}]
    encoded = b"".join(json.dumps(row).encode() + b"\n" for row in fields)
    header = {"type": "header", "contract_version": "2.0.0", "input_size": 9,
              "input_sha256": "a" * 64}
    result = {"contract_version": "1.0.0", "document": {"size_bytes": 9,
              "structured_field_count": 1}, "complete": complete}
    summary = {"type": "summary", "field_count": 1,
               "fields_sha256": hashlib.sha256(encoded).hexdigest(), "result": result}
    return json.dumps(header).encode() + b"\n" + encoded + json.dumps(summary).encode() + b"\n"


def test_complete_stream_retains_original_field_offsets():
    result = read_jsonl(io.BytesIO(stream()), expected_digest="a" * 64)
    assert result.contract_version == "2.0.0"
    assert list(result.field_index) == [0, 9, 0xD3A8A8, 0]
    assert result.input_sha256 == "a" * 64


@pytest.mark.parametrize("mutate", [
    lambda b: b[:b.rfind(b'{"type": "summary"')],
    lambda b: b.replace(b'"seq": 0', b'"seq": 2'),
    lambda b: b.replace(b'"length": 9', b'"length": 0'),
    lambda b: b.replace(b'"offset": 0', b'"offset": 1.5'),
    lambda b: b + b'{}\n',
    lambda b: b.replace(b'"field_count": 1', b'"field_count": 2'),
])
def test_corrupt_stream_is_rejected(mutate):
    with pytest.raises(AdapterError):
        read_jsonl(io.BytesIO(mutate(stream())))


def test_wrong_source_digest_is_rejected():
    with pytest.raises(AdapterError):
        read_jsonl(io.BytesIO(stream()), expected_digest="b" * 64)


def test_limits_and_incomplete_are_not_success():
    with pytest.raises(AdapterError):
        read_jsonl(io.BytesIO(stream()), max_line_bytes=32)
    with pytest.raises(AdapterError):
        read_jsonl(io.BytesIO(stream()), max_fields=0)
    assert not read_jsonl(io.BytesIO(stream(complete=False))).complete
