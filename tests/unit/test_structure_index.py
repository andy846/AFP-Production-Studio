from afpstudio.document_model.structure import build_structure_index
from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index


def test_nested_groups_and_raw_duplicate_tle_values(tmp_path):
    sf = synthetic.structured_field
    triplet = synthetic.triplet
    tle = sf(0xD3A090, triplet(0x02, b"\x01\x00NAME") + triplet(0x36, b"\x00\x00VALUE") + triplet(0x36, b"\x00\x00OTHER"))
    data = (sf(synthetic.BDT) + sf(synthetic.BNG, synthetic.encode_name("OUTER")) +
            sf(synthetic.BNG, synthetic.encode_name("INNER")) + sf(synthetic.BPG) + tle +
            sf(synthetic.EPG) + sf(synthetic.ENG) + sf(synthetic.ENG) + sf(synthetic.EDT))
    path = tmp_path / "index.afp"
    path.write_bytes(data)
    result = build_structure_index(path, build_index(path))
    assert result["complete"] is True
    assert result["documents"][0]["first_page"] == 1
    assert result["page_groups"][1]["parent_id"] == 1
    assert result["page_groups"][0]["last_page"] == 1
    tag = result["tags"][0]
    assert tag["page"] == 1
    assert tag["page_group_id"] == 2
    assert tag["status"] == "raw_encoding_unknown"
    assert [item["id"] for item in tag["triplets"]] == [2, 54, 54]
    assert data[tag["offset"]:tag["offset"] + len(tle)] == tle


def test_malformed_triplet_is_reported_with_original_offset(tmp_path):
    tle = synthetic.structured_field(0xD3A090, b"\x07\x36\x00")
    path = tmp_path / "broken.afp"
    path.write_bytes(synthetic.bdt() + tle + synthetic.edt())
    result = build_structure_index(path, build_index(path))
    assert not result["complete"]
    assert result["tags"][0]["status"] == "malformed"
    assert result["warnings"][0]["offset"] == len(synthetic.bdt()) + 9
