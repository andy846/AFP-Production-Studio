"""The media preflight engine."""

from __future__ import annotations

import pytest

from afpstudio.preflight.engine import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNRESOLVED,
    STATUS_WARNING,
    run_preflight,
    worst,
)
from afpstudio.preflight.media import analyse_media, load_form_definitions
from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index


def codes(result):
    """Every finding code in a preflight result."""
    found = {finding.code for finding in result.findings}
    for page in result.pages:
        found.update(finding.code for finding in page.findings)
    return found


def test_status_ranking():
    assert worst(["pass", "warning"]) == STATUS_WARNING
    assert worst(["warning", "unresolved"]) == STATUS_UNRESOLVED
    assert worst(["unresolved", "fail"]) == STATUS_FAIL
    assert worst([]) == STATUS_PASS


def test_ambiguous_medium_map_is_unresolved(indexed, permissive_profile):
    path, index, analysis = indexed("ambiguous.afp", synthetic.simplex_document(2))
    name = analysis.invokes[0].name
    analysis.ambiguous_maps.add(name)
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert all(page.status == STATUS_UNRESOLVED for page in result.pages)
    assert "AMBIGUOUS_MEDIUM_MAP" in codes(result)


def test_simplex_document_resolves(indexed, permissive_profile):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(3)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert result.status == STATUS_PASS
    assert [page.medium_map for page in result.pages] == ["MAP01"] * 3
    assert result.pages[0].requested.duplex == "simplex"
    assert result.pages[0].resolved.stock_name == "A4 White 80gsm"
    assert result.pages[0].resolved.tray == "Tray 1"
    assert result.pages[0].resolved.verified is True


