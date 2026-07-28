import json

from shanhai import version


def _reset():
    version._cache = None


def test_reads_version_file(tmp_path, monkeypatch):
    f = tmp_path / "version.json"
    f.write_text(json.dumps({"build": 42, "sha": "abc1234", "dirty": False,
                             "stamped_at": "2026-07-28T13:00:00+08:00"}))
    monkeypatch.setattr(version, "_VERSION_FILE", f)
    _reset()
    assert version.build_info() == {"build": 42, "sha": "abc1234", "dirty": False,
                                    "stamped_at": "2026-07-28T13:00:00+08:00"}


def test_missing_file_degrades_to_dev(tmp_path, monkeypatch):
    # 新克隆的仓库没有 version.json(它是 gitignore 的)。降级路径最容易漏测,
    # 而它一旦抛异常就是整个服务起不来 / /api/version 返回 500。
    monkeypatch.setattr(version, "_VERSION_FILE", tmp_path / "nope.json")
    _reset()
    assert version.build_info()["sha"] == "dev"


def test_corrupt_json_degrades_to_dev(tmp_path, monkeypatch):
    f = tmp_path / "version.json"
    f.write_text("{ 这不是 JSON")
    monkeypatch.setattr(version, "_VERSION_FILE", f)
    _reset()
    assert version.build_info()["sha"] == "dev"


def test_partial_file_fills_missing_keys(tmp_path, monkeypatch):
    # 只有 build 一个键(手工改坏 / 旧格式):缺的键用降级值补齐,不 KeyError
    f = tmp_path / "version.json"
    f.write_text(json.dumps({"build": 7}))
    monkeypatch.setattr(version, "_VERSION_FILE", f)
    _reset()
    info = version.build_info()
    assert info["build"] == 7 and info["sha"] == "dev"


def test_result_is_a_copy(tmp_path, monkeypatch):
    # 调用方(FastAPI 序列化、测试)改到返回值不该污染进程内缓存
    f = tmp_path / "version.json"
    f.write_text(json.dumps({"build": 1, "sha": "aaa", "dirty": False, "stamped_at": ""}))
    monkeypatch.setattr(version, "_VERSION_FILE", f)
    _reset()
    version.build_info()["build"] = 999
    assert version.build_info()["build"] == 1
