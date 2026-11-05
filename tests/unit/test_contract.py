"""The Java adapter contract."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from afpstudio.parser_adapters.alpheus import AlpheusAdapter
from afpstudio.parser_adapters.contract import (
    AdapterError,
    compare_with_local,
    validate_result,
)


def sample_payload(**overrides):
    payload = {
        "contract_version": "1.0.0",
        "adapter": {
            "name": "alpheus-adapter",
            "version": "1.0.0",
            "alpheus_commit": "abc",
        },
        "document": {
            "filename": "job.afp",
            "size_bytes": 100,
            "page_count": 2,
            "documents": 1,
            "structured_field_count": 10,
        },
        "complete": True,
        "pages": [{"page_number": 1, "offset": 10}],
        "medium_maps": [{"name": "MAP01", "offset": 20}],
        "invokes": [{"name": "MAP01", "offset": 30, "page_number": None}],
        "errors": [],
    }
    payload.update(overrides)
    return payload


def test_a_valid_payload_is_accepted():
    result = validate_result(sample_payload())
    assert result.page_count == 2
    assert result.medium_maps[0].name == "MAP01"
    assert result.complete is True


def test_an_unknown_major_version_is_refused():
    with pytest.raises(AdapterError) as excinfo:
        validate_result(sample_payload(contract_version="2.0.0"))
    assert excinfo.value.code == "contract_version_mismatch"


def test_an_unknown_minor_version_is_accepted():
    result = validate_result(sample_payload(contract_version="1.9.0"))
    assert result.contract_version == "1.9.0"


def test_unknown_fields_are_ignored():
    payload = sample_payload()
    payload["something_new"] = {"a": 1}
    payload["document"]["extra"] = "value"
    result = validate_result(payload)
    assert result.page_count == 2


def test_a_missing_version_is_refused():
    payload = sample_payload()
    del payload["contract_version"]
    with pytest.raises(AdapterError):
        validate_result(payload)


@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_missing_or_non_boolean_complete_is_refused(value):
    payload = sample_payload(complete=value)
    with pytest.raises(AdapterError):
        validate_result(payload)


def test_a_non_object_is_refused():
    with pytest.raises(AdapterError):
        validate_result([1, 2, 3])


def test_disagreeing_page_counts_are_reported():
    result = validate_result(sample_payload())
    differences = compare_with_local(result, 5, ["MAP01"])
    assert differences
    assert differences[0]["kind"] == "page_count"


def test_disagreeing_medium_maps_are_reported():
    result = validate_result(sample_payload())
    differences = compare_with_local(result, 2, ["MAP01", "MAP02"])
    assert differences
    assert differences[0]["kind"] == "medium_maps"
    assert differences[0]["only_local"] == ["MAP02"]


def test_agreement_produces_no_differences():
    result = validate_result(sample_payload())
    assert compare_with_local(result, 2, ["MAP01"]) == []


def test_detailed_comparison_reports_offsets_and_page():
    result = validate_result(sample_payload())
    index = SimpleNamespace(page_starts=[11, 20])
    analysis = SimpleNamespace(invokes=[SimpleNamespace(offset=30, name="MAP02", page=0, in_page=True)])
    differences = compare_with_local(result, 2, ["MAP01"], index=index, analysis=analysis)
    assert [(item["kind"], item["offset"], item["page"]) for item in differences] == [
        ("page_offset", 11, 1), ("invoke", 30, 1)
    ]
    assert differences[1]["local"] == "MAP02"
    assert differences[1]["adapter"] == "MAP01"


def test_missing_invocation_is_reported_at_original_offset():
    result = validate_result(sample_payload(invokes=[]))
    analysis = SimpleNamespace(invokes=[SimpleNamespace(offset=42, name="MAP01", page=None, in_page=False)])
    differences = compare_with_local(result, 2, ["MAP01"], analysis=analysis)
    assert differences[0]["kind"] == "invoke"
    assert differences[0]["offset"] == 42


def test_incomplete_adapter_cannot_produce_pass(tmp_path):
    from afpstudio.document_model.model import DocumentStore
    from afpstudio.testing.synthetic import simplex_document

    path = tmp_path / "job.afp"
    path.write_bytes(simplex_document(2))
    adapter = validate_result(sample_payload(complete=False))
    document = DocumentStore().open(path, adapter_result=adapter)
    assert document.preflight.status == "fail"
    assert document.parse_complete is False
    assert document.comparison_status == "failed"


def test_missing_adapter_is_reported_cleanly(tmp_path):
    adapter = AlpheusAdapter(jar=None)
    assert adapter.available() is False
    assert "setup_deps" in adapter.describe()["reason"]
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "x.afp")
    assert excinfo.value.code == "parser_unavailable"


def test_missing_jar_is_reported_cleanly(tmp_path):
    adapter = AlpheusAdapter(jar=tmp_path / "nope.jar")
    assert adapter.available() is False
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "x.afp")
    assert excinfo.value.code == "parser_unavailable"


def test_the_command_is_an_argument_array(tmp_path):
    adapter = AlpheusAdapter(jar=tmp_path / "adapter.jar")
    command = adapter.command(tmp_path / "a file with spaces.afp")
    assert isinstance(command, list)
    assert command[0] == "java"
    assert "--input" in command
    assert command[command.index("--input") + 1] == str(
        tmp_path / "a file with spaces.afp"
    )
    assert not any("&&" in part or ";" in part for part in command)