def test_document_level_invoke_applies_to_every_page(
    indexed, permissive_profile
):
    path, index, analysis = indexed(
        "duplex.afp", synthetic.duplex_document(2)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert [page.medium_map for page in result.pages] == ["MAPDUP"] * 2
    assert result.pages[0].medium_map_source == "document_imm"
    assert result.pages[0].requested.duplex == "duplex-long-edge"


def test_medium_map_transition_is_tracked(indexed, permissive_profile):
    path, index, analysis = indexed(
        "transition.afp", synthetic.transition_document(4)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert [page.medium_map for page in result.pages] == [
        "MAP01",
        "MAP01",
        "MAP02",
        "MAP02",
    ]
    assert len(result.transitions) == 2
    assert result.transitions[1].page_number == 3
    assert result.transitions[1].from_map == "MAP01"
    assert result.transitions[1].to_map == "MAP02"


def test_missing_form_definition_is_unresolved_not_pass(
    indexed, permissive_profile
):
    path, index, analysis = indexed(
        "noformdef.afp", synthetic.no_form_def_document(2)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert result.status != STATUS_PASS
    assert "MEDIUM_MAP_UNDECLARED" in codes(result)
    assert result.pages[0].status == STATUS_UNRESOLVED


def test_external_form_definition_resolves_the_medium_map(
    indexed, permissive_profile, tmp_path
):
    form_def = tmp_path / "formdef.afp"
    form_def.write_bytes(
        synthetic.DocumentSpec(
            medium_maps=[
                synthetic.MediumMapSpec(
                    name="MAP01",
                    media_type="WHITE80",
                    media_destination="TRAY1",
                )
            ],
            page_invokes=[],
        ).build()
    )
    document = tmp_path / "job.afp"
    document.write_bytes(synthetic.no_form_def_document(2))

    index = build_index(document)
    analysis = analyse_media(index, document)
    external = load_form_definitions(form_def)
    result = run_preflight(
        document,
        index,
        analysis,
        profile=permissive_profile,
        external_definitions=external,
    )
    assert result.pages[0].requested.definition_source == "external_form_def"
    assert result.pages[0].resolved.stock_name == "A4 White 80gsm"
    assert "MEDIUM_MAP_UNDECLARED" not in codes(result)


def test_unknown_medium_map_is_unresolved(indexed, permissive_profile):
    data = synthetic.DocumentSpec(
        medium_maps=[
            synthetic.MediumMapSpec(
                name="MAP99",
                media_type="UNKNOWN",
                media_destination="TRAY9",
            )
        ],
        page_invokes=["MAP99", "MAP99"],
    ).build()
    path, index, analysis = indexed(
        "unknown-map.afp", data
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert "MEDIA_MAP_UNMAPPED" in codes(result)
    assert result.pages[0].medium_map == "MAP99"
    assert result.pages[0].resolved.stock_name is None
    assert result.pages[0].status == STATUS_UNRESOLVED


def test_no_printer_profile_means_unresolved(indexed):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    result = run_preflight(path, index, analysis, profile=None)
    assert result.status == STATUS_UNRESOLVED
    assert "MISSING_PRINTER_PROFILE" in codes(result)


def test_truncated_document_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "truncated.afp", synthetic.truncated_document()
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert result.status == STATUS_FAIL
    assert "INCOMPLETE_PARSE" in codes(result)
    assert result.parse_complete is False


def test_invalid_structured_field_length_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "badlength.afp", synthetic.invalid_length_document()
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert result.status == STATUS_FAIL
    assert "INCOMPLETE_PARSE" in codes(result)


def test_malformed_invoke_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "bad.afp", synthetic.malformed_imm_document()
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert "INVALID_IMM" in codes(result)
    assert result.status == STATUS_FAIL


def test_conflicting_duplex_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "conflict.afp", synthetic.conflicting_duplex_document()
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert "DUPLEX_INCONSISTENT" in codes(result)
    assert result.status == STATUS_FAIL


def test_paper_size_mismatch_fails(indexed):
    from afpstudio.printer_profiles.schema import validate_profile

    profile = validate_profile(
        {
            "schema_version": "1.0",
            "printer_profile": "Letter printer",
            "media_maps": {
                "MAP01": {
                    "stock_name": "Letter",
                    "tray": "Tray 1",
                    "paper_size": {"width_mm": 215.9, "height_mm": 279.4},
                }
            },
            "rules": [],
        }
    )
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    assert "PAPER_SIZE_MISMATCH" in codes(result)
    assert result.status == STATUS_FAIL


def test_missing_resource_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    result = run_preflight(
        path,
        index,
        analysis,
        profile=permissive_profile,
        missing_resources=[
            {"kind": "coded font", "name": "MISSING01", "codec": None}
        ],
    )
    assert "MISSING_RESOURCE" in codes(result)
    assert result.status == STATUS_FAIL


def test_parser_disagreement_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    result = run_preflight(
        path,
        index,
        analysis,
        profile=permissive_profile,
        parser_disagreements=[
            {
                "kind": "page_count",
                "message": "The parsers counted different page numbers.",
            }
        ],
    )
    assert "PARSER_DISAGREEMENT" in codes(result)
    assert result.status == STATUS_FAIL


def test_parser_failure_fails(indexed, permissive_profile):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    result = run_preflight(
        path,
        index,
        analysis,
        profile=permissive_profile,
        parser_failures=[{"code": "timeout", "message": "it took too long"}],
    )
    assert "PARSER_FAILURE" in codes(result)
    assert result.status == STATUS_FAIL


def test_preflight_records_the_offset_of_every_page(
    indexed, permissive_profile
):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(3)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    for page in result.pages:
        assert page.offset == index.page_starts[page.page_index]


def test_preflight_never_labels_resolved_media_as_actual(
    indexed, permissive_profile
):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    payload = result.as_dict()
    assert "actual" not in payload
    assert payload["pages"][0]["resolved"]["source"] == "printer_profile"


def test_unexpected_media_change_is_a_warning(indexed, permissive_profile):
    path, index, analysis = indexed(
        "transition.afp", synthetic.transition_document(4)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert "UNEXPECTED_MEDIA_CHANGE" in codes(result)
    assert result.pages[2].status in (STATUS_WARNING, STATUS_UNRESOLVED)
    finding = next(
        f
        for f in result.findings
        if f.code == "UNEXPECTED_MEDIA_CHANGE"
    )
    assert finding.status == STATUS_WARNING
    assert finding.page == 3


def test_document_boundary_does_not_reassign_previous_page_or_carry_imm(indexed):
    second = synthetic.bdt() + synthetic.bpg() + synthetic.epg() + synthetic.edt()
    path, index, analysis = indexed("two-documents.afp", synthetic.simplex_document(2) + second)
    result = run_preflight(path, index, analysis)
    assert [p.document_id for p in result.pages] == ["D1", "D1", "D2"]
    assert [p.medium_map for p in result.pages] == ["MAP01", "MAP01", None]


def test_clean_field_boundary_without_end_document_is_not_complete(indexed, permissive_profile):
    raw = synthetic.simplex_document(1)[:-len(synthetic.edt())]
    path, index, analysis = indexed("missing-edt.afp", raw)
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert not result.parse_complete
    assert result.status == STATUS_FAIL
    assert any(warning["code"] == "DOCUMENT_UNCLOSED" for warning in index.warnings)


@pytest.mark.parametrize("pages", [1, 3, 10])
def test_page_count_is_stable(indexed, permissive_profile, pages):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(pages)
    )
    result = run_preflight(path, index, analysis, profile=permissive_profile)
    assert len(result.pages) == pages
    assert result.documents == 1
