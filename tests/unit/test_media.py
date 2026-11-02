"""The MO:DCA media walk."""

from __future__ import annotations

from afpstudio.preflight.media import (
    DUPLEX_CONTROL_NAMES,
    analyse_media,
    parse_copy_counts,
    parse_medium_descriptor,
    parse_modification,
    parse_page_descriptor,
)
from afpstudio.testing import synthetic


def test_medium_descriptor_is_decoded_in_millimetres():
    payload = synthetic.mdd(210.0, 297.0)[9:]
    descriptor = parse_medium_descriptor(payload)
    assert descriptor.x_unit_base == synthetic.UNIT_BASE_INCHES_10
    assert descriptor.x_units_per_unit_base == 2400
    assert abs(descriptor.width_mm - 210.0) < 0.5
    assert abs(descriptor.height_mm - 297.0) < 0.5


def test_zero_extent_means_no_stated_size():
    descriptor = parse_medium_descriptor(bytes(13))
    assert descriptor.width_mm is None
    assert descriptor.height_mm is None
    assert descriptor.size_known is False


def test_logical_resolution_ratio_has_no_physical_size():
    payload = (
        bytes([0x02, 0x02])
        + (2400).to_bytes(2, "big")
        + (2400).to_bytes(2, "big")
        + (1984).to_bytes(3, "big")
        + (2806).to_bytes(3, "big")
        + bytes([0x00])
    )
    descriptor = parse_medium_descriptor(payload)
    assert descriptor.width_mm is None


def test_copy_counts_are_repeating_groups_of_six_bytes():
    payload = synthetic.mcc([(1, 1, 3), (2, 4, 4)])[9:]
    groups = parse_copy_counts(payload)
    assert len(groups) == 2
    assert groups[0].starting_copy == 1
    assert groups[0].ending_copy == 1
    assert groups[0].mmc_identifier == 3
    assert groups[1].copies == 3


def test_modification_control_keywords():
    payload = synthetic.mmc(
        identifier=7, duplex=synthetic.DUPLEX_LONG_EDGE, source_selector=2
    )[9:]
    modification = parse_modification(payload)
    assert modification.identifier == 7
    assert modification.duplex_control == synthetic.DUPLEX_LONG_EDGE
    assert (
        DUPLEX_CONTROL_NAMES[modification.duplex_control] == "duplex-long-edge"
    )


def test_page_descriptor_size():
    payload = synthetic.pgd(210.0, 297.0)[9:]
    descriptor = parse_page_descriptor(payload)
    assert abs(descriptor.width_mm - 210.0) < 0.5
    assert abs(descriptor.height_mm - 297.0) < 0.5


def test_medium_map_is_found_and_named(indexed):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(2)
    )
    assert "MAP01" in analysis.medium_maps
    definition = analysis.medium_maps["MAP01"]
    assert definition.form_map == "FORM01"
    assert definition.media_type_name == "WHITE80"
    assert definition.media_destination_name == "TRAY1"
    assert definition.total_copies == 1


def test_invokes_are_recorded_with_their_level(indexed):
    path, index, analysis = indexed(
        "duplex.afp", synthetic.duplex_document(2)
    )
    assert len(analysis.invokes) == 1
    invoke = analysis.invokes[0]
    assert invoke.name == "MAPDUP"
    assert invoke.in_page is False
    assert invoke.malformed is False


def test_page_level_invoke_is_marked(indexed):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(1)
    )
    assert analysis.invokes[0].in_page is True


def test_page_descriptor_is_attributed_to_its_page(indexed):
    path, index, analysis = indexed(
        "simplex.afp", synthetic.simplex_document(3)
    )
    assert set(analysis.page_descriptors) == {0, 1, 2}
    descriptor = analysis.page_descriptors[0]
    assert abs(descriptor.width_mm - 210.0) < 0.5


def test_named_page_group_is_attributed_to_its_pages(indexed):
    data = synthetic.DocumentSpec(
        medium_maps=[
            synthetic.MediumMapSpec(
                name="MAP01", media_type="WHITE80", media_destination="TRAY1"
            )
        ],
        page_group="GROUP1",
        page_invokes=["MAP01", "MAP01"],
    ).build()
    path, index, analysis = indexed("group.afp", data)
    assert analysis.page_groups[0] == "GROUP1"
    assert analysis.page_groups[1] == "GROUP1"


def test_malformed_invoke_has_no_name(indexed):
    path, index, analysis = indexed(
        "bad.afp", synthetic.malformed_imm_document()
    )
    assert analysis.invokes[0].name == ""
    assert analysis.invokes[0].malformed is True


def test_real_corpus_media_is_decoded(media_corpus):
    from afpstudio.viewer.index import build_index

    path = media_corpus / "afplib_start.afp"
    index = build_index(path)
    analysis = analyse_media(index, path)
    assert "MM000000" in analysis.medium_maps
    assert analysis.invokes[0].name == "MM000000"
    definition = analysis.medium_maps["MM000000"]
    assert definition.total_copies == 1
    assert definition.modification is not None
    assert definition.modification.duplex_control == 0x01
