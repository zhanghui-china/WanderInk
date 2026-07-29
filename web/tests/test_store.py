from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from shanhai import store
from shanhai.schema import Legend


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.load("does_not_exist", root=tmp_path)


def test_project_dir_rejects_path_traversal(tmp_path):
    # M7:project_id 须是合法形状,不能靠它拼出越界路径。
    with pytest.raises(ValueError):
        store.project_dir("../etc", root=tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("a/b", root=tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("", root=tmp_path)


def test_project_dir_rejects_reserved_underscore_namespace(tmp_path):
    """下划线开头是共享目录的保留命名空间(VOICE_SAMPLE_DIRNAME = "_voice_samples"),
    不是作品。此前 project_dir 放行它,于是 DELETE /api/projects/_voice_samples
    会 rmtree 掉全站音色样本——delete_project 只判 is_dir 就删,不要求含 project.json。
    共享目录有自己的入口 voice_sample_dir(),不经过这里,拒掉不影响任何合法用途。"""
    with pytest.raises(ValueError):
        store.project_dir(store.VOICE_SAMPLE_DIRNAME, root=tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("_anything", root=tmp_path)


def test_project_dir_accepts_legal_id(tmp_path):
    assert store.project_dir("abc123_-XY", root=tmp_path) == tmp_path / "abc123_-XY"


def test_create_project_sets_created_at(tmp_path):
    p = store.create_project("雷峰塔", root=tmp_path)
    assert p.created_at != ""
    datetime.fromisoformat(p.created_at)  # 解析失败会抛异常使测试失败


def test_create_save_load(tmp_path):
    p = store.create_project("雷峰塔", root=tmp_path)
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["http://x"])
    store.save(p, root=tmp_path)
    p2 = store.load(p.project_id, root=tmp_path)
    assert p2.legend.title == "白蛇传"
    assert not (store.project_dir(p.project_id, root=tmp_path) / "project.json.tmp").exists()


def test_concurrent_save_no_torn_write(tmp_path):
    # A2 回归:固定 project.json.tmp 名下,多线程并发写同一项目会互相截断/触发
    # FileNotFoundError;唯一临时名 + 原子 replace 后,并发 save 不抛错,最终仍是某次完整写入。
    p = store.create_project("雷峰塔", root=tmp_path)

    def _save(i: int) -> None:
        q = store.load(p.project_id, root=tmp_path)
        q.scenic_spot = f"景点{i:03d}"
        store.save(q, root=tmp_path)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in [ex.submit(_save, i) for i in range(60)]:
            f.result()  # 任一线程抛异常都会在此冒出 → 测试失败

    loaded = store.load(p.project_id, root=tmp_path)  # 完整可解析,非半写文件
    assert loaded.scenic_spot.startswith("景点")
    # 唯一临时名在各自 replace 后均被消费,不留残余 .tmp
    assert not list(store.project_dir(p.project_id, root=tmp_path).glob("*.tmp"))


def test_default_root_is_late_bound(tmp_path, monkeypatch):
    """monkeypatch DEFAULT_ROOT 必须对所有 store 函数生效——它们的 root 默认值若写成
    `root: Path = DEFAULT_ROOT`,会在函数定义时求值绑死,monkeypatch 改模块属性完全无效。
    那个写法曾让几条 api 测试自以为隔离、实际往真实 projects/ 里写,在 DGX 上跑一次
    pytest 就在线上作品目录里留下了一个假作品。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("测试景点")
    assert (tmp_path / p.project_id / "project.json").exists()
    assert store.project_dir(p.project_id) == tmp_path / p.project_id
    assert store.voice_sample_dir().parent == tmp_path
    p.scenic_spot = "改过"
    store.save(p)
    assert store.load(p.project_id).scenic_spot == "改过"
