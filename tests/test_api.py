import os
import subprocess
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shanhai import api, runtime_config, store
from shanhai.schema import CharacterCard, Legend, Project, Script, StoryboardCell

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def _login_override():
    """现有端点已全部要求登录(Depends(current_user)),否则本文件测试会因 401 全挂。
    用依赖覆盖让测试在「已登录 testuser」语境下跑,不必真的走 cookie 登录流程
    (真实 cookie 登录流程由 tests/test_auth.py 覆盖)。"""
    api.app.dependency_overrides[api.current_user] = lambda: "testuser"
    yield
    api.app.dependency_overrides.clear()


def test_meta_lists_enums():
    j = client.get("/api/meta").json()
    assert j["minutes"] == [1, 3, 5]
    assert "guofeng_ink" in j["styles"]
    assert j["readonly"] is False               # 默认非只读


def test_meta_includes_voices():
    j = client.get("/api/meta").json()
    assert isinstance(j["voices"], list) and j["voices"]   # 至少回退 [tts_voice]


def test_meta_includes_loras():
    # loras 列表来自 loras.LORA_PRESETS 的 key,不是文件名——前端下拉框只需要短名。
    j = client.get("/api/meta").json()
    assert set(j["loras"]) == {"Real_ani_qwen", "figurine_qwen", "bjd.7ARL"}


