"""The printer profile schema and store."""

from __future__ import annotations

import json

import pytest

from afpstudio.printer_profiles.schema import (
    ProfileError,
    validate_profile,
)
from afpstudio.printer_profiles.store import ProfileStore


def test_sample_profile_is_valid(sample_profile):
    assert sample_profile.name == "Sample Production Printer"
    assert "MAP01" in sample_profile.media_maps
    assert sample_profile.media_maps["MAP01"].tray == "Tray 1"
    assert sample_profile.media_maps["MAP01"].paper_size.width_mm == 210.0
    assert len(sample_profile.rules) == 3


def test_profile_round_trips():
    data = {
        "schema_version": "1.0",
        "printer_profile": "Printer",
        "media_maps": {"MAP01": {"stock_name": "A4", "tray": "Tray 1"}},
        "rules": [],
    }
    profile = validate_profile(data)
    assert profile.as_dict() == {
        "schema_version": "1.0",
        "printer_profile": "Printer",
        "media_maps": {
            "MAP01": {"stock_name": "A4", "tray": "Tray 1"}
        },
        "rules": [],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "2.0"},
        {"printer_profile": ""},
        {"media_maps": {"MAP01": {}}},
        {"media_maps": {"MAP01": {"stock_name": "A4", "unknown": 1}}},
        {"media_maps": {"MAP01": {"stock_name": "A4", "paper_size": {"width_mm": 1}}}},
        {"media_maps": {"MAP01": {"stock_name": "A4", "duplex": "sideways"}}},
        {"rules": [{"id": "", "scope": "medium_map", "severity": "fail"}]},
        {"rules": [{"id": "a", "scope": "nope", "severity": "fail"}]},
        {"rules": [{"id": "a", "scope": "medium_map", "severity": "nope"}]},
        {"rules": [{"id": "a", "scope": "medium_map", "severity": "fail"}, {"id": "a", "scope": "medium_map", "severity": "fail"}]},
    ],
)
def test_invalid_profiles_are_rejected(mutation):
    data = {
        "schema_version": "1.0",
        "printer_profile": "Printer",
        "media_maps": {},
        "rules": [],
    }
    data.update(mutation)
    with pytest.raises(ProfileError):
        validate_profile(data)


def test_store_installs_the_sample(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    installed = store.ensure_sample()
    assert installed.exists()
    profile = store.load("sample_profile")
    assert profile.name == "Sample Production Printer"


def test_store_saves_and_lists(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    store.ensure_sample()
    store.save(
        "mine",
        {
            "schema_version": "1.0",
            "printer_profile": "Mine",
            "media_maps": {"M": {"stock_name": "S"}},
            "rules": [],
        },
    )
    assert "mine" in store.paths()[0].stem
    assert store.load("mine").name == "Mine"


def test_store_refuses_a_traversing_name(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    store.ensure_sample()
    with pytest.raises(ProfileError):
        store.load("../secrets")


@pytest.mark.parametrize("name", ["../secrets", "..\\secrets", "/absolute", "C:\\absolute", "bad:name", "."])
def test_save_refuses_unsafe_names(tmp_path, name):
    store = ProfileStore(tmp_path / "profiles")
    profile = {"schema_version": "1.0", "printer_profile": "Mine", "media_maps": {}, "rules": []}
    with pytest.raises(ProfileError):
        store.save(name, profile)


def test_a_broken_profile_does_not_break_the_list(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    store.ensure_sample()
    (tmp_path / "profiles" / "broken.json").write_text("{ not json", encoding="utf-8")
    profiles = store.load_all()
    assert "broken" not in profiles
    assert "sample_profile" in profiles


def test_saved_profile_is_valid_json(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    store.save(
        "mine",
        {
            "schema_version": "1.0",
            "printer_profile": "Mine",
            "media_maps": {"M": {"stock_name": "S"}},
            "rules": [],
        },
    )
    data = json.loads((tmp_path / "profiles" / "mine.json").read_text("utf-8"))
    assert data["printer_profile"] == "Mine"
