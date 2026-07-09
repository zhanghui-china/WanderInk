from shanhai import store
from shanhai.schema import Legend


def test_create_save_load(tmp_path):
    p = store.create_project("雷峰塔", root=tmp_path)
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["http://x"])
    store.save(p, root=tmp_path)
    p2 = store.load(p.project_id, root=tmp_path)
    assert p2.legend.title == "白蛇传"
    assert not (store.project_dir(p.project_id, root=tmp_path) / "project.json.tmp").exists()
