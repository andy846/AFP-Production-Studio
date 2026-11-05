"""Inventory evidence is scoped to the operator's profile, never printer fact."""
import pytest

from afpstudio.printer_profiles.schema import ProfileError, validate_profile
from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index
from afpstudio.viewer.resources import resource_dependencies


def profile_data():
    return {"schema_version": "1.0", "printer_profile": "Test RIP",
            "media_maps": {}, "rules": [], "resource_inventory": [{
                "kind": "overlay", "name": "OVL01", "evidence_ref": "RIP inventory 2026-11-06",
                "verified_by": "UAT Operator", "verified_at": "2026-11-06"}]}


def test_inventory_preserves_attestation_and_never_becomes_actual(tmp_path):
    profile = validate_profile(profile_data())
    assert profile.as_dict()["resource_inventory"] == profile_data()["resource_inventory"]
    path = tmp_path / "overlay.afp"
    path.write_bytes(synthetic.bdt() + synthetic.bpg() +
                     synthetic.structured_field(0xD3AFD8, synthetic.encode_name("OVL01") + b"\0" * 6) +
                     synthetic.epg() + synthetic.edt())
    index = build_index(path)
    unknown = resource_dependencies(index, path)
    assert unknown["references"][0]["status"] == "external_unverified"
    result = resource_dependencies(index, path, inventory=profile.resource_inventory)
    ref = result["references"][0]
    assert ref["status"] == "inventory_attested_available"
    assert ref["declared_on_page"] == 1
    assert ref["evidence"]["evidence_ref"] == "RIP inventory 2026-11-06"
    assert "actual" not in ref


@pytest.mark.parametrize("mutate", [
    lambda d: d["resource_inventory"][0].pop("evidence_ref"),
    lambda d: d["resource_inventory"][0].update(verified_at="tomorrow"),
    lambda d: d["resource_inventory"].append(dict(d["resource_inventory"][0])),
    lambda d: d["resource_inventory"][0].update(kind="executable"),
])
def test_invalid_or_conflicting_inventory_is_rejected(mutate):
    data = profile_data()
    mutate(data)
    with pytest.raises(ProfileError):
        validate_profile(data)


def test_later_definition_is_observation_not_scope_resolution(tmp_path):
    path = tmp_path / "ambiguous.afp"
    path.write_bytes(synthetic.bdt() + synthetic.bpg() +
        synthetic.structured_field(0xD3AF5F, synthetic.encode_name("SEG01") + b"\0"*6) + synthetic.epg() +
        synthetic.structured_field(0xD3A85F, synthetic.encode_name("SEG01")) +
        synthetic.structured_field(0xD3A95F) + synthetic.edt())
    data = resource_dependencies(build_index(path), path)
    assert data["references"][0]["status"] == "definition_present_scope_unverified"
    assert data["references"][0]["definition_offsets"]
