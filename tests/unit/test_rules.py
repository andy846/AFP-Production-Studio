"""The operator's media rules."""

from __future__ import annotations

from afpstudio.preflight.engine import run_preflight
from afpstudio.printer_profiles.schema import validate_profile
from afpstudio.testing import synthetic


def profile_with(rules, maps=None):
    """Build a profile with the given rules."""
    return validate_profile(
        {
            "schema_version": "1.0",
            "printer_profile": "Rule Test Printer",
            "media_maps": maps
            or {
                "MAP01": {
                    "stock_name": "A4 White 80gsm",
                    "tray": "Tray 1",
                    "paper_size": {"width_mm": 210.0, "height_mm": 297.0},
                },
                "MAP02": {
                    "stock_name": "A4 Letterhead",
                    "tray": "Tray 2",
                    "paper_size": {"width_mm": 210.0, "height_mm": 297.0},
                },
            },
            "rules": rules,
        }
    )


def test_letterhead_only_on_page_one(indexed):
    profile = profile_with(
        [
            {
                "id": "letterhead-page-1",
                "scope": "medium_map",
                "match": {"medium_map": "MAP02"},
                "constraints": {"max_page_number": 1},
                "severity": "fail",
            }
        ]
    )
    path, index, analysis = indexed(
        "transition.afp", synthetic.transition_document(4)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    assert result.status == "fail"
    violations = [
        f for f in result.findings if f.code == "RULE_VIOLATION"
    ]
    assert violations
    assert any(f.page == 3 for f in violations)


def test_remaining_pages_must_be_plain(indexed):
    profile = profile_with(
        [
            {
                "id": "plain-after-page-one",
                "scope": "medium_map",
                "match": {"medium_map": "MAP01"},
                "constraints": {"min_page_number": 2},
                "severity": "fail",
            }
        ]
    )
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(3)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    violations = [
        f for f in result.findings if f.code == "RULE_VIOLATION"
    ]
    assert any(f.page == 1 for f in violations)


def test_unknown_medium_map_must_fail(indexed):
    profile = profile_with(
        [
            {
                "id": "unknown-map-fails",
                "scope": "medium_map",
                "match": {"medium_map": "__unmapped__"},
                "constraints": {},
                "severity": "fail",
            }
        ]
    )
    path, index, analysis = indexed(
        "duplex.afp", synthetic.duplex_document(2)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    violations = [
        f for f in result.findings if f.code == "RULE_VIOLATION"
    ]
    assert violations
    assert all(f.page in (1, 2) for f in violations)


def test_rule_can_be_ignored(indexed):
    profile = profile_with(
        [
            {
                "id": "ignored",
                "scope": "medium_map",
                "match": {"medium_map": "MAP01"},
                "constraints": {"max_page_number": 1},
                "severity": "ignore",
            }
        ]
    )
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(3)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    assert not [f for f in result.findings if f.code == "RULE_VIOLATION"]


def test_media_transition_rule(indexed):
    profile = profile_with(
        [
            {
                "id": "no-plain-to-letterhead",
                "scope": "media_transition",
                "match": {"from_medium_map": "MAP01", "to_medium_map": "MAP02"},
                "constraints": {"max_occurrences": 0},
                "severity": "warning",
            }
        ]
    )
    path, index, analysis = indexed(
        "transition.afp", synthetic.transition_document(4)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    violations = [
        f for f in result.findings if f.code == "RULE_VIOLATION"
    ]
    assert violations
    # The transition is now covered by a rule, so it is no longer
    # reported as an unexpected change.
    assert not [
        f for f in result.findings if f.code == "UNEXPECTED_MEDIA_CHANGE"
    ]


def test_document_rule_counts_distinct_medium_maps(indexed):
    profile = profile_with(
        [
            {
                "id": "single-medium-map",
                "scope": "document",
                "match": {},
                "constraints": {"max_distinct_medium_maps": 1},
                "severity": "warning",
            }
        ]
    )
    path, index, analysis = indexed(
        "transition.afp", synthetic.transition_document(4)
    )
    result = run_preflight(path, index, analysis, profile=profile)
    violations = [
        f for f in result.findings if f.code == "RULE_VIOLATION"
    ]
    assert violations
    assert violations[0].page is None


def test_unknown_constraint_is_rejected():
    from afpstudio.printer_profiles.schema import ProfileError

    try:
        profile_with(
            [
                {
                    "id": "typo",
                    "scope": "medium_map",
                    "match": {"medium_map": "MAP01"},
                    "constraints": {"max_page": 1},
                    "severity": "fail",
                }
            ]
        )
    except ProfileError as exc:
        assert "max_page" in str(exc)
    else:
        raise AssertionError("The typo should have been rejected.")