def test_meta_voices_follow_s5_override(_isolated_config_path):
    """meta 音色列表须跟随 S5 实际生效的 TTS 后端(resolve_settings("s5")),而非仅全局层——
    否则用户把 s5 覆盖成别的 TTS 端点后,表单仍列全局音色、选中即令 S5 请求全失败降级静音。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        stages={"s5": runtime_config.ConfigOverride(tts_voices="cosy-a, cosy-b")},
    ))
    j = client.get("/api/meta").json()
    assert j["voices"] == ["cosy-a", "cosy-b"]              # s5 覆盖的音色,而非全局默认


def test_create_blocked_in_readonly(monkeypatch):
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.post("/api/projects", json={"scenic_spot": "雷峰塔"})
    assert r.status_code == 403                  # 只读模式拒绝新建生成
    assert client.get("/api/meta").json()["readonly"] is True


def test_readonly_engaged_via_env_file_only(tmp_path):
    # H7/P6 回归(真集成,子进程隔离):只在 .env 写 SHANHAI_READONLY=true、进程环境里没有,
    # 全新进程 import shanhai.api 后其模块级 _READONLY 必须为 True——即真正走 api.py 的
    # load_env()→os.getenv 顺序,而非在测试里旁路重放一遍解析逻辑(那样重构挪动/删掉
    # load_env 也测不出)。子进程避免污染本进程 os.environ 与已加载的 api 模块。
    (tmp_path / ".env").write_text("SHANHAI_READONLY=true\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHANHAI_")}
    r = subprocess.run(
        [sys.executable, "-c", "import shanhai.api as a; print(a._READONLY)"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True"


def test_create_validates_input():
    assert client.post("/api/projects", json={"scenic_spot": "  "}).status_code == 400
    assert client.post("/api/projects", json={"scenic_spot": "x", "minutes": 9}).status_code == 400
    assert client.post("/api/projects", json={"scenic_spot": "x", "tone": "搞笑"}).status_code == 400


def test_create_validates_speed_range():
    # A7:speed 越界会让 TTS/ffmpeg atempo 出错,须在 [0.5, 2.0] 内。
    assert client.post("/api/projects", json={"scenic_spot": "x", "speed": 0}).status_code == 400
    assert client.post("/api/projects", json={"scenic_spot": "x", "speed": 5}).status_code == 400


def test_create_rejects_oversized_story():
    # A7:story 无上限会把超大体喂给 LLM。
    r = client.post("/api/projects", json={"scenic_spot": "x", "story": "长" * 20001})
    assert r.status_code == 422


def test_get_missing_project_404():
    assert client.get("/api/projects/does_not_exist_xyz").status_code == 404


def test_get_illegal_project_id_404():
    # M7:project_id 含非法字符(路径遍历/空白等)时 project_dir 校验抛 ValueError,
    # 须映射为 404 而非 500(store.project_dir 是唯一落盘入口)。
    assert client.get("/api/projects/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/projects/bad.id").status_code == 404
    assert client.get("/api/projects/bad%20id").status_code == 404


def test_url_helpers_normalize_paths():
    assert api._file_url("abcd", "pages/page_01.png") == "/files/abcd/pages/page_01.png"
    assert api._file_url("abcd", "") is None
    # output['mp4'] 带 projects/<id> 前缀,需剥离
    assert api._mp4_url("projects/abcd/output/final.mp4") == "/files/abcd/output/final.mp4"
    assert api._mp4_url("") is None


def test_serialize_builds_urls():
    p = Project(project_id="abcd", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["x"])
    p.script = Script(title="白蛇传", theme="t", acts=[], characters=[
        CharacterCard(name="白娘子", role="蛇仙", personality="p", appearance="a",
                      turnaround_image="characters/白娘子.png")])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白娘子"], caption="初遇", emotion="宁静",
                                   image="pages/page_01.png", audio="audio/page_01.mp3",
                                   duration_ms=3200, status="confirmed")]
    p.output["mp4"] = "projects/abcd/output/final.mp4"
    p.output["zip"] = "projects/abcd/output/pages.zip"
    p.output["pdf"] = "projects/abcd/output/book.pdf"
    d = api._serialize(p)
    assert d["mp4"] == "/files/abcd/output/final.mp4"
    assert d["zip"] == "/files/abcd/output/pages.zip"
    assert d["pdf"] == "/files/abcd/output/book.pdf"
    assert d["pages"][0]["image"] == "/files/abcd/pages/page_01.png"
    assert d["pages"][0]["audio"] == "/files/abcd/audio/page_01.mp3"
    assert d["pages"][0]["visual_desc"] == "断桥"        # 分镜画面描述
    assert d["pages"][0]["scene_ref"] == "1-1"
    assert d["pages"][0]["characters"] == ["白娘子"]
    assert d["characters"][0]["image"] == "/files/abcd/characters/白娘子.png"
    assert d["script_title"] == "白蛇传"
    assert d["pages"][0]["silent"] is False              # 真人解说页非静音
    assert d["deliverable"] is True                      # 有成图页 → 可交付
    # 角色维度与页维度并列吐出:前端进度格靠它显示 S3 的 "N/M 位角色"(此前只有 S4 有数字)
    assert d["content_summary"] == {"total": 1, "imaged": 1, "narrated": 1, "silent": 0,
                                    "characters_imaged": 1, "characters_total": 1}


def test_serialize_appends_version_to_existing_files(tmp_path, monkeypatch):
    # /files 静态挂载不发 Cache-Control:存在的产物文件须带 ?v=<mtime> 做 cache-busting,
    # 否则重绘/重排后同名文件被浏览器缓存挡住不回源。
    proj = tmp_path / "abcd"
    (proj / "pages").mkdir(parents=True)
    (proj / "audio").mkdir()
    (proj / "characters").mkdir()
    (proj / "pages" / "page_01.png").write_bytes(b"img")
    (proj / "audio" / "page_01.mp3").write_bytes(b"aud")
    (proj / "characters" / "白娘子.png").write_bytes(b"chr")
    monkeypatch.setattr(store, "project_dir", lambda pid, *a, **k: tmp_path / pid)
    p = Project(project_id="abcd", scenic_spot="雷峰塔")
    p.script = Script(title="白蛇传", theme="t", acts=[], characters=[
        CharacterCard(name="白娘子", role="蛇仙", personality="p", appearance="a",
                      turnaround_image="characters/白娘子.png")])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白娘子"], caption="初遇", emotion="宁静",
                                   image="pages/page_01.png", audio="audio/page_01.mp3",
                                   duration_ms=3200, status="confirmed")]
    d = api._serialize(p)
    assert d["pages"][0]["image"].startswith("/files/abcd/pages/page_01.png?v=")
    assert d["pages"][0]["audio"].startswith("/files/abcd/audio/page_01.mp3?v=")
    assert d["characters"][0]["image"].startswith("/files/abcd/characters/白娘子.png?v=")
    # 不存在的文件不加版本参数,退回原始 URL
    p.storyboard[0].image = "pages/missing.png"
    d2 = api._serialize(p)
    assert d2["pages"][0]["image"] == "/files/abcd/pages/missing.png"


def _imaged_page(**kw) -> StoryboardCell:
    base = dict(index=1, scene_ref="1-1", visual_desc="v", characters=[], caption="c",
                emotion="宁静", image="pages/page_01.png", audio="audio/page_01.mp3",
                duration_ms=3200, status="confirmed")
    base.update(kw)
    return StoryboardCell(**base)


def test_pipeline_status_done_when_fully_narrated():
    p = Project(project_id="dlv1", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]                      # 有图 + 真人解说(silent 默认 False)
    p.output["mp4"] = "projects/dlv1/output/final.mp4"   # 已合成成片
    assert api._deliverable_status(p) == "done"


def test_pipeline_status_degraded_when_silent_pages():
    p = Project(project_id="dlv2", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page(silent=True)]           # 有图但静音兜底
    p.output["mp4"] = "projects/dlv2/output/final.mp4"
    st = api._deliverable_status(p)
    assert st.startswith("done(降级")                    # 诚实标注降级
    assert "1 页静音兜底" in st


def test_pipeline_status_error_when_nothing_imaged():
    p = Project(project_id="dlv3", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption="c", emotion="宁静", status="draft")]
    p.output["mp4"] = "projects/dlv3/output/final.mp4"   # 有 mp4 却无成图页(防御分支)
    assert api._deliverable_status(p).startswith("error")   # 无成图页 → 不可交付


def test_pipeline_status_degraded_when_pages_missing():
    # 回归:S4 部分页生成失败,s6 跳过后仍会正常合成 mp4(_content_cells 只挑 confirmed 页)——
    # 此时不能报纯 "done",必须诚实标注出图页数少于总页数,否则前端会显示"全部完成"这种假象
    # (2026-07 用户反馈:漫画没有全部生成,但还是说全部完成)。
    p = Project(project_id="dlv5", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page(index=1), StoryboardCell(
        index=2, scene_ref="1-2", visual_desc="v", characters=[],
        caption="c", emotion="宁静", status="failed")]
    p.output["mp4"] = "projects/dlv5/output/final.mp4"
    st = api._deliverable_status(p)
    assert st.startswith("done(降级")
    assert "1/2 页出图" in st


def test_pipeline_status_degraded_when_imaged_but_audio_cleared():
    # 关键回归:s5 双重失败(cell.audio="" 且 silent=False)的确认页——有图但无音轨,会被 s6 跳过。
    # content_summary['imaged'] 只看 image、把它算作已出图,若只判 imaged<total 会逃过降级;
    # 单算 composed(confirmed 且图/音齐备)才能诚实标注,绝不返回纯 done。
    p = Project(project_id="dlv6", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page(index=1),
                    _imaged_page(index=2, audio="", silent=False)]   # 有图但音轨被清
    p.output["mp4"] = "projects/dlv6/output/final.mp4"
    st = api._deliverable_status(p)
    assert st != "done"
    assert st.startswith("done(降级")
    assert "1/2 页入选成片" in st
    assert "1/2 页出图" not in st                          # imaged=2==total,出图降级不该误报


def test_pipeline_status_partial_when_not_composed():
    # 回归(rootcause 验证复现):有图 + 真人解说但尚未合成 mp4(如编辑后单步重跑 s5),
    # 不能报 done —— 否则 pipeline=done 而 mp4=null 就是被本次改动要根除的"假成片"。
    p = Project(project_id="dlv4", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]                      # 内容齐全但 output 为空
    assert api._deliverable_status(p) == "partial: 尚未合成成片"
    assert not p.output.get("mp4")


def test_serialize_marks_silent_and_non_deliverable():
    p = Project(project_id="ser2", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page(silent=True), _imaged_page(index=2, status="draft",
                                                            image="", audio="")]
    d = api._serialize(p)
    assert d["pages"][0]["silent"] is True
    assert d["content_summary"] == {"total": 2, "imaged": 1, "narrated": 0, "silent": 1,
                                    "characters_imaged": 0, "characters_total": 0}
    assert d["deliverable"] is True                      # 至少一页成图


@patch("shanhai.api._pipeline")            # 不真跑生成
@patch("shanhai.api.Settings")             # 不读 .env / 建真实客户端
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_returns_id_and_starts_job(mock_create, _save, _settings, mock_pipe):
    mock_create.return_value = Project(project_id="newid01", scenic_spot="黄鹤楼")
    r = client.post("/api/projects", json={"scenic_spot": "黄鹤楼", "minutes": 1})
    assert r.status_code == 200
    assert r.json()["project_id"] == "newid01"
    api._JOBS["newid01"].result(timeout=2)   # _JOBS 现在存 Future,等其结束再断言
    mock_pipe.assert_called_once()


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_stores_voice_and_speed(mock_create, _save, _settings, _pipe):
    p = Project(project_id="vsid01", scenic_spot="黄鹤楼")
    mock_create.return_value = p
    r = client.post("/api/projects", json={"scenic_spot": "黄鹤楼", "minutes": 1,
                                           "voice": "shimmer", "speed": 1.25})
    assert r.status_code == 200
    api._JOBS["vsid01"].result(timeout=2)
    assert p.params.voice == "shimmer"           # body.voice 写入 project.params
    assert p.params.speed == 1.25


def test_export_endpoint_runs_even_in_readonly(monkeypatch):
    monkeypatch.setattr(api, "_READONLY", True)          # 导出不受只读限制
    p = Project(project_id="expid", scenic_spot="雷峰塔")
    p.output["pdf"] = "projects/expid/output/book.pdf"
    p.output["zip"] = "projects/expid/output/pages.zip"
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.export.build_exports", return_value=p) as mock_build:
        r = client.post("/api/projects/expid/export")
    assert r.status_code == 200
    assert r.json()["pdf"] == "/files/expid/output/book.pdf"
    assert r.json()["zip"] == "/files/expid/output/pages.zip"
    mock_build.assert_called_once()


def test_create_rejects_when_queue_full():
    saved = dict(api._JOBS)
    api._JOBS.clear()
    try:
        api._JOBS.update({f"busy{i}": Future() for i in range(api.MAX_PENDING)})  # 均未完成
        r = client.post("/api/projects", json={"scenic_spot": "峨眉山"})
        assert r.status_code == 429                       # 队列满则拒绝新建
    finally:
        for f in api._JOBS.values():
            if not f.done():
                f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


@patch("shanhai.api.Settings")             # 不读 .env / 建真实客户端
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_concurrent_create_cleanup_consistent(mock_create, _save, _settings):
    # A6 回归:旧代码在 create_project 里裸迭代 _JOBS 做「清理已完成句柄」,两线程各自
    # 拿到重叠快照后 del 同一键 → 第二个 KeyError → 500。加 _JOBS_LOCK 后清理+背压+提交
    # 原子化,并发建项目既不 500 也不破坏 _JOBS。submit 打桩为即完成 Future,只测锁逻辑、
    # 不给共享单 worker _EXECUTOR 留后台任务(否则污染后续 run_step 用例)。
    saved = dict(api._JOBS)
    api._JOBS.clear()
    for i in range(5):  # 预置已完成句柄,供并发 create 竞相清理
        fut = Future(); fut.set_result(None)
        api._JOBS[f"done{i}"] = fut
    mock_create.side_effect = lambda spot: Project(project_id=os.urandom(4).hex(),
                                                   scenic_spot=spot)
    errors: list = []

    def _fake_submit(*_a, **_k) -> Future:
        fut = Future(); fut.set_result(None)
        return fut

    def _go() -> None:
        try:
            api.create_project(api.NewProject(scenic_spot="峨眉山", minutes=1))
        except api.HTTPException as e:
            if e.status_code != 429:            # 背压 429 是合法结果
                errors.append(e.status_code)
        except Exception as e:                  # noqa: BLE001 — KeyError 等即旧 bug
            errors.append(repr(e))

    try:
        with patch.object(api._EXECUTOR, "submit", side_effect=_fake_submit):
            threads = [threading.Thread(target=_go) for _ in range(24)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert not errors                       # 无 500 / 无 KeyError
        assert not any(k.startswith("done") for k in api._JOBS)  # 已完成句柄被清理干净
    finally:
        for f in api._JOBS.values():
            if not f.done():
                f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_reconcile_zombie_jobs(tmp_path):
    # A5:重启后 _JOBS 为空,磁盘上 running/queued 都是永远推不动的僵尸 → 改写为 error。
    running = store.create_project("running项目", root=tmp_path)
    running.status["pipeline"] = "running"
    store.save(running, root=tmp_path)
    queued = store.create_project("queued项目", root=tmp_path)
    queued.status["pipeline"] = "queued"
    store.save(queued, root=tmp_path)
    done = store.create_project("done项目", root=tmp_path)
    done.status["pipeline"] = "done"
    store.save(done, root=tmp_path)

    n = api.reconcile_zombie_jobs(tmp_path)
    assert n == 2                                # running + queued 各一条
    assert store.load(running.project_id, root=tmp_path).status["pipeline"].startswith("error")
    assert store.load(queued.project_id, root=tmp_path).status["pipeline"].startswith("error")
    assert store.load(done.project_id, root=tmp_path).status["pipeline"] == "done"  # 非僵尸不动


def test_list_projects_sorted_by_created_at_desc(tmp_path, monkeypatch):
    # 有 created_at 的项目按新到旧排序;混入无 created_at 的历史项目应排在最后。
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    old = store.create_project("旧项目", root=tmp_path)
    old.created_at = "2026-01-01T00:00:00+00:00"
    store.save(old, root=tmp_path)
    new = store.create_project("新项目", root=tmp_path)
    new.created_at = "2026-06-01T00:00:00+00:00"
    store.save(new, root=tmp_path)
    legacy_older = store.create_project("历史项目_更早", root=tmp_path)
    legacy_older.created_at = ""
    store.save(legacy_older, root=tmp_path)
    legacy_newer = store.create_project("历史项目_更新", root=tmp_path)
    legacy_newer.created_at = ""
    store.save(legacy_newer, root=tmp_path)
    # 人为拉开两个历史项目的 mtime,验证组内也是按 mtime 新到旧排序,而不只是碰巧的写入顺序
    older_path = store.project_dir(legacy_older.project_id, tmp_path) / "project.json"
    newer_path = store.project_dir(legacy_newer.project_id, tmp_path) / "project.json"
    now = os.stat(newer_path).st_mtime
    os.utime(older_path, (now - 100, now - 100))
    os.utime(newer_path, (now, now))

    r = client.get("/api/projects")
    assert r.status_code == 200
    ids = [item["project_id"] for item in r.json()]
    assert (
        ids.index(new.project_id)
        < ids.index(old.project_id)
        < ids.index(legacy_newer.project_id)
        < ids.index(legacy_older.project_id)
    )


def test_list_projects_fields_and_skips_corrupt(tmp_path, monkeypatch):
    # 轻量化后输出结构/字段与改前等价:project_id/scenic_spot/owner/pipeline/mp4(经 _mp4_url 转换);
    # 损坏/非法 JSON 的 project.json 仍被跳过,不让整表失败。
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone"
    p.status["pipeline"] = "done"
    p.output["mp4"] = f"projects/{p.project_id}/output/final.mp4"
    store.save(p, root=tmp_path)
    bad_dir = tmp_path / "brokenpid"          # 非法 JSON 的损坏项目
    bad_dir.mkdir()
    (bad_dir / "project.json").write_text("{not valid json", encoding="utf-8")

    r = client.get("/api/projects")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1                    # 损坏项目被跳过
    item = items[0]
    assert set(item) == {"project_id", "scenic_spot", "owner", "pipeline", "mp4"}
    assert item["project_id"] == p.project_id
    assert item["scenic_spot"] == "雷峰塔"
    assert item["owner"] == "someone"
    assert item["pipeline"] == "done"
    # 文件不存在故无 ?v= 后缀,与 _mp4_url 对不存在文件的处理一致
    assert item["mp4"] == f"/files/{p.project_id}/output/final.mp4"


def test_list_projects_skips_non_object_json(tmp_path, monkeypatch):
    # 合法 JSON 但非对象(null/[]/42/字符串):json.loads 成功但 d.get(...) 会抛 AttributeError,
    # 一个坏文件不得拖垮整表——须被 isinstance 守卫跳过,端点仍 200 且只返回正常项目。
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    good = store.create_project("雷峰塔", root=tmp_path)
    store.save(good, root=tmp_path)
    for name, content in (("nullpid", "null"), ("listpid", "[]"),
                          ("numpid", "42"), ("strpid", '"hi"')):
        d = tmp_path / name
        d.mkdir()
        (d / "project.json").write_text(content, encoding="utf-8")

    r = client.get("/api/projects")
    assert r.status_code == 200
    items = r.json()
    assert [it["project_id"] for it in items] == [good.project_id]  # 非对象项目全被跳过


def test_delete_project_requires_admin(monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    r = client.delete("/api/projects/anything")
    assert r.status_code == 403


def test_delete_project_admin_removes_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    workdir = tmp_path / "delme01"
    workdir.mkdir()
    (workdir / "project.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(api.store, "project_dir", lambda pid: workdir)

    r = client.delete("/api/projects/delme01")
    assert r.status_code == 200
    assert r.json() == {"deleted": True}
    assert not workdir.exists()


def test_delete_project_404_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setattr(api.store, "project_dir", lambda pid: tmp_path / "nope")
    r = client.delete("/api/projects/does-not-exist")
    assert r.status_code == 404


def test_delete_project_rejects_when_job_pending(monkeypatch):
    # 有未完成生成作业时删除返回 409,避免删掉正在被后台线程写入的目录。
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["delbusy"] = f
    try:
        r = client.delete("/api/projects/delbusy")
        assert r.status_code == 409
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_delete_project_blocked_in_readonly_mode(monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.delete("/api/projects/anything")
    assert r.status_code == 403


def test_export_rejects_when_job_pending():
    # A3:项目有未完成生成作业时导出返回 409,避免读半成品/回滚管线进度。
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["expbusy"] = f
    try:
        r = client.post("/api/projects/expbusy/export")
        assert r.status_code == 409
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


# ---------- 归属 / 队列 / 取消(Phase 2) ----------

@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_sets_owner_to_current_user(mock_create, _save, _settings, _pipe):
    p = Project(project_id="ownid01", scenic_spot="黄鹤楼")
    mock_create.return_value = p
    r = client.post("/api/projects", json={"scenic_spot": "黄鹤楼", "minutes": 1})
    assert r.status_code == 200
    api._JOBS["ownid01"].result(timeout=2)
    assert p.owner == "testuser"      # 依赖覆盖令 current_user 恒为 testuser(见 _login_override)


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_passes_multi_panel(mock_create, _save, _settings, _pipe):
    p = Project(project_id="mpid01", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects",
                    json={"scenic_spot": "花果山", "minutes": 1, "multi_panel": True})
    assert r.status_code == 200
    api._JOBS["mpid01"].result(timeout=2)
    assert p.params.multi_panel is True


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_multi_panel_defaults_false(mock_create, _save, _settings, _pipe):
    p = Project(project_id="mpid02", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects", json={"scenic_spot": "花果山", "minutes": 1})
    assert r.status_code == 200
    api._JOBS["mpid02"].result(timeout=2)
    assert p.params.multi_panel is False


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_use_hermes_agent_defaults_true(mock_create, _save, _settings, _pipe):
    p = Project(project_id="haid01", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects", json={"scenic_spot": "花果山", "minutes": 1})
    assert r.status_code == 200
    api._JOBS["haid01"].result(timeout=2)
    assert p.params.use_hermes_agent is True


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_passes_use_hermes_agent_false(mock_create, _save, _settings, _pipe):
    p = Project(project_id="haid02", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects",
                    json={"scenic_spot": "花果山", "minutes": 1, "use_hermes_agent": False})
    assert r.status_code == 200
    api._JOBS["haid02"].result(timeout=2)
    assert p.params.use_hermes_agent is False


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_passes_master_skill(mock_create, _save, _settings, _pipe):
    p = Project(project_id="swid01", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects",
                    json={"scenic_spot": "花果山", "minutes": 1, "master_skill": True})
    assert r.status_code == 200
    api._JOBS["swid01"].result(timeout=2)
    assert p.params.master_skill is True


def test_use_master_skill_gate_requires_hermes_backend():
    # 开关开了但该环节后端不是 hermes-agent → gate 落为 False(退化普通生成,不把斜杠发给别的模型)。
    # 用 S1/S2 两个 stage_label 分别验证,gate 逻辑与调用哪个环节无关,只看 stage_settings。
    from shanhai.config import Settings
    p = Project(project_id="g1", scenic_spot="花果山")
    p.params.master_skill = True
    hermes = Settings(_env_file=None, base_url="http://127.0.0.1:8642/v1", api_key="x",
                      llm_model="hermes-agent")
    other = Settings(_env_file=None, base_url="https://api.stepfun.com/v1", api_key="x",
                     llm_model="step-3.7-flash")
    assert api._use_master_skill(p, hermes, "S1") is True
    assert api._use_master_skill(p, other, "S1") is False
    assert api._use_master_skill(p, hermes, "S2") is True
    assert api._use_master_skill(p, other, "S2") is False
    p.params.master_skill = False
    assert api._use_master_skill(p, hermes, "S1") is False   # 开关关 → 恒 False


def test_cancel_rejects_non_owner():
    p = Project(project_id="cancelid1", scenic_spot="雷峰塔", owner="someoneelse")
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["cancelid1"] = f
    try:
        with patch("shanhai.api.store.load", return_value=p):
            r = client.post("/api/projects/cancelid1/cancel")
        assert r.status_code == 403
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_cancel_succeeds_for_owner_when_queued():
    # 未开始执行的 Future(未被线程池取走)——.cancel() 返回 True,直接取消成功。
    p = Project(project_id="cancelid2", scenic_spot="雷峰塔", owner="testuser")
    saved = dict(api._JOBS)
    api._JOBS.clear()
    api._JOBS["cancelid2"] = Future()
    try:
        with patch("shanhai.api.store.load", return_value=p), \
             patch("shanhai.api.store.save") as mock_save:
            r = client.post("/api/projects/cancelid2/cancel")
        assert r.status_code == 200
        assert r.json() == {"cancelled": True}
        assert p.status["pipeline"] == "cancelled"
        mock_save.assert_called_once()
    finally:
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_cancel_rejects_already_finished_job():
    # 作业已跑完(Future done,尚未被下次提交清理出 _JOBS)时应 400,而不是误标 _CANCELLED——
    # 那样标记再无人消费,会一直残留污染该项目下次重跑(对抗审计发现的取消标记泄漏根因之一)。
    p = Project(project_id="cancelid3", scenic_spot="雷峰塔", owner="testuser")
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    f.set_result(None)                                     # 已完成
    api._JOBS["cancelid3"] = f
    try:
        with patch("shanhai.api.store.load", return_value=p):
            r = client.post("/api/projects/cancelid3/cancel")
        assert r.status_code == 400
        assert "cancelid3" not in api._CANCELLED
    finally:
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_cancel_marks_running_job_for_cooperative_cancel():
    # 已被线程池取走开始执行的作业(.cancel() 返回 False):走协作式标记分支。
    p = Project(project_id="cancelid4", scenic_spot="雷峰塔", owner="testuser")
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    f.set_running_or_notify_cancel()                       # PENDING -> RUNNING,之后 cancel() 返回 False
    api._JOBS["cancelid4"] = f
    try:
        with patch("shanhai.api.store.load", return_value=p):
            r = client.post("/api/projects/cancelid4/cancel")
        assert r.status_code == 200
        assert r.json() == {"cancelling": True}
        assert "cancelid4" in api._CANCELLED
    finally:
        api._CANCELLED.discard("cancelid4")
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_check_cancelled_consumes_flag_once():
    api._CANCELLED.add("consumeid")
    try:
        assert api._check_cancelled("consumeid") is True
        assert api._check_cancelled("consumeid") is False   # 命中即移除,不重复触发
    finally:
        api._CANCELLED.discard("consumeid")


def test_is_cancelled_does_not_consume_flag():
    api._CANCELLED.add("peekid")
    try:
        assert api._is_cancelled("peekid") is True
        assert api._is_cancelled("peekid") is True   # 非消费型 peek,不会移除标记
        assert "peekid" in api._CANCELLED
    finally:
        api._CANCELLED.discard("peekid")


def test_cancel_queued_persists_fresh_reload_not_stale_snapshot():
    # 批次4 finding-2 回归:直接取消(排队未开始)写 cancelled 必须在 _project_lock 内重新
    # store.load 最新快照,而非复用锁外拿到的陈旧 p —— 否则会覆盖窗口期内并发编辑端点的改动(丢更新)。
    stale = Project(project_id="cxfreshid", scenic_spot="旧", owner="testuser")  # 锁外(owner 校验)拿到的陈旧快照
    fresh = Project(project_id="cxfreshid", scenic_spot="新", owner="testuser")  # 锁内重载应拿到的最新快照
    loads = [stale, fresh]                                    # 第 1 次 owner 校验、第 2 次锁内重载
    saved = {}
    saved_jobs = dict(api._JOBS)
    api._JOBS.clear()
    api._JOBS["cxfreshid"] = Future()                         # PENDING → f.cancel() 返回 True 直接取消
    try:
        with patch("shanhai.api.store.load", side_effect=lambda *a, **k: loads.pop(0)), \
             patch("shanhai.api.store.save", side_effect=lambda p, **k: saved.update(obj=p)):
            r = client.post("/api/projects/cxfreshid/cancel")
        assert r.status_code == 200
        assert r.json() == {"cancelled": True}
        assert saved["obj"] is fresh                          # 落盘的是锁内重载的 fresh,不是陈旧的 stale
        assert fresh.status["pipeline"] == "cancelled"
        assert stale.status.get("pipeline") != "cancelled"    # 陈旧快照没被当作落盘对象
    finally:
        api._JOBS.clear()
        api._JOBS.update(saved_jobs)


def test_get_queue_excludes_finished_jobs():
    # 批次4 finding-4 回归:已完成(f.done())的作业不再滞留队列,get_queue 读路径顺带清理。
    p = Project(project_id="qdoneid", scenic_spot="雷峰塔", owner="testuser")
    saved_jobs = dict(api._JOBS)
    api._JOBS.clear()
    done = Future()
    done.set_result(None)                                     # 已完成
    api._JOBS["qdoneid"] = done
    api._JOBS["qrunid"] = Future()                            # 仍在队列(未完成)
    try:
        with patch("shanhai.api.store.load",
                   side_effect=lambda pid, *a, **k: Project(project_id=pid, scenic_spot="雷峰塔")):
            r = client.get("/api/queue")
        ids = [row["project_id"] for row in r.json()]
        assert "qdoneid" not in ids                           # 已完成的不返回
        assert "qrunid" in ids                                # 未完成的仍在
        assert "qdoneid" not in api._JOBS                     # 且已被顺带清理出 _JOBS
    finally:
        api._JOBS.clear()
        api._JOBS.update(saved_jobs)


def test_pipeline_clears_stale_cancel_flag_on_completion():
    # 回归:取消发生在最后一环节执行期间时,_CANCELLED 不会被再次检查消费,曾经会一直残留,
    # 误伤该项目下次重跑。_pipeline 收尾(finally)必须清掉本次作业的标记。
    from unittest.mock import MagicMock
    p = Project(project_id="leakid", scenic_spot="雷峰塔")
    mock_settings = MagicMock()
    settings = {k: mock_settings for k in ("s0", "s1", "s2", "s3", "s4", "s5")}
    clients = {
        "s0": (MagicMock(),), "s1": (MagicMock(),), "s2": (MagicMock(),),
        "s3": (MagicMock(), MagicMock()), "s4": (MagicMock(), MagicMock()),
        "s5": (MagicMock(), MagicMock(), MagicMock(), MagicMock()),
    }
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.resolve_stage_clients", return_value=(settings, clients)), \
         patch("shanhai.api.s0_legend") as s0, \
         patch("shanhai.api.s1_script") as s1, \
         patch("shanhai.api.s2_storyboard") as s2, \
         patch("shanhai.api.s3_characters") as s3, \
         patch("shanhai.api.s4_pages") as s4, \
         patch("shanhai.api.s5_audio") as s5, \
         patch("shanhai.api.s6_compose") as s6:
        s0.from_text.return_value = p
        for m in (s1, s2, s3, s4, s5, s6):
            m.run.return_value = p
        api._CANCELLED.add("leakid")          # 模拟取消请求在最后环节执行期间到达
        api._pipeline("leakid", runtime_config.AppConfig(), "自备故事")
    assert "leakid" not in api._CANCELLED     # 收尾必须清掉,不留给下次重跑


def test_pipeline_prelude_exception_falls_to_error_not_stuck_queued():
    # 批次4 finding-1 回归:序言(store.load/resolve_stage_clients/_clients)抛异常时,项目必须
    # 落 error(走 _save_error),而非被 Future 静默吞掉、永久卡 queued(前端无限轮询)。
    p = Project(project_id="preludeid", scenic_spot="雷峰塔")
    p.status["pipeline"] = "queued"
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.resolve_stage_clients", side_effect=RuntimeError("畸形 base_url")):
        api._pipeline("preludeid", runtime_config.AppConfig(), "自备故事")
    assert p.status["pipeline"].startswith("error")   # 不再卡 queued
    assert "preludeid" not in api._CANCELLED           # finally 仍执行,不残留标记


def test_run_step_prelude_exception_falls_to_error_not_stuck_queued():
    # 批次4 finding-1 回归(单步版):_run_step 序言 resolve_settings 抛错也须落 error 而非卡 queued。
    p = Project(project_id="steppreludeid", scenic_spot="雷峰塔")
    p.status["pipeline"] = "queued"
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.resolve_settings", side_effect=RuntimeError("畸形 base_url")):
        api._run_step("steppreludeid", "s6", runtime_config.AppConfig())
    assert p.status["pipeline"].startswith("error")
    assert "steppreludeid" not in api._CANCELLED


def test_pipeline_records_step_and_total_timing(tmp_path: Path):
    # 每步开始/结束都要落 started_at/elapsed_s,整体落 pipeline_started_at/pipeline_finished_at,
    # 供前端时间线展示每步及总耗时。
    # ⚠️ s3~s6 的 mock 必须真的往 workdir 里写文件,否则会被空跑守卫判成"什么都没做"、
    # 计时键一个不写(守卫只看产物指纹)。s0/s1/s2 不设守卫,无所谓。
    from unittest.mock import MagicMock
    p = Project(project_id="timingid", scenic_spot="雷峰塔")
    mock_settings = MagicMock()
    mock_settings.image_endpoint = ("https://example.com/v1", "key")  # image_concurrency 需要能解包
    settings = {k: mock_settings for k in ("s0", "s1", "s2", "s3", "s4", "s5")}
    clients = {
        "s0": (MagicMock(),), "s1": (MagicMock(),), "s2": (MagicMock(),),
        "s3": (MagicMock(), MagicMock()), "s4": (MagicMock(), MagicMock()),
        "s5": (MagicMock(), MagicMock(), MagicMock(), MagicMock()),
    }
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api.resolve_stage_clients", return_value=(settings, clients)), \
         patch("shanhai.api.s0_legend") as s0, \
         patch("shanhai.api.s1_script") as s1, \
         patch("shanhai.api.s2_storyboard") as s2, \
         patch("shanhai.api.s3_characters") as s3, \
         patch("shanhai.api.s4_pages") as s4, \
         patch("shanhai.api.s5_audio") as s5, \
         patch("shanhai.api.s6_compose") as s6:
        s0.from_text.return_value = p
        for i, m in enumerate((s1, s2, s3, s4, s5, s6)):
            def _write(*_args, _n=i, **_kwargs):
                (tmp_path / f"artifact_{_n}.bin").write_bytes(b"x")   # 真的产出文件,指纹随之变化
                return p
            m.run.side_effect = _write
        api._pipeline("timingid", runtime_config.AppConfig(), "自备故事")

    assert p.status["pipeline"] != "running"   # 已跑到终态(mock 未产出可交付内容,具体终态值不是本测试重点)
    assert p.status["pipeline_started_at"]
    assert p.status["pipeline_finished_at"]
    for step in ("s0", "s1", "s2", "s3", "s4", "s5", "s6"):
        assert p.status[f"{step}_started_at"]
        assert p.status[f"{step}_finished_at"]
        float(p.status[f"{step}_elapsed_s"])   # 能转成 float,解析失败即测试失败
        # 自洽性:结束时刻不早于开始时刻(ISO 8601 字符串可直接按字典序/datetime 比较)。
        assert p.status[f"{step}_finished_at"] >= p.status[f"{step}_started_at"]


def test_pipeline_use_hermes_agent_false_falls_back_to_global_llm():
    # use_hermes_agent=False:S0/S1 应跳过按环节覆盖(如 hermes-agent),改用仅叠加全局默认
    # 的 Settings/client——即调 resolve_settings(None, cfg),而非 resolve_stage_clients 给的
    # 那份按环节覆盖后的 s0/s1 client。S2 及之后的环节不受影响,仍用 resolve_stage_clients 原样结果。
    from unittest.mock import MagicMock
    p = Project(project_id="hafid", scenic_spot="雷峰塔")
    p.params.use_hermes_agent = False
    mock_settings = MagicMock()
    mock_settings.image_endpoint = ("https://example.com/v1", "key")
    stage_settings = {k: mock_settings for k in ("s0", "s1", "s2", "s3", "s4", "s5")}
    hermes_llm = MagicMock(name="hermes_llm")
    stage_clients = {
        "s0": (hermes_llm,), "s1": (hermes_llm,), "s2": (MagicMock(),),
        "s3": (MagicMock(), MagicMock()), "s4": (MagicMock(), MagicMock()),
        "s5": (MagicMock(), MagicMock(), MagicMock(), MagicMock()),
    }
    fallback_llm = MagicMock(name="fallback_llm")
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.resolve_stage_clients", return_value=(stage_settings, stage_clients)), \
         patch("shanhai.api.resolve_settings", return_value=mock_settings) as resolve_settings, \
         patch("shanhai.api._clients", return_value=(fallback_llm, MagicMock(), MagicMock(),
                                                      MagicMock())), \
         patch("shanhai.api.s0_legend") as s0, \
         patch("shanhai.api.s1_script") as s1, \
         patch("shanhai.api.s2_storyboard") as s2, \
         patch("shanhai.api.s3_characters") as s3, \
         patch("shanhai.api.s4_pages") as s4, \
         patch("shanhai.api.s5_audio") as s5, \
         patch("shanhai.api.s6_compose") as s6:
        s0.from_text.return_value = p
        for m in (s1, s2, s3, s4, s5, s6):
            m.run.return_value = p
        api._pipeline("hafid", runtime_config.AppConfig(), "自备故事")

    resolve_settings.assert_any_call(None, runtime_config.AppConfig())
    assert s0.from_text.call_args[0][1] is fallback_llm    # S0 用了回退 client,不是 hermes
    assert s1.run.call_args[0][1] is fallback_llm          # S1 同上
    assert s2.run.call_args[0][1] is stage_clients["s2"][0]  # S2 不受影响,原样用 resolve_stage_clients 的结果


@patch("shanhai.api.Settings")
def test_run_step_records_step_timing(_settings, tmp_path: Path):
    # ⚠️ mock 必须真的往 workdir 里写文件,否则会被空跑守卫判成"什么都没做"、三个计时键
    # 一字不写,断言全空(守卫只看产物指纹,见 _mark_step_elapsed)。
    from unittest.mock import MagicMock
    p = Project(project_id="stepTimingId", scenic_spot="雷峰塔")
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s6_compose") as s6:
        def _write_mp4(*_args, **_kwargs):
            (tmp_path / "final.mp4").write_bytes(b"mp4")
            return p
        s6.run.side_effect = _write_mp4
        api._run_step("stepTimingId", "s6", runtime_config.AppConfig())

    assert p.status["s6_started_at"]
    assert p.status["s6_finished_at"]
    float(p.status["s6_elapsed_s"])
    assert p.status["s6_finished_at"] >= p.status["s6_started_at"]   # 自洽性:结束不早于开始
    assert p.status["pipeline_started_at"]
    assert p.status["pipeline_finished_at"]


@patch("shanhai.api.Settings")
def test_run_step_reflects_latest_run_only(_settings, tmp_path: Path):
    # 用户拍板语义:elapsed_s 是**最近一次**运行的耗时,不是历史累计(回退了 445fcaa 的累加逻辑)。
    # 续跑两轮(17s、7s)后,elapsed_s 须是第二轮的 7.0,而不是两轮之和 24.0;started_at 须
    # 跟着第二轮前移(不再固定第一轮时刻),finished_at 随每轮真实完成更新。
    #
    # ⚠️ mock 必须让 s4_pages.run 真的**往 workdir 里写文件**——空跑守卫的判据是产物目录的
    # 文件指纹,只改内存里的 cell.image 骗不过它,会被判成没做事、三个计时键原样不动、断言全空。
    # 该守卫本身是为了修另一个故障(配音第一次就生成好了,重跑只显示 2s):见
    # test_run_step_skips_timing_when_nothing_regenerated。
    from unittest.mock import MagicMock
    p = Project(project_id="accumTimingId", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page(index=1), _imaged_page(index=2, image="")]
    pages = tmp_path / "pages"
    pages.mkdir()
    # 第一轮:t0=100 → 结束时 monotonic()=117,耗时 17s;第二轮:t0=200 → 结束时 207,耗时 7s。
    monotonic_values = iter([100.0, 117.0, 200.0, 207.0])
    written = iter(["page_02.png", "page_03.png"])

    def _write_a_page(*_args, **_kwargs):
        (pages / next(written)).write_bytes(b"png")   # 真的产出文件,指纹随之变化
        return p

    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s4_pages") as s4, \
         patch("shanhai.api.time.monotonic", side_effect=monotonic_values):
        s4.run.side_effect = _write_a_page
        api._run_step("accumTimingId", "s4", runtime_config.AppConfig())
        first_started_at = p.status["s4_started_at"]
        assert p.status["s4_elapsed_s"] == "17.0"
        first_finished_at = p.status["s4_finished_at"]

        api._run_step("accumTimingId", "s4", runtime_config.AppConfig())

    assert p.status["s4_started_at"] != first_started_at   # 开始时间随续跑前移,不再固定首跑
    assert p.status["s4_elapsed_s"] == "7.0"                # 只反映第二轮,不与首轮累加
    assert p.status["s4_finished_at"] != first_finished_at  # 结束时间随每轮真实完成更新


@patch("shanhai.api.Settings")
def test_run_step_skips_timing_when_nothing_regenerated(_settings, tmp_path: Path):
    # 「配音 2s」故障的回归测试:DGX 上实测过 5 个作品的 s5_elapsed_s 全变成 2.0——每页音频
    # 其实早就在盘上、一页没重做,那 2 秒纯粹是 s5_audio.run 幂等跳过全部子项的空转开销,
    # 却把此前真实的十几分钟覆盖掉了。本轮一个产物文件都没重写时,三个计时键必须一字不动。
    from unittest.mock import MagicMock
    p = Project(project_id="skipTimingId", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    p.output = {"mp4": "output/final.mp4"}   # 已出片,用来验证空跑不会误毁成片
    p.status = {
        "s5_started_at": "2020-01-01T00:00:00+00:00",
        "s5_finished_at": "2020-01-01T00:15:00+00:00",
        "s5_elapsed_s": "900.0",
        "s6_elapsed_s": "300.0",
    }
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "page_01.mp3").write_bytes(b"mp3")   # 产物已在盘上,本轮不会被重写
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s5_audio") as s5:
        s5.run.return_value = p   # 原样返回:模拟全部子项因已有音频被幂等跳过,无新产物
        api._run_step("skipTimingId", "s5", runtime_config.AppConfig())

    assert p.status["s5_started_at"] == "2020-01-01T00:00:00+00:00"    # 三个计时键一字未动
    assert p.status["s5_finished_at"] == "2020-01-01T00:15:00+00:00"
    assert p.status["s5_elapsed_s"] == "900.0"
    assert "s5_running_since" not in p.status                          # 进行中标记要收干净
    # 空跑不触发下游级联:什么都没重做,下游就没过期。这一条同时守着一个既有 bug——
    # 原先 p.output.clear() 是无条件的,在已出片的项目上点重新生成会白白毁掉 mp4/zip/pdf。
    assert p.output == {"mp4": "output/final.mp4"}
    assert p.status["s6_elapsed_s"] == "300.0"


def test_mark_step_writes_three_timing_keys_only_at_the_end(tmp_path: Path):
    # 三个计时键必须在收尾时**原子写入**,开工时一个都不碰。
    # 反例(第一版就是这么写的,被审计打回):开工就改写 started_at、pop 掉 finished_at,
    # 紧接着一次落盘;此时步骤体若抛异常或进程被杀,盘上会留下"本轮的 started_at +
    # 上一轮的 elapsed_s + 没有 finished_at"这种分属两次运行的错配状态,前端永久显示"进行中"
    # 却同时列着上一轮的耗时,而且下一次空跑会把它原样还原、自锁住。
    p = Project(project_id="markstartid", scenic_spot="雷峰塔")
    p.status.update({"s4_started_at": "2020-01-01T00:00:00+00:00",
                     "s4_finished_at": "2020-01-01T00:05:00+00:00",
                     "s4_elapsed_s": "300.0"})
    start = api._mark_step_started(p, "s4", tmp_path)
    # 开工阶段(= 中途崩溃时盘上的样子):上一轮的三个键完整保留,自洽且不含错配
    assert p.status["s4_started_at"] == "2020-01-01T00:00:00+00:00"
    assert p.status["s4_finished_at"] == "2020-01-01T00:05:00+00:00"
    assert p.status["s4_elapsed_s"] == "300.0"
    assert p.status["s4_running_since"]        # 进行中改用单独的键,不污染那三个

    (tmp_path / "new.png").write_bytes(b"png")   # 本轮真的产出了东西
    assert api._mark_step_elapsed(p, "s4", start, tmp_path) is True
    assert p.status["s4_started_at"] != "2020-01-01T00:00:00+00:00"
    assert p.status["s4_finished_at"] != "2020-01-01T00:05:00+00:00"
    assert "s4_running_since" not in p.status


def test_run_step_marks_cancelled_when_flag_set_during_step():
    # 用户在环节执行期间点了取消(s3/s4/s5 内部 cancel_check 提前收尾,但用的是非消费型
    # _is_cancelled,标记仍留在 _CANCELLED 里),_run_step 跑完该环节后须在此消费掉标记、
    # 把 pipeline 标成 cancelled——否则会被 _deliverable_status 误判成普通 partial/done,
    # 用户看不到"这是我自己取消的"这个诚实反馈。
    from unittest.mock import MagicMock

    from shanhai.config import Settings
    p = Project(project_id="cancelmidstep", scenic_spot="雷峰塔")
    fake = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x")

    def _run_s4(*_args, **_kwargs):
        # 模拟 s4_pages.run 内部 cancel_check 命中、提前收尾:留下取消标记(不消费)再返回
        api._CANCELLED.add("cancelmidstep")
        return p

    with patch("shanhai.api.resolve_settings", return_value=fake), \
         patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s4_pages") as s4:
        s4.run.side_effect = _run_s4
        api._run_step("cancelmidstep", "s4", runtime_config.AppConfig())

    assert p.status["pipeline"] == "cancelled"
    assert "cancelmidstep" not in api._CANCELLED   # 标记已被消费,不残留污染下次重跑


def test_run_step_cascades_clears_downstream_status(tmp_path: Path):
    # 联动诚实化:重跑上游 s4 使其下游 s5/s6 产物过期,须级联清掉下游 status 键(含计时键),
    # 避免残留"假完成"标记;本环节 s4 自身与其上游不被级联清除,output 因上游重跑清空。
    # ⚠️ mock 必须真的写文件:空跑守卫判定"本轮无产出"时会跳过级联(什么都没重做、下游就没过期)。
    from unittest.mock import MagicMock

    from shanhai.config import Settings
    p = Project(project_id="cascadeId", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    p.status = {"s4": "done", "s5": "done", "s5_elapsed_s": "2.0",
                "s5_finished_at": "2020-01-01T00:00:00+00:00", "s6": "done"}
    fake = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x")
    with patch("shanhai.api.resolve_settings", return_value=fake), \
         patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s4_pages") as s4:
        def _run_s4(*_args, **_kwargs):
            p.status["s4"] = "done"   # 模拟真实 s4_pages.run() 跑完后写回终态(自身状态由 run() 负责)
            (tmp_path / "page_01.png").write_bytes(b"png")   # 真的产出文件,指纹随之变化
            return p
        s4.run.side_effect = _run_s4
        api._run_step("cascadeId", "s4", runtime_config.AppConfig())
    assert "s5" not in p.status and "s6" not in p.status   # 下游被级联清除
    assert "s5_elapsed_s" not in p.status                  # 下游计时键一并清除
    assert "s5_finished_at" not in p.status                # 三个计时键同进同退,不留孤儿
    assert p.status["s4"] == "done"                        # 本环节自身不被级联清除
    assert p.output == {}                                  # 上游重跑,旧成片失效


def test_run_step_clears_own_stale_status_before_running():
    # 修复:重跑期间必须先清掉本环节自己陈旧的终态(如上次成功的 done),否则前端
    # currentIdx(只认非 done/非 partial 为"当前步")判定错位——正在重跑的这一格
    # 不会显示动感。断言 s3_characters.run 真正被调用时,status["s3"] 已经不是旧的 "done"。
    from unittest.mock import MagicMock

    from shanhai.config import Settings
    p = Project(project_id="staleId", scenic_spot="雷峰塔")
    p.status = {"s3": "done"}
    fake = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x")
    seen: dict = {}

    def _capture_run(*_args, **_kwargs):
        seen["s3_during_run"] = p.status.get("s3")
        p.status["s3"] = "done"   # 模拟真实 s3_characters.run() 跑完后写回终态
        return p

    with patch("shanhai.api.resolve_settings", return_value=fake), \
         patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s3_characters") as s3:
        s3.run.side_effect = _capture_run
        api._run_step("staleId", "s3", runtime_config.AppConfig())
    assert seen["s3_during_run"] != "done"   # 执行期间已清空旧终态,而非等跑完才更新
    assert p.status["s3"] == "done"          # 跑完后 s3.run 的返回值(仍是 done)照常生效


def test_run_step_error_preserves_disk_storyboard(tmp_path, monkeypatch):
    # 步骤半途抛错(如 s2 先赋值 storyboard 再校验失败)时,异常兜底不能把半损坏的内存态落盘,
    # 否则会把磁盘上完整的 storyboard 清空、20 页产物引用永久丢失。须重载磁盘干净快照写 error。
    from unittest.mock import MagicMock

    from shanhai.config import Settings
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = Project(project_id="s2fail", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page(index=i) for i in range(1, 21)]   # 20 页完整
    p.status = {"pipeline": "partial"}
    store.save(p)                                          # 落盘干净快照

    def _corrupt_then_raise(proj, _llm):
        proj.storyboard = []                              # 模拟 s2 校验抛错前已清空内存 storyboard
        raise ValueError("分镜为空,S2 未产出任何页")

    fake = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x")
    with patch("shanhai.api.resolve_settings", return_value=fake), \
         patch("shanhai.api._clients", return_value=(MagicMock(),) * 4), \
         patch("shanhai.api.s2_storyboard") as s2:
        s2.run.side_effect = _corrupt_then_raise
        api._run_step("s2fail", "s2", runtime_config.AppConfig())

    reloaded = store.load("s2fail")
    assert len(reloaded.storyboard) == 20                 # 磁盘 storyboard 未被半损坏内存态清空
    assert reloaded.status["pipeline"].startswith("error:")


def test_image_concurrency_serial_for_local_backend():
    # 本地 shim(127.0.0.1/localhost)背后是团队共用的单张 GPU,并发只会互相拖慢/冲突。
    # image_endpoint 优先取 image_base_url,必须显式传它(而不是只传通用 base_url)——
    # 否则会被运行机器 os.environ 里已加载的真实 SHANHAI_IMAGE_BASE_URL(如 DGX 的
    # 本地 ComfyUI 地址)悄悄接管,测试结果随部署环境漂移而非只测传入值本身。
    from shanhai.config import Settings
    s = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x",
                 image_base_url="http://127.0.0.1:8091/v1")
    assert api.image_concurrency(s) == 1
    s2 = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x",
                  image_base_url="http://localhost:8091/v1")
    assert api.image_concurrency(s2) == 1


def test_image_concurrency_parallel_for_remote_backend():
    from shanhai.config import Settings
    from shanhai.runtime_config import REMOTE_IMAGE_CONCURRENCY
    s = Settings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x",
                 image_base_url="https://api.tu-zi.com/v1")
    assert api.image_concurrency(s) == REMOTE_IMAGE_CONCURRENCY


def test_get_queue_reflects_jobs_owner_and_spot():
    p1 = Project(project_id="qid1", scenic_spot="雷峰塔", owner="alice")
    p1.status["pipeline"] = "running"
    p2 = Project(project_id="qid2", scenic_spot="黄鹤楼", owner="bob")
    p2.status["pipeline"] = "queued"
    projects = {"qid1": p1, "qid2": p2}
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f1, f2 = Future(), Future()
    api._JOBS["qid1"] = f1
    api._JOBS["qid2"] = f2
    try:
        with patch("shanhai.api.store.load", side_effect=lambda pid: projects[pid]):
            r = client.get("/api/queue")
        assert r.status_code == 200
        items = {it["project_id"]: it for it in r.json()}
        assert items["qid1"] == {"project_id": "qid1", "owner": "alice",
                                 "scenic_spot": "雷峰塔", "pipeline": "running"}
        assert items["qid2"] == {"project_id": "qid2", "owner": "bob",
                                 "scenic_spot": "黄鹤楼", "pipeline": "queued"}
    finally:
        f1.set_result(None)
        f2.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


# ---------- 编辑端点 ----------

def test_patch_cell_clears_audio_on_caption_change():
    p = Project(project_id="editid", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=[], caption="旧文案", emotion="宁静",
                                   image="pages/page_01.png", audio="audio/page_01.mp3",
                                   duration_ms=3200, status="confirmed")]
    with patch("shanhai.api.store.load", return_value=p), patch("shanhai.api.store.save"):
        r = client.patch("/api/projects/editid/cells/1", json={"caption": "新文案"})
    assert r.status_code == 200
    assert r.json()["pages"][0]["caption"] == "新文案"
    assert r.json()["pages"][0]["audio"] is None          # caption 变更级联清 audio
    assert r.json()["pages"][0]["image"] is not None       # image/status 不受 caption 影响


def test_patch_cell_blocked_in_readonly(monkeypatch):
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.patch("/api/projects/anyid/cells/1", json={"caption": "x"})
    assert r.status_code == 403


def test_patch_cell_blocked_when_job_pending():
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["pendingid"] = f
    try:
        r = client.patch("/api/projects/pendingid/cells/1", json={"caption": "x"})
        assert r.status_code == 409                       # 该项目有未完成作业,拒绝并发编辑
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_patch_cell_missing_project_404():
    r = client.patch("/api/projects/does_not_exist_xyz/cells/1", json={"caption": "x"})
    assert r.status_code == 404


def test_patch_cell_rejects_non_owner():
    # 编辑权限与取消权限同标准:仅项目所有者可编辑(_login_override 恒为 testuser)。
    p = Project(project_id="notmineid", scenic_spot="雷峰塔", owner="someoneelse")
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    with patch("shanhai.api.store.load", return_value=p):
        r = client.patch("/api/projects/notmineid/cells/1", json={"caption": "x"})
    assert r.status_code == 403


def test_delete_cell_rejects_non_owner():
    p = Project(project_id="notmineid2", scenic_spot="雷峰塔", owner="someoneelse")
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    with patch("shanhai.api.store.load", return_value=p):
        r = client.delete("/api/projects/notmineid2/cells/1")
    assert r.status_code == 403


def test_patch_cell_allows_legacy_project_without_owner():
    # 历史项目 owner 为空字符串:视为无主,不做归属限制(不能因加固而锁死存量数据)。
    p = Project(project_id="legacyid", scenic_spot="雷峰塔")
    assert p.owner == ""
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    with patch("shanhai.api.store.load", return_value=p), patch("shanhai.api.store.save"):
        r = client.patch("/api/projects/legacyid/cells/1", json={"caption": "新"})
    assert r.status_code == 200


def test_reorder_rejects_non_permutation():
    p = Project(project_id="reorderid", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                       caption="c1", emotion="宁静"),
        StoryboardCell(index=2, scene_ref="", visual_desc="b", characters=[],
                       caption="c2", emotion="宁静"),
    ]
    with patch("shanhai.api.store.load", return_value=p), patch("shanhai.api.store.save"):
        r = client.post("/api/projects/reorderid/cells/reorder", json={"order": [1, 1]})
    assert r.status_code == 400                           # order 不是全排列


def test_run_step_rejects_unknown_name():
    r = client.post("/api/projects/anyid/steps/s9")
    assert r.status_code == 400


def test_run_step_rejects_when_job_pending():
    # A4:同项目已有未完成作业时 run_step 拒绝重复提交(_editable 与锁内复检双重保障)。
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["runbusy"] = f
    try:
        r = client.post("/api/projects/runbusy/steps/s6")
        assert r.status_code == 409
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_run_step_rejects_non_owner():
    p = Project(project_id="notminestep", scenic_spot="雷峰塔", owner="someoneelse")
    with patch("shanhai.api.store.load", return_value=p):
        r = client.post("/api/projects/notminestep/steps/s6")
    assert r.status_code == 403


@patch("shanhai.api._run_step")           # 不真跑单步
@patch("shanhai.api.Settings")            # 不读 .env / 建真实客户端
def test_run_step_queues_job(_settings, mock_run_step):
    p = Project(project_id="stepid", scenic_spot="雷峰塔")
    # store.save 必须一并 mock——漏了这个之前会把 queued 状态真写进仓库根 projects/stepid/,
    # 每次跑测试都污染真实数据目录(2026-07-14 实测:DGX 部署前多次撞见这个残留项目)。
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"):
        r = client.post("/api/projects/stepid/steps/s6")
    assert r.status_code == 202
    assert r.json() == {"queued": True}
    api._JOBS["stepid"].result(timeout=2)   # 等后台线程跑完(已 mock,立即返回)
    mock_run_step.assert_called_once()


@patch("shanhai.api._run_step")
@patch("shanhai.api.Settings")
def test_run_step_marks_queued_before_submit(_settings, _run):
    # 202 后立刻轮询不能读到上一次遗留的 done —— 提交前须先落盘 queued
    p = Project(project_id="qid", scenic_spot="雷峰塔")
    p.status["pipeline"] = "done"           # 上一步遗留状态
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save") as mock_save:
        r = client.post("/api/projects/qid/steps/s6")
    assert r.status_code == 202
    assert p.status["pipeline"] == "queued"
    mock_save.assert_called()               # queued 已落盘
    api._JOBS["qid"].result(timeout=2)


@patch("shanhai.api._run_step")
@patch("shanhai.api.Settings")
def test_run_step_persists_fresh_reload_not_stale_snapshot(_settings, _run):
    # finding-1 回归:run_step 写 queued 必须在 per-project 锁内重新 store.load 最新快照,
    # 而非复用 _editable 锁外拿到的陈旧 p —— 否则会覆盖此间发生的并发编辑(丢更新)。
    stale = Project(project_id="freshid", scenic_spot="旧")   # _editable 锁外拿到的陈旧快照
    fresh = Project(project_id="freshid", scenic_spot="新")   # 锁内重载应拿到的最新快照
    loads = [stale, fresh]                                    # 第 1 次 _editable、第 2 次锁内重载
    saved = {}
    with patch("shanhai.api.store.load", side_effect=lambda *a, **k: loads.pop(0)), \
         patch("shanhai.api.store.save", side_effect=lambda p, **k: saved.update(obj=p)):
        r = client.post("/api/projects/freshid/steps/s6")
    assert r.status_code == 202
    assert saved["obj"] is fresh             # 落盘的是锁内重载的 fresh,不是陈旧的 stale
    assert fresh.status["pipeline"] == "queued"
    assert stale.status.get("pipeline") != "queued"   # 陈旧快照没被当作落盘对象


def test_create_cell_rejects_oversized_visual_desc():
    # A7:visual_desc 无上限会喂给下游成图 prompt。
    r = client.post("/api/projects/anyid/cells", json={
        "after_index": 0, "caption": "c", "visual_desc": "长" * 2001,
    })
    assert r.status_code == 422


def test_create_cell_rejects_too_many_characters():
    # A7:characters 无上限,超限拒绝(在 create_cell 端点挡)。
    r = client.post("/api/projects/anyid/cells", json={
        "after_index": 0, "caption": "c", "visual_desc": "v",
        "characters": [f"c{i}" for i in range(51)],
    })
    assert r.status_code == 400


# ---------- 静态文件 ----------

def test_files_hides_project_json():
    # FP8:project.json(含用户 story、legend sources、角色 feature_prompt 等内部态)不经
    # /files 暴露。在 StaticFiles 规范化 path 之后按 basename 拦截,故各绕过变体都应 404,
    # 而其它产物正常托管。写真实 projects/ 目录再验证(否则文件不存在测不出"拦截 vs 未命中")。
    import shutil
    d = store.DEFAULT_ROOT / "fp8test"
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text('{"secret": "内部 prompt"}', encoding="utf-8")
    (d / "art.txt").write_text("ok", encoding="utf-8")     # 对照:非 project.json 正常托管
    try:
        assert client.get("/files/fp8test/project.json").status_code == 404       # 规范路径
        assert client.get("/files/fp8test/project.json/").status_code == 404      # 尾随斜杠绕过
        assert client.get("/files/fp8test/PROJECT.JSON").status_code == 404        # 大小写绕过
        assert client.head("/files/fp8test/project.json").status_code == 404       # HEAD 绕过
        assert client.get("/files/fp8test/art.txt").status_code == 200             # 其它产物不受影响
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_patch_cell_rejects_oversized_fields():
    # A7:编辑路径(PATCH)与插入路径同等挡超大 visual_desc/characters(否则经编辑绕过上限喂下游)。
    assert client.patch("/api/projects/anyid/cells/1",
                        json={"visual_desc": "x" * 2001}).status_code == 422   # visual_desc > 2000
    assert client.patch("/api/projects/anyid/cells/1",
                        json={"characters": ["c"] * 51}).status_code == 400     # characters > 50


# ---------- 端点/模型配置(全局默认 + 按环节覆盖) ----------

@pytest.fixture
def _isolated_config_path(tmp_path, monkeypatch):
    """把配置路径指到 tmp_path,隔离测试对真实 config.json 的读写。
    _config_path() 延迟读 SHANHAI_CONFIG_PATH,故设环境变量即可。"""
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "config.json"))


def test_get_config_never_leaks_plaintext_api_key(_isolated_config_path):
    real_key = os.environ.get("SHANHAI_API_KEY", "")
    r = client.get("/api/config")
    assert r.status_code == 200
    assert real_key and real_key not in r.text          # 明文密钥绝不出现在响应体中
    assert r.json()["defaults"]["api_key"] is True       # .env 已配置 → defaults 回 true(非明文)


def test_get_config_masks_configured_secret(_isolated_config_path):
    runtime_config.save_overrides(runtime_config.AppConfig(
        global_=runtime_config.ConfigOverride(llm_api_key="sk-secret-value"),
    ))
    r = client.get("/api/config")
    assert "sk-secret-value" not in r.text
    assert r.json()["global"]["llm_api_key"] == runtime_config.MASK   # 已配置密钥呈掩码


def test_put_config_blocked_in_readonly(monkeypatch, _isolated_config_path):
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.put("/api/config", json={"global": {}, "stages": {}})
    assert r.status_code == 403


def test_put_config_rejects_illegal_llm_provider(_isolated_config_path):
    r = client.put("/api/config", json={"global": {"llm_provider": "bogus"}, "stages": {}})
    assert r.status_code == 422


def test_put_config_rejects_unknown_stage(_isolated_config_path):
    r = client.put("/api/config", json={"global": {}, "stages": {"s9": {}}})
    assert r.status_code == 400


def test_put_config_sentinel_semantics(_isolated_config_path):
    # 新值:更新
    r1 = client.put("/api/config", json={"global": {"llm_api_key": "sk-first"}, "stages": {}})
    assert r1.status_code == 200
    assert r1.json()["global"]["llm_api_key"] == runtime_config.MASK

    # 哨兵 __UNCHANGED__:保持已存值不变(顺带更新非密钥字段)
    r2 = client.put("/api/config", json={
        "global": {"llm_api_key": runtime_config.SENTINEL, "llm_model": "m2"}, "stages": {},
    })
    assert r2.status_code == 200
    assert r2.json()["global"]["llm_api_key"] == runtime_config.MASK   # 仍已配置
    assert r2.json()["global"]["llm_model"] == "m2"
    assert runtime_config.load_overrides().global_.llm_api_key == "sk-first"   # 底层值未变

    # 空字符串:清除继承
    r3 = client.put("/api/config", json={"global": {"llm_api_key": ""}, "stages": {}})
    assert r3.status_code == 200
    assert r3.json()["global"]["llm_api_key"] is None
    assert runtime_config.load_overrides().global_.llm_api_key is None

    # 再次给新值:更新
    r4 = client.put("/api/config", json={"global": {"llm_api_key": "sk-second"}, "stages": {}})
    assert r4.status_code == 200
    assert runtime_config.load_overrides().global_.llm_api_key == "sk-second"


def test_get_config_readable_in_readonly(monkeypatch, _isolated_config_path):
    """只读模式仍可读配置(仅 PUT 被 403),readonly 标志置真。"""
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["readonly"] is True


def test_put_config_rejects_extra_field(_isolated_config_path):
    """越权/多余字段(如 readonly)被 extra=forbid 拒绝。"""
    r = client.put("/api/config", json={"global": {"readonly": True}, "stages": {}})
    assert r.status_code == 422


def test_get_config_masks_stage_secret(_isolated_config_path):
    """环节层密钥同样脱敏(不只 global)。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        stages={"s5": runtime_config.ConfigOverride(tts_api_key="sk-tts")}))
    r = client.get("/api/config")
    assert "sk-tts" not in r.text
    assert r.json()["stages"]["s5"]["tts_api_key"] == runtime_config.MASK


