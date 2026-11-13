from afpstudio.testing import synthetic
from afpstudio.viewer.index import build_index
from afpstudio.viewer.renderer import PageRenderer, select_replay_fields


def test_page_replay_selection_matches_original_scan(tmp_path):
    source = tmp_path / "job.afp"
    source.write_bytes(synthetic.transition_document(12))
    index = build_index(source)
    renderer = PageRenderer(source, index)
    for page in range(index.page_count):
        assert renderer.replay_fields(page) == select_replay_fields(index, page)