def test_put_preserves_unsent_global_fields(_isolated_config_path):
    """只发一个 global 字段,其余(尤其前端不渲染的 base_url/api_key)保留,不被静默抹掉。"""
    runtime_config.save_overrides(runtime_config.AppConfig(global_=runtime_config.ConfigOverride(
        base_url="https://root", api_key="sk-root", llm_model="m1")))
    r = client.put("/api/config", json={"global": {"llm_model": "m2"}, "stages": {}})
    assert r.status_code == 200
    stored = runtime_config.load_overrides().global_
    assert stored.llm_model == "m2"
    assert stored.base_url == "https://root"   # 未随请求发送 → 保留(不被抹掉)
    assert stored.api_key == "sk-root"


def test_put_preserves_unsent_stages(_isolated_config_path):
    """部分 PUT 只带 s2,不误删其它已存环节(s5)。"""
    runtime_config.save_overrides(runtime_config.AppConfig(stages={
        "s2": runtime_config.ConfigOverride(llm_model="a"),
        "s5": runtime_config.ConfigOverride(tts_model="b")}))
    r = client.put("/api/config", json={"global": {}, "stages": {"s2": {"llm_model": "a2"}}})
    assert r.status_code == 200
    stages = runtime_config.load_overrides().stages
    assert stages["s2"].llm_model == "a2"
    assert stages["s5"].tts_model == "b"       # 未在 PUT 出现 → 保留


def test_put_empty_stage_is_pruned(_isolated_config_path):
    """清空某环节的全部字段 → 该环节覆盖被删除。"""
    runtime_config.save_overrides(runtime_config.AppConfig(stages={
        "s2": runtime_config.ConfigOverride(llm_model="a")}))
    r = client.put("/api/config", json={"global": {}, "stages": {"s2": {"llm_model": ""}}})
    assert r.status_code == 200
    assert "s2" not in runtime_config.load_overrides().stages


def test_put_mask_echo_keeps_existing_secret(_isolated_config_path):
    """客户端把 GET 的掩码原样回填,不应被当成新密钥写入。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        global_=runtime_config.ConfigOverride(llm_api_key="sk-real")))
    r = client.put("/api/config", json={"global": {"llm_api_key": runtime_config.MASK}, "stages": {}})
    assert r.status_code == 200
    assert runtime_config.load_overrides().global_.llm_api_key == "sk-real"


def test_files_blocks_runtime_config_basename(monkeypatch):
    """/files 静态托管按运行时 _config_path().name 动态拦截配置文件:文件存在但仍 404 且不泄露内容。"""
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(store.DEFAULT_ROOT / "secrets.json"))
    served = store.DEFAULT_ROOT / "secrets.json"
    served.parent.mkdir(exist_ok=True)
    served.write_text("PLAINTEXT_KEY", encoding="utf-8")
    try:
        r = client.get("/files/secrets.json")
        assert r.status_code == 404               # 文件存在但被拦 → 证明是 basename 拦截而非"不存在"
        assert "PLAINTEXT_KEY" not in r.text
    finally:
        served.unlink(missing_ok=True)


def test_create_project_validates_settings_before_creating(monkeypatch):
    """.env 基线无效(Settings 构造失败)时急切失败,不创建孤儿 queued 项目。"""
    monkeypatch.setattr(api, "Settings", lambda: (_ for _ in ()).throw(RuntimeError("坏 .env")))
    created: list = []
    monkeypatch.setattr(store, "create_project", lambda *a, **k: created.append(1))
    with pytest.raises(RuntimeError):
        client.post("/api/projects", json={"scenic_spot": "测试景区"})
    assert created == []   # Settings 校验先于建项目 → 未落盘任何项目


@patch("shanhai.api.Settings")
def test_run_step_s6_rerun_is_never_treated_as_noop(_settings, tmp_path: Path):
    """S6 重跑必须记下真实耗时——这是空跑守卫第一版最严重的漏判(三路对抗审计一致命中)。

    s6_compose.run 完全不幂等:每次都重新排版、逐页 ffmpeg 编码、xfade 拼接、封字幕,
    几分钟起步,但写回的永远是同名的 output["mp4"]。第一版守卫拿 tuple(sorted(p.output))
    当指纹,开工前 == 收工后,于是每一次 S6 重跑的耗时都被吞掉,用户等了十分钟、
    界面上数字纹丝不动。改成按产物文件指纹判定后,重编码必然改动文件、守卫不再误命中。
    """
    from unittest.mock import MagicMock
    p = Project(project_id="s6rerunid", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    p.output = {"mp4": "output/final.mp4", "zip": "output/pages.zip", "pdf": "output/book.pdf"}
    p.status = {"s6_started_at": "2020-01-01T00:00:00+00:00",
                "s6_finished_at": "2020-01-01T00:02:00+00:00",
                "s6_elapsed_s": "120.0"}
    out = tmp_path / "output"
    out.mkdir()
    (out / "final.mp4").write_bytes(b"old")

    def _reencode(*_args, **_kwargs):
        (out / "final.mp4").write_bytes(b"newly encoded")   # 同名文件被重写,output 键集合不变
        return p

    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s6_compose") as s6:
        s6.run.side_effect = _reencode
        api._run_step("s6rerunid", "s6", runtime_config.AppConfig())

    assert p.status["s6_elapsed_s"] != "120.0"                          # 本轮耗时被如实记下
    assert p.status["s6_started_at"] != "2020-01-01T00:00:00+00:00"
    assert p.status["s6_finished_at"] != "2020-01-01T00:02:00+00:00"


# ---------- 角色参考图上传/删除 ----------

def _jpeg_bytes(w=800, h=1200, color=(200, 50, 50)) -> bytes:
    from io import BytesIO

    from PIL import Image
    im = Image.new("RGB", (w, h), color)
    buf = BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def _ref_project(owner: str = "") -> Project:
    p = Project(project_id="refid", scenic_spot="雷峰塔", owner=owner)
    p.script = Script(title="t", theme="th", acts=[], characters=[
        CharacterCard(name="白娘子", role="蛇仙", personality="p", appearance="a",
                     turnaround_image="characters/白娘子.png", locked=True)])
    p.status = {"pipeline": "done", "s3": "done"}
    return p


def test_upload_reference_success(tmp_path: Path):
    p = _ref_project()
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r = client.post("/api/projects/refid/characters/白娘子/reference",
                        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["characters"][0]["reference_image"]                # 非空 URL
    assert body["characters"][0]["image"] is not None               # 未清 turnaround_image
    assert "s3" not in body["status"]                                # mark_character_redraw 令下游失效
    assert p.script.characters[0].locked is False                   # 解锁待重绘
    saved = list((tmp_path / "characters" / "refs").iterdir())
    assert len(saved) == 1
    from PIL import Image
    im = Image.open(saved[0])
    assert im.format == "PNG"                                        # 真的重新编码成 PNG 落盘
    assert max(im.size) <= 768


def test_upload_reference_rejects_oversize(tmp_path: Path):
    p = _ref_project()
    data = b"\x00" * (9 * 1024 * 1024)
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r = client.post("/api/projects/refid/characters/白娘子/reference",
                        files={"file": ("big.jpg", data, "image/jpeg")})
    assert r.status_code == 413
    refs = tmp_path / "characters" / "refs"
    assert not refs.exists() or not any(refs.iterdir())              # 没有半成品落盘


def test_upload_reference_rejects_non_image_despite_content_type(tmp_path: Path):
    p = _ref_project()
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r = client.post("/api/projects/refid/characters/白娘子/reference",
                        files={"file": ("fake.png", b"not an image", "image/png")})
    assert r.status_code == 400                                      # 不信 content_type,只信解码结果


def test_upload_reference_unknown_character_404(tmp_path: Path):
    p = _ref_project()
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r = client.post("/api/projects/refid/characters/不存在的角色/reference",
                        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 404
    assert not (tmp_path / "characters").exists()                    # 角色名不存在,未写任何文件


def test_upload_reference_blocked_in_readonly(monkeypatch):
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.post("/api/projects/refid/characters/白娘子/reference",
                    files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 403


def test_upload_reference_rejects_non_owner(tmp_path: Path):
    p = _ref_project(owner="someoneelse")
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r = client.post("/api/projects/refid/characters/白娘子/reference",
                        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 403


def test_upload_reference_rejects_when_job_pending():
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["refid"] = f
    try:
        r = client.post("/api/projects/refid/characters/白娘子/reference",
                        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")})
        assert r.status_code == 409
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def test_delete_reference_removes_file_and_is_idempotent(tmp_path: Path):
    p = _ref_project()
    ref_path = tmp_path / "characters" / "refs" / "ref_x.png"
    ref_path.parent.mkdir(parents=True)
    ref_path.write_bytes(b"png")
    p.script.characters[0].reference_image = "characters/refs/ref_x.png"
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r1 = client.delete("/api/projects/refid/characters/白娘子/reference")
        assert r1.status_code == 200
        assert not ref_path.exists()
        assert p.script.characters[0].reference_image == ""
        r2 = client.delete("/api/projects/refid/characters/白娘子/reference")  # 再删一次仍 200
    assert r2.status_code == 200


def test_delete_reference_unknown_character_404(tmp_path: Path):
    p = _ref_project()
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path):
        r = client.delete("/api/projects/refid/characters/不存在的角色/reference")
    assert r.status_code == 404


@patch("shanhai.api.Settings")
def test_s2_rerun_keeps_s3_and_purges_stale_artifacts(_settings, tmp_path: Path):
    """重跑分镜:S3 保留、S4~S6 与多语种作废、旧的逐页产物与成片被删、三视图留着。

    S3 依赖的是 project.script,而 S2 换的是 storyboard——剧本没动,三视图仍然有效。
    按 _STEP_NAMES 位置级联会把 S3 一起作废,而它的图还在、locked 还是 True,用户真去点
    S3 时会被空跑守卫判成没干活,那格的历史耗时就此永久丢失。
    """
    from unittest.mock import MagicMock
    from shanhai.config import Settings as RealSettings
    p = Project(project_id="s2cascade", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    p.output = {"mp4": "projects/s2cascade/output/final.mp4"}
    p.status = {"s3": "done", "s3_elapsed_s": "120.0", "s3_finished_at": "2020-01-01T00:00:00+00:00",
                "s4": "done", "s5": "done", "s6": "done",
                "s5t_en": "done", "s5_en": "done", "track_en": "done",
                "track_en_elapsed_s": "489.9"}
    for sub, name in (("pages", "page_01.png"), ("audio", "page_01.mp3"),
                      ("output", "final.mp4"), ("output", "final.en.vtt"),
                      ("characters", "白娘子.png")):
        (tmp_path / sub).mkdir(exist_ok=True)
        (tmp_path / sub / name).write_bytes(b"x")

    fake = RealSettings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x")
    with patch("shanhai.api.resolve_settings", return_value=fake), \
         patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s2_storyboard") as s2:
        def _run_s2(*_a, **_k):
            p.storyboard = [_imaged_page()]   # 整体替换,模拟真实行为
            p.status["s2"] = "done"
            return p
        s2.run.side_effect = _run_s2
        api._run_step("s2cascade", "s2", runtime_config.AppConfig())

    assert p.status["s3"] == "done"                     # S3 不被误伤
    assert p.status["s3_elapsed_s"] == "120.0"          # 它的历史耗时也留着
    for k in ("s4", "s5", "s6", "s5t_en", "s5_en", "track_en", "track_en_elapsed_s"):
        assert k not in p.status, f"{k} 应被作废"
    assert p.output == {}
    # 旧的逐页产物与成片(含字幕)已删,角色三视图保留
    assert not (tmp_path / "pages" / "page_01.png").exists()
    assert not (tmp_path / "audio" / "page_01.mp3").exists()
    assert not (tmp_path / "output" / "final.en.vtt").exists()
    assert (tmp_path / "characters" / "白娘子.png").exists()


@patch("shanhai.api.Settings")
def test_s2_failure_does_not_delete_anything(_settings, tmp_path: Path):
    """S2 抛异常时**一个文件都不能删**。

    s2_storyboard.run 会在 LLM 返回空分镜时 raise;那一刻旧产物还是用户仅有的东西,
    先删后跑等于让一次失败的重生成把成片也赔进去。这条最容易写反,故单独守着。
    """
    from unittest.mock import MagicMock
    from shanhai.config import Settings as RealSettings
    p = Project(project_id="s2fail2", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    p.output = {"mp4": "projects/s2fail2/output/final.mp4"}
    for sub, name in (("pages", "page_01.png"), ("output", "final.mp4")):
        (tmp_path / sub).mkdir(exist_ok=True)
        (tmp_path / sub / name).write_bytes(b"x")

    fake = RealSettings(_env_file=None, base_url="https://placeholder.invalid/v1", api_key="x")
    with patch("shanhai.api.resolve_settings", return_value=fake), \
         patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._save_error"), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s2_storyboard") as s2:
        s2.run.side_effect = ValueError("分镜为空,S2 未产出任何页")
        api._run_step("s2fail2", "s2", runtime_config.AppConfig())

    assert (tmp_path / "pages" / "page_01.png").exists()
    assert (tmp_path / "output" / "final.mp4").exists()


def test_invalidates_table_covers_every_step():
    """依赖表必须与 _STEP_NAMES 同步,否则将来加环节时会静默漏配级联。"""
    assert set(api._INVALIDATES) == set(api._STEP_NAMES)
    for name, downstream in api._INVALIDATES.items():
        assert name not in downstream            # 不作废自己
        for d in downstream:
            assert d in api._STEP_NAMES


@patch("shanhai.api.Settings")
def test_run_step_skips_timing_on_first_noop_without_prior_record(_settings, tmp_path: Path):
    # 「石坊温热」s3=0.0秒 的回归测试:计时键被级联清空之后的**首次**空跑,此前没有耗时记录,
    # 老守卫的 `elapsed_s in status` 前置条件因此不成立、被整个绕过,把 0.0 写了进去
    # (4 个三视图早就在盘上、一个没重画,真实 0.0016 秒),前端显示「0秒」、总耗时少算一环节。
    # 守卫只看指纹即可:没产出任何文件的运行,不管此前有没有记录都不该落一个假耗时。
    from unittest.mock import MagicMock
    p = Project(project_id="noopFirstId", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    p.status = {}                                   # 计时键已被清空,此前无任何记录
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "page_01.mp3").write_bytes(b"mp3")   # 产物已在盘上,本轮不会被重写
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api.store.project_dir", return_value=tmp_path), \
         patch("shanhai.api._clients",
               return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s5_audio") as s5:
        s5.run.return_value = p                     # 原样返回:全部子项幂等跳过,无新产物
        api._run_step("noopFirstId", "s5", runtime_config.AppConfig())

    assert "s5_elapsed_s" not in p.status           # 不写假的 0.0 秒,这一行留空
    assert "s5_started_at" not in p.status
    assert "s5_finished_at" not in p.status
    assert "s5_running_since" not in p.status       # 进行中标记仍要收干净


def test_version_endpoint_is_unauthenticated():
    # /api/version 刻意免鉴权:部署脚本靠 curl 它自证"线上正在跑的进程"确实是刚传上去的那版
    # (原先 ops-dgx.md 的验证只有 curl -w '%{http_code}',证明不了任何事),
    # 前端也要在登录页之前就拿到它。将来谁顺手给它加了 Depends(current_user),这条会炸。
    api.app.dependency_overrides.clear()          # 去掉本文件 autouse 的"已登录"覆盖
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"build", "sha", "dirty", "stamped_at"}
    assert isinstance(body["build"], int)


def test_version_route_not_swallowed_by_static_catch_all():
    # api.py 末尾把 web/dist 挂在 "/" 上做 SPA 兜底(html=True)。任何声明在它**之后**的路由
    # 永远命中不到,只会拿到 index.html。这条守住 /api/version 的声明位置。
    r = client.get("/api/version")
    assert r.headers["content-type"].startswith("application/json")
    assert "<!doctype html" not in r.text.lower()
