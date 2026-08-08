import json
import os
import subprocess
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shanhai import api, auth, runtime_config, store
from shanhai.schema import (CharacterCard, Legend, LocalizedTrack, Project, Script,
                            StoryboardCell)

client = TestClient(api.app)


# 测试的登录身份。生产建作品必设 owner(api.create_project),而 store.create_project /
# Project() 都不设——夹具若不补,拿到的是「无主作品」,自 2026-08-06 收紧后普通用户对它
# 一律 403,测的就不再是被测功能而是归属判据了。
TEST_USER = "testuser"


@pytest.fixture(autouse=True)
def _login_override():
    """现有端点已全部要求登录(Depends(current_user)),否则本文件测试会因 401 全挂。
    用依赖覆盖让测试在「已登录 testuser」语境下跑,不必真的走 cookie 登录流程
    (真实 cookie 登录流程由 tests/test_auth.py 覆盖)。"""
    api.app.dependency_overrides[api.current_user] = lambda: TEST_USER
    yield
    api.app.dependency_overrides.clear()


def _cookie_client(tmp_path, monkeypatch, username: str = "testuser") -> TestClient:
    """带**真实 session cookie** 的客户端。

    /files 挂载不是 FastAPI 路由,拿不到 Depends(current_user),它直接读 scope["session"],
    因此上面那个 _login_override(依赖覆盖)对它完全不可见——测 /files 的登录闸只能真登录。
    不为此在实现里加测试专用的间接层。"""
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    auth.add_user(username, "pw-" + username)
    c = TestClient(api.app)
    r = c.post("/api/login", json={"username": username, "password": "pw-" + username})
    assert r.status_code == 200, f"测试自身的登录夹具失效: {r.status_code} {r.text}"
    return c


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


def test_meta_exposes_step_cascade_table():
    """「补全重生成」的弹窗要如实告诉用户点下去会跑哪几步,名单必须来自后端这张表。

    ⚠️ 断言直接对着 _INVALIDATES 本身,而不是在这里抄一份期望值——抄一份就和"前端自己
    硬编码一份"是同一个毛病:表改了测试照样绿,界面却在向用户描述一个不存在的行为。"""
    j = client.get("/api/meta").json()
    assert j["step_cascade"] == {k: list(v) for k, v in api._INVALIDATES.items()}


def test_meta_step_cascade_covers_every_runnable_step():
    """级联表的键集合必须与可单步重跑的步骤完全一致。

    少了 → 前端 cascadeOf 取不到,那一步静默退回单出口确认框(功能悄悄没了);
    多了 → 弹窗会列出一个 run_step 根本不接受的步骤,点下去 400。"""
    j = client.get("/api/meta").json()
    assert set(j["step_cascade"]) == set(api._STEP_NAMES)


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


def test_create_rejects_sensitive_story():
    # 敏感原文若先落盘再由后台 s0_legend.from_text 拒绝,未过审的文本会永久留在 project.json,
    # 并经 GET /api/projects/{id} 的 story 字段回给任意登录用户。须在落盘前同门校验并 400。
    r = client.post("/api/projects",
                    json={"scenic_spot": "雷峰塔", "story": "从前有座塔。毛泽东的故事。"})
    assert r.status_code == 400
    assert "敏感" in r.json()["detail"]


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
    p = Project(project_id="expid", scenic_spot="雷峰塔", owner=TEST_USER)
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


def test_reconcile_survives_unreadable_project(tmp_path, capsys):
    """一个读不出来的 project.json 不能阻断对账,而且必须被点名——它会永远卡在生成中,
    运维得知道是哪个(用户可以在界面上手动重置它)。"""
    zombie = store.create_project("僵尸", root=tmp_path)
    zombie.status["pipeline"] = "running"
    store.save(zombie, root=tmp_path)
    (tmp_path / "brokenpid").mkdir()
    (tmp_path / "brokenpid" / "project.json").write_text("{not valid json", encoding="utf-8")

    assert api.reconcile_zombie_jobs(tmp_path) == 1        # 坏文件不影响好项目被修
    out = capsys.readouterr().out
    assert "brokenpid" in out and "跳过" in out            # 不再静默


def test_reconcile_survives_schema_drift(tmp_path, capsys):
    """合法 JSON 但非法 Literal 值:这类项目 store.load 永久失败(见 docs/decisions/0003),
    同样只跳过、不阻断。"""
    (tmp_path / "driftpid").mkdir()
    (tmp_path / "driftpid" / "project.json").write_text(
        json.dumps({"project_id": "driftpid", "scenic_spot": "雷峰塔",
                    "status": {"pipeline": "running"},
                    "params": {"audience": "外星人"}}),   # 不在 Literal 枚举里
        encoding="utf-8")
    assert api.reconcile_zombie_jobs(tmp_path) == 0
    assert "driftpid" in capsys.readouterr().out


def test_reconcile_survives_save_failure(tmp_path, capsys):
    """**这条是修复的核心**:此前 store.save 在 try 之外,一个不可写的项目目录就会让异常
    冒出 main()、端口根本不绑;而 systemd Restart=always 没覆盖 StartLimit*,重启几次后
    进 failed 态——一个坏目录 = 全站永久下线。"""
    z = store.create_project("僵尸", root=tmp_path)
    z.status["pipeline"] = "running"
    store.save(z, root=tmp_path)

    with patch("shanhai.api.store.save", side_effect=OSError("No space left on device")):
        n = api.reconcile_zombie_jobs(tmp_path)           # 不抛,不冒到 main()
    assert n == 0                                          # 没修成就不算数
    out = capsys.readouterr().out
    assert z.project_id in out and "写回失败" in out


def test_reconcile_root_is_late_bound(tmp_path, monkeypatch):
    """签名从 root=store.DEFAULT_ROOT 改成 None:早绑定会让 monkeypatch DEFAULT_ROOT 失效,
    而 store.py 的注释记着早绑定曾导致测试往真实 projects/ 里写脏数据。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    z = store.create_project("僵尸", root=tmp_path)
    z.status["pipeline"] = "running"
    store.save(z, root=tmp_path)
    assert api.reconcile_zombie_jobs() == 1                # 不传参也该扫到 tmp_path


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
    assert set(item) == {"project_id", "scenic_spot", "owner", "pipeline", "mp4",
                         "multi_panel", "stalled"}
    assert item["project_id"] == p.project_id
    assert item["scenic_spot"] == "雷峰塔"
    assert item["owner"] == "someone"
    assert item["pipeline"] == "done"
    # 文件不存在故无 ?v= 后缀,与 _mp4_url 对不存在文件的处理一致
    assert item["mp4"] == f"/files/{p.project_id}/output/final.mp4"


def test_list_projects_reports_multi_panel(tmp_path, monkeypatch):
    """列表要能看出这部作品建的时候勾没勾分格排版。老作品的 project.json 里根本没有
    params.multi_panel 这个键(该字段是后加的),取默认 False——与它们当时的实际行为
    一致,不是替旧数据编造结论;更不能因为缺键就让整表失败(与损坏项目同一条不变量)。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    panel = store.create_project("喀纳斯", root=tmp_path)
    panel.params.multi_panel = True
    store.save(panel, root=tmp_path)
    plain = store.create_project("雷峰塔", root=tmp_path)
    store.save(plain, root=tmp_path)
    legacy = tmp_path / "legacypid"           # 历史作品:整个 params 都没有
    legacy.mkdir()
    (legacy / "project.json").write_text(
        '{"project_id": "legacypid", "scenic_spot": "黄鹤楼"}', encoding="utf-8")

    items = {it["project_id"]: it["multi_panel"] for it in client.get("/api/projects").json()}
    assert items[panel.project_id] is True
    assert items[plain.project_id] is False
    assert items["legacypid"] is False        # 缺键 → False,且没把整表拖垮


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


def test_cancel_matches_editable_ownership_rule(tmp_path, monkeypatch):
    """取消权限必须与编辑权限同判据。此前用的是 `p.owner != user`,而 _editable 用的是
    `p.owner and p.owner != user`——历史项目 owner 为空时人人可编辑,却**没有人**能取消
    它的作业,那个作业只能靠重启进程停下,期间一直占着执行槽。两个判据的差异没有任何
    理由,现已共用 _may_edit,不再靠人肉保持一致。

    2026-08-06 起无主作品对普通用户一律 403(见 _may_edit),故这里断言的是 403 而不是
    "放行后落到 400"。锁的仍是同一件事:cancel 与 edit 用同一个判据。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = ""                              # 无主的历史项目
    store.save(p, root=tmp_path)
    assert client.post(f"/api/projects/{p.project_id}/cancel").status_code == 403
    # 同判据的另一半:编辑侧同样拒绝(若两边判据再次分叉,这两条会一起报警)
    assert client.patch(f"/api/projects/{p.project_id}/cells/1",
                        json={"caption": "x"}).status_code == 403


def test_cancel_rejects_other_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone-else"
    store.save(p, root=tmp_path)
    assert client.post(f"/api/projects/{p.project_id}/cancel").status_code == 403


# ---------- 失联作业(磁盘写着生成中,内存里已无人推进)与「重置状态」 ----------

def _stalled_project(tmp_path, monkeypatch, owner: str = "testuser", pipeline: str = "running"):
    """手工制造失联态:磁盘写 running,但**不提交任何作业**——精确复刻用户遇到的现场
    (重启硬杀、或 _save_error 自身失败之后的样子)。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = owner
    p.status["pipeline"] = pipeline
    store.save(p, root=tmp_path)
    return p


def test_stalled_true_when_no_job():
    assert api._stalled("nojobid", "running") is True
    assert api._stalled("nojobid", "queued") is True


def test_stalled_false_while_job_alive():
    f = Future()
    api._JOBS["aliveid"] = f
    try:
        assert api._stalled("aliveid", "running") is False
    finally:
        f.set_result(None)
        api._JOBS.pop("aliveid", None)


def test_stalled_true_when_job_already_done():
    """**不需要重启就能卡死**的那条路径:_save_error 自身失败时线程会正常结束,
    f.done() 立刻为真、条目还滞留在 _JOBS 里(它没有 finally 清理),而磁盘停在 running。"""
    f = Future()
    f.set_result(None)
    api._JOBS["doneid"] = f
    try:
        assert api._stalled("doneid", "running") is True
    finally:
        api._JOBS.pop("doneid", None)


def test_stalled_false_for_terminal_status():
    for s in ("done", "error: x", "cancelled", "partial: y", "pending"):
        assert api._stalled("whatever", s) is False


def test_detail_and_list_expose_stalled_without_persisting_it(tmp_path, monkeypatch):
    """stalled 是"此刻内存里有没有作业"的函数,落盘就会变成过期的谎言,故只出现在响应里。"""
    p = _stalled_project(tmp_path, monkeypatch)
    assert client.get(f"/api/projects/{p.project_id}").json()["stalled"] is True
    assert client.get("/api/projects").json()[0]["stalled"] is True
    on_disk = json.loads((tmp_path / p.project_id / "project.json").read_text(encoding="utf-8"))
    assert "stalled" not in on_disk and "stalled" not in on_disk.get("status", {})


def test_cancel_still_400_when_stalled(tmp_path, monkeypatch):
    """复现用户撞的那堵墙:取消端点对失联作业一律 400(有意为之,注释与本用例一起锁着)。
    这正是「重置」必须单独存在的理由——cancel 永远救不了这种状态。"""
    p = _stalled_project(tmp_path, monkeypatch)
    r = client.post(f"/api/projects/{p.project_id}/cancel")
    assert r.status_code == 400


def test_reset_recovers_stalled_project(tmp_path, monkeypatch):
    p = _stalled_project(tmp_path, monkeypatch)
    r = client.post(f"/api/projects/{p.project_id}/reset")
    assert r.status_code == 200
    assert r.json()["stalled"] is False
    assert store.load(p.project_id, root=tmp_path).status["pipeline"].startswith("error: 已手动重置")


def test_reset_rejects_non_owner(tmp_path, monkeypatch):
    p = _stalled_project(tmp_path, monkeypatch, owner="someone-else")
    assert client.post(f"/api/projects/{p.project_id}/reset").status_code == 403


def test_reset_allows_admin_on_other_owner_project(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    p = _stalled_project(tmp_path, monkeypatch, owner="someone-else")
    assert client.post(f"/api/projects/{p.project_id}/reset").status_code == 200


def test_reset_rejects_terminal_project(tmp_path, monkeypatch):
    """刻意不做成幂等 200:静默成功会掩盖"前端状态陈旧"这类真问题。"""
    p = _stalled_project(tmp_path, monkeypatch, pipeline="done")
    r = client.post(f"/api/projects/{p.project_id}/reset")
    assert r.status_code == 400


def test_reset_refuses_while_job_alive(tmp_path, monkeypatch):
    """有活作业时指回「取消」。重置一个仍在跑的作业会被它醒来后的 _locked_save 盖回去,
    做出来就是个时灵时不灵的按钮。"""
    p = _stalled_project(tmp_path, monkeypatch)
    f = Future()
    api._JOBS[p.project_id] = f
    try:
        r = client.post(f"/api/projects/{p.project_id}/reset")
        assert r.status_code == 409
    finally:
        f.set_result(None)
        api._JOBS.pop(p.project_id, None)


def test_reset_blocked_in_readonly(tmp_path, monkeypatch):
    p = _stalled_project(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "_READONLY", True)
    assert client.post(f"/api/projects/{p.project_id}/reset").status_code == 403


def test_reset_then_step_rerun_works(tmp_path, monkeypatch):
    """重置之后恢复链路真的通:能重新提交单步生成,作品是活的。"""
    p = _stalled_project(tmp_path, monkeypatch)
    assert client.post(f"/api/projects/{p.project_id}/reset").status_code == 200
    with patch("shanhai.api._run_step"), patch("shanhai.api.Settings"):
        r = client.post(f"/api/projects/{p.project_id}/steps/s4")
    assert r.status_code == 202
    api._JOBS.pop(p.project_id, None)


def test_write_config_requires_admin(monkeypatch):
    """配置写入决定全站上游端点:任意登录用户可改的话,把 llm_base_url 指向自己的机器
    就能拿到所有人的剧本与自备故事原文,同时让全站生成瘫痪。必须是管理员闸门。"""
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    r = client.put("/api/config", json={"global": {"llm_base_url": "http://evil.example/v1"}})
    assert r.status_code == 403


def test_write_config_allows_admin(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setattr(api, "update_overrides", lambda fn: None)
    r = client.put("/api/config", json={"global": {}})
    assert r.status_code == 200


# ---- 按用户配 LLM:分层写权限 + 行级读过滤 ----
# global/stages 决定全站上游,仍限 admin;users[自己] 只影响自己名下的作品,故本人可改。

def test_write_config_allows_non_admin_to_edit_own_user_layer(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "cfg.json"))
    r = client.put("/api/config", json={"users": {"testuser": {"llm_model": "my-model"}}})
    assert r.status_code == 200
    assert runtime_config.load_overrides().users["testuser"].llm_model == "my-model"


def test_write_config_rejects_non_admin_editing_others_user_layer(monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    r = client.put("/api/config", json={"users": {"someone-else": {"llm_model": "x"}}})
    assert r.status_code == 403


def test_write_config_rejects_non_admin_touching_global_or_stages(monkeypatch):
    """只发 users 才放行;夹带 global/stages 一律 403,不做"部分执行"。"""
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    assert client.put("/api/config", json={"global": {"llm_model": "x"}}).status_code == 403
    assert client.put("/api/config", json={"stages": {"s0": {}}}).status_code == 403
    # 夹带在合法的 users 里也不行
    r = client.put("/api/config", json={"users": {"testuser": {}}, "global": {"llm_model": "x"}})
    assert r.status_code == 403


def test_write_config_admin_can_edit_any_user_layer(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "cfg.json"))
    r = client.put("/api/config", json={"users": {"zhanghui": {"llm_model": "for-zhanghui"}}})
    assert r.status_code == 200
    assert runtime_config.load_overrides().users["zhanghui"].llm_model == "for-zhanghui"


def test_write_config_rejects_image_field_in_user_layer(monkeypatch, tmp_path):
    """422 而不是 403:这是 UserOverride 的 extra="forbid" 在拦,连管理员也塞不进去。
    image 端点按人可配的话,单并发的两处 hostname 判定会静默失效。"""
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "cfg.json"))
    r = client.put("/api/config",
                   json={"users": {"testuser": {"image_base_url": "http://192.168.1.9:8099/v1"}}})
    assert r.status_code == 422


def test_read_config_filters_user_layer_for_non_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "cfg.json"))
    runtime_config.save_overrides(runtime_config.AppConfig(users={
        "testuser": runtime_config.UserOverride(llm_model="mine"),
        "someone-else": runtime_config.UserOverride(llm_model="theirs"),
    }))
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    assert set(client.get("/api/config").json()["users"]) == {"testuser"}
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    assert set(client.get("/api/config").json()["users"]) == {"testuser", "someone-else"}


def test_export_rejects_other_owner(tmp_path, monkeypatch):
    """导出会读改写他人的 project.json 并往他人目录落盘,必须校验归属。
    注意不能改走 _editable:导出不受只读拦截是 export_project 里刻意的设计。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone-else"
    store.save(p, root=tmp_path)
    assert client.post(f"/api/projects/{p.project_id}/export").status_code == 403


def test_export_rejects_ownerless_project_for_non_admin(tmp_path, monkeypatch):
    """无主作品(owner 为空)对普通用户一律 403,导出侧与 _editable 判据一致。

    2026-08-06 之前这里断言的是 200(为存量历史数据留的口子),线上无主数据清零后收紧。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = ""
    store.save(p, root=tmp_path)
    with patch("shanhai.api.export.build_exports", side_effect=lambda proj, _d: proj):
        assert client.post(f"/api/projects/{p.project_id}/export").status_code == 403


def test_admin_still_edits_ownerless_project(tmp_path, monkeypatch):
    """收紧后无主作品并非谁都改不了——管理员仍可编辑,否则真冒出无主数据就成了死锁。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = ""
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    store.save(p, root=tmp_path)
    r = client.patch(f"/api/projects/{p.project_id}/cells/1", json={"caption": "新"})
    assert r.status_code == 200


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


# gate 本身的用例已随 _use_master_skill 迁到 tests/test_runtime_config.py
# (它现在还负责往 status 写引擎记录,与 runtime_config 的其余职责同处)。


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
    # 注意 owner 层**保留**(只跳环节层):该开关针对的是环节级钉死的 skill 后端,
    # 而用户自选的 LLM 不是 skill——跳掉它会让 use_hermes_agent=False 顺带没收个人配置。
    from unittest.mock import MagicMock
    p = Project(project_id="hafid", scenic_spot="雷峰塔", owner="zhanghui")
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

    resolve_settings.assert_any_call(None, runtime_config.AppConfig(), owner="zhanghui")
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
    p = Project(project_id="editid", scenic_spot="雷峰塔", owner=TEST_USER)
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


def test_patch_cell_rejects_project_without_owner():
    # owner 为空的作品:普通用户改不了(2026-08-06 收紧,此前断言的是 200)。
    p = Project(project_id="legacyid", scenic_spot="雷峰塔")
    assert p.owner == ""
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    with patch("shanhai.api.store.load", return_value=p), patch("shanhai.api.store.save"):
        r = client.patch("/api/projects/legacyid/cells/1", json={"caption": "新"})
    assert r.status_code == 403


# ---- 管理员归属旁路(_may_edit)。前四条锁"能",后两条锁"能到哪为止"。----

def test_patch_cell_allows_admin_on_other_owner_project(monkeypatch):
    """管理员可编辑任何人的作品。这是产品要求(「admin 用户可以执行任何用户的命令」),
    此前 _editable 里没有这条旁路,管理员改别人的作品一样吃 403。"""
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    p = Project(project_id="adminpatchid", scenic_spot="雷峰塔", owner="someoneelse")
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    with patch("shanhai.api.store.load", return_value=p), patch("shanhai.api.store.save"):
        r = client.patch("/api/projects/adminpatchid/cells/1", json={"caption": "新"})
    assert r.status_code == 200


def test_delete_cell_allows_admin_on_other_owner_project(monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    p = Project(project_id="admindelcellid", scenic_spot="雷峰塔", owner="someoneelse")
    p.storyboard = [StoryboardCell(index=1, scene_ref="", visual_desc="a", characters=[],
                                   caption="c1", emotion="宁静")]
    with patch("shanhai.api.store.load", return_value=p), patch("shanhai.api.store.save"):
        r = client.delete("/api/projects/admindelcellid/cells/1")
    assert r.status_code == 200


def test_cancel_allows_admin_on_other_owner_project(tmp_path, monkeypatch):
    """cancel 有自己的一份归属判据(不走 _editable),必须同样认管理员——否则管理员
    看得到别人卡住的作业却停不掉,只能重启进程。"""
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone-else"
    store.save(p, root=tmp_path)
    r = client.post(f"/api/projects/{p.project_id}/cancel")
    assert r.status_code == 400   # 归属放行 → 落到"当前没有可取消的作业",而不是 403


def test_export_allows_admin_on_other_owner_project(tmp_path, monkeypatch):
    """export 也有自己的一份判据(刻意不走 _editable,因为导出不受只读拦截)。"""
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone-else"
    store.save(p, root=tmp_path)
    with patch("shanhai.api.export.build_exports", side_effect=lambda proj, _d: proj):
        assert client.post(f"/api/projects/{p.project_id}/export").status_code == 200


def test_admin_bypass_does_not_cover_readonly(monkeypatch):
    """旁路只覆盖「归属」。只读是部署模式(公开演示),不是权限档位——管理员照样被拦,
    否则演示站上一个管理员会话就能改数据。"""
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.patch("/api/projects/anyid/cells/1", json={"caption": "x"})
    assert r.status_code == 403


def test_admin_bypass_does_not_cover_pending_job(monkeypatch):
    """同上:「有未完成作业」拦的是并发写导致的丢更新,与身份无关,管理员一样 409。"""
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    saved = dict(api._JOBS)
    api._JOBS.clear()
    f = Future()
    api._JOBS["adminpendingid"] = f
    try:
        r = client.patch("/api/projects/adminpendingid/cells/1", json={"caption": "x"})
        assert r.status_code == 409
    finally:
        f.set_result(None)
        api._JOBS.clear()
        api._JOBS.update(saved)


def _spy_clients():
    """替身 _clients:记下每次拿到的 Settings 的 llm_model,返回四个假 client。
    断言的是"解析出的模型是谁的",不是 Settings 对象本身。"""
    from unittest.mock import MagicMock

    class Spy:
        models: list[str] = []

        def __call__(self, s):
            self.models.append(s.llm_model)
            return (MagicMock(), MagicMock(), MagicMock(), MagicMock())

    spy = Spy()
    spy.models = []
    return spy


def test_run_one_step_resolves_with_project_owner_not_operator(tmp_path, monkeypatch):
    """**决策 4 的锁**:按作品 owner 解析,不是按当前操作者。

    admin 帮 zhanghui 重跑 S1 时若切成 admin 自己的 LLM,同一部作品的 S1 与 S4 会走两套模型、
    文风对不上。而且后台线程手里本来就只有 p.owner(提交时 user 就丢了)。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = Project(project_id="ownerresolve", scenic_spot="雷峰塔", owner="zhanghui")
    cfg = runtime_config.AppConfig(
        global_=runtime_config.ConfigOverride(llm_model="admin-cloud"),
        users={"zhanghui": runtime_config.UserOverride(llm_model="zhanghui-local")},
    )
    seen = _spy_clients()
    with patch("shanhai.api._clients", side_effect=seen), \
         patch("shanhai.api._check_cancelled", return_value=True):   # 解析完立刻短路,不真跑
        api._run_one_step(p, p.project_id, "s1", cfg, tmp_path)
    assert seen.models == ["zhanghui-local"]


def test_run_one_step_ownerless_project_uses_global(tmp_path, monkeypatch):
    """历史无主项目(owner="")跳过 users 层——存量数据行为与今天完全一致。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = Project(project_id="ownerlessres", scenic_spot="雷峰塔")
    assert p.owner == ""
    cfg = runtime_config.AppConfig(
        global_=runtime_config.ConfigOverride(llm_model="admin-cloud"),
        users={"zhanghui": runtime_config.UserOverride(llm_model="zhanghui-local")},
    )
    seen = _spy_clients()
    with patch("shanhai.api._clients", side_effect=seen), \
         patch("shanhai.api._check_cancelled", return_value=True):
        api._run_one_step(p, p.project_id, "s1", cfg, tmp_path)
    assert seen.models == ["admin-cloud"]


def test_reorder_rejects_non_permutation():
    p = Project(project_id="reorderid", scenic_spot="雷峰塔", owner=TEST_USER)
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
    p = Project(project_id="stepid", scenic_spot="雷峰塔", owner=TEST_USER)
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
    p = Project(project_id="qid", scenic_spot="雷峰塔", owner=TEST_USER)
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
    stale = Project(project_id="freshid", scenic_spot="旧", owner=TEST_USER)   # _editable 锁外拿到的陈旧快照
    fresh = Project(project_id="freshid", scenic_spot="新", owner=TEST_USER)   # 锁内重载应拿到的最新快照
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

def test_files_hides_project_json(tmp_path, monkeypatch):
    # FP8:project.json(含用户 story、legend sources、角色 feature_prompt 等内部态)不经
    # /files 暴露。在 StaticFiles 规范化 path 之后按 basename 拦截,故各绕过变体都应 404,
    # 而其它产物正常托管。写真实 projects/ 目录再验证(否则文件不存在测不出"拦截 vs 未命中")。
    # 用真 cookie 客户端:/files 现在还有一道登录闸,依赖覆盖对它无效(见 _cookie_client)。
    import shutil
    c = _cookie_client(tmp_path, monkeypatch)
    d = store.DEFAULT_ROOT / "fp8test"
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text('{"secret": "内部 prompt"}', encoding="utf-8")
    (d / "art.txt").write_text("ok", encoding="utf-8")     # 对照:非 project.json 正常托管
    try:
        assert c.get("/files/fp8test/project.json").status_code == 404       # 规范路径
        assert c.get("/files/fp8test/project.json/").status_code == 404      # 尾随斜杠绕过
        assert c.get("/files/fp8test/PROJECT.JSON").status_code == 404        # 大小写绕过
        assert c.head("/files/fp8test/project.json").status_code == 404       # HEAD 绕过
        assert c.get("/files/fp8test/art.txt").status_code == 200             # 其它产物不受影响
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_files_requires_login(tmp_path, monkeypatch):
    """/files 托管的是用户产物(成片/页图/配音/真人参考照),不是 SPA 构建资源。
    此前完全免鉴权,URL 一旦外泄(转发、日志、Referer)文件即永久公开,而 DGX 单实例
    同时对 cpolar 公网开放。未登录一律 404 而非 401:不区分「没登录」与「文件不存在」,
    否则匿名者可拿 URL 探测某个作品是否存在。"""
    import shutil
    d = store.DEFAULT_ROOT / "gatetest"
    d.mkdir(parents=True, exist_ok=True)
    (d / "art.txt").write_text("ok", encoding="utf-8")
    try:
        anon = TestClient(api.app)                     # 全新客户端,无 cookie
        assert anon.get("/files/gatetest/art.txt").status_code == 404
        c = _cookie_client(tmp_path, monkeypatch)
        assert c.get("/files/gatetest/art.txt").status_code == 200
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_files_gate_is_login_only_not_ownership(tmp_path, monkeypatch):
    """闸门只认「登录」,**不认归属**——「所有人看到全部作品」是写进 2026-07-14 设计文档的
    产品目标(:55),静态资源不该比 API 本身更严,否则用户在列表里点开别人的作品会是
    「文案渲染正常、图/音/视频全 404」。这条锁住这个决定,防止以后被误收紧。"""
    import shutil
    p = Project(project_id="othersproj", scenic_spot="雷峰塔", owner="someoneelse")
    d = store.DEFAULT_ROOT / p.project_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "art.txt").write_text("ok", encoding="utf-8")
    try:
        c = _cookie_client(tmp_path, monkeypatch)      # 登录名是 testuser,不是 owner
        assert c.get(f"/files/{p.project_id}/art.txt").status_code == 200
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_files_hides_whole_voice_sample_namespace(tmp_path, monkeypatch):
    """真人录音只能经带鉴权的 GET /api/projects/{id}/voice-sample 拿。

    此前 index.json(音色句柄 → 随机盐文件名的全表)不在 protected 里、可匿名下载,
    读一次就击穿了 vs_<token>.wav 依赖的「靠随机 token 保密」——那也让「录音收到本人+admin」
    形同虚设(绕开端点直接下文件)。故整个前缀摘掉,而不是单补拦 index.json。"""
    import shutil
    d = store.voice_sample_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text('{"clone:x": "vs_tok.wav"}', encoding="utf-8")
    (d / "vs_tok.wav").write_bytes(b"RIFF....WAVE")
    try:
        c = _cookie_client(tmp_path, monkeypatch)      # 即便已登录也拿不到
        base = f"/files/{store.VOICE_SAMPLE_DIRNAME}"
        assert c.get(f"{base}/index.json").status_code == 404
        assert c.get(f"{base}/vs_tok.wav").status_code == 404
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
    _config_path() 延迟读 SHANHAI_CONFIG_PATH,故设环境变量即可。

    顺带把调用方当作管理员:PUT /api/config 现在有 is_admin 闸门,而这一组用例测的是
    合并/脱敏/剪枝语义,不是鉴权(鉴权另有 test_write_config_requires_admin 专管)。"""
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(api, "is_admin", lambda user: True)


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


def _ref_project(owner: str = TEST_USER) -> Project:
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


# _serialize 的 page 字典是逐字段挑选的,不是 model_dump。给 StoryboardCell 加字段并不会
# 自动出现在响应里,而前端 types.ts 把字段声明成必填、TypeScript 又校验不到运行时响应,
# 所以漏一个字段是**完全静默**的:`pg.xxx` 恒 undefined,那块 UI 永远不渲染。
# image_gen_ms 就这么漏了整整一个版本(83e7a10 加了字段/写入/前端渲染/类型,唯独没加序列化)。
# 这份清单与 web/src/types.ts 的 Page 接口一一对应,改一边必须改另一边。
_PAGE_FIELDS_USED_BY_WEB = {
    "index", "caption", "emotion", "status", "duration_ms", "silent",
    "scene_ref", "visual_desc", "characters", "image", "audio", "tracks",
    "image_gen_ms", "image_route", "image_lora", "missing_refs",
    "image_prompt", "panels",
}


def test_serialize_page_exposes_every_field_the_web_uses(tmp_path: Path):
    p = Project(project_id="pageFieldsId", scenic_spot="雷峰塔")
    p.storyboard = [_imaged_page()]
    with patch("shanhai.api.store.project_dir", return_value=tmp_path):
        page = api._serialize(p)["pages"][0]
    # 用相等而不是包含:少了会静默不渲染,多了说明前端类型没跟上,两边都该被发现
    assert set(page) == _PAGE_FIELDS_USED_BY_WEB


# 角色字典同样是逐字段挑选的,同样会静默漏——上面 pages 那道锁是 image_gen_ms 漏了一个版本
# 之后补的,角色侧此前一直没有。加 turnaround_gen_ms 时一并建起来。
# 这份清单与 web/src/types.ts 的 Character 接口一一对应,改一边必须改另一边。
_CHARACTER_FIELDS_USED_BY_WEB = {"name", "role", "image", "reference_image", "turnaround_gen_ms"}


def test_serialize_character_exposes_every_field_the_web_uses(tmp_path: Path):
    p = Project(project_id="charFieldsId", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[],
                      characters=[CharacterCard(name="白娘子", role="主角", personality="坚韧",
                                                appearance="白衣", turnaround_image="characters/白娘子.png",
                                                turnaround_gen_ms=4200)])
    with patch("shanhai.api.store.project_dir", return_value=tmp_path):
        character = api._serialize(p)["characters"][0]
    assert set(character) == _CHARACTER_FIELDS_USED_BY_WEB
    assert character["turnaround_gen_ms"] == 4200


# ---- 自备故事原文持久化 ----

def test_create_persists_story_verbatim(tmp_path, monkeypatch):
    """自备故事原文必须一字不差落盘(含换行):S0 只取 ≤200 字梗概,原文丢了就再也找不回来。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    story = "第一段\n\n第二段:白娘子与许仙。   末尾留白 "
    with patch("shanhai.api._pipeline"), patch("shanhai.api.Settings"):
        r = client.post("/api/projects",
                        json={"scenic_spot": "雷峰塔", "minutes": 1, "story": story})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    api._JOBS[pid].result(timeout=2)
    assert store.load(pid, root=tmp_path).story == story


def test_create_without_story_leaves_none(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    with patch("shanhai.api._pipeline"), patch("shanhai.api.Settings"):
        r = client.post("/api/projects", json={"scenic_spot": "雷峰塔", "minutes": 1})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    api._JOBS[pid].result(timeout=2)
    assert store.load(pid, root=tmp_path).story is None   # None = 走自动检索传说


def test_legacy_project_json_without_story_loads(tmp_path):
    """改造前落盘的 project.json 没有 story 键,反序列化不能炸。"""
    (tmp_path / "oldid01").mkdir()
    (tmp_path / "oldid01" / "project.json").write_text(
        '{"project_id": "oldid01", "scenic_spot": "雷峰塔"}', encoding="utf-8")
    assert store.load("oldid01", root=tmp_path).story is None


def test_serialize_exposes_only_story_flag(tmp_path):
    """详情响应只带一个布尔位,不带原文。原文最长 20000 字,而详情端点在管线跑动时
    被前端每 2 秒轮询一次(App.tsx 的 tick),塞进去等于每 2 秒重传一遍 60KB。"""
    p = Project(project_id="storyId01", scenic_spot="雷峰塔", story="很久以前")
    with patch("shanhai.api.store.project_dir", return_value=tmp_path):
        out = api._serialize(p)
    assert out["has_story"] is True
    assert "story" not in out


def test_serialize_story_flag_false_without_story(tmp_path):
    p = Project(project_id="storyId02", scenic_spot="雷峰塔")
    with patch("shanhai.api.store.project_dir", return_value=tmp_path):
        assert api._serialize(p)["has_story"] is False


def test_story_endpoint_returns_verbatim(tmp_path, monkeypatch):
    """原文改走独立端点,用户点开按钮时才拉一次。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    p.story = "第一段\n\n第二段"
    store.save(p, root=tmp_path)
    r = client.get(f"/api/projects/{p.project_id}/story")
    assert r.status_code == 200
    assert r.json()["story"] == "第一段\n\n第二段"


def test_story_endpoint_null_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    store.save(p, root=tmp_path)  # 端点是从盘上重读的,只改内存这份不算数
    assert client.get(f"/api/projects/{p.project_id}/story").json()["story"] is None


def test_story_endpoint_404_for_missing_project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    assert client.get("/api/projects/nosuchid/story").status_code == 404


# ---- 私人素材(自备故事原文 / 真人录音)只给作者与管理员。----
# 与「作品团队内全员可见」并存:这两样是用户**输入**的素材,不是生成出来的作品。

def test_story_rejects_non_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner, p.story = "someone-else", "别人贴的私人文本"
    store.save(p, root=tmp_path)
    assert client.get(f"/api/projects/{p.project_id}/story").status_code == 403


def test_story_allows_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner, p.story = "someone-else", "别人贴的私人文本"
    store.save(p, root=tmp_path)
    r = client.get(f"/api/projects/{p.project_id}/story")
    assert r.status_code == 200 and r.json()["story"] == "别人贴的私人文本"


def test_story_rejects_ownerless_project_for_non_admin(tmp_path, monkeypatch):
    """判据与写侧 _may_edit 共用,故收紧「无主」这条在读侧同步生效:自备原文是私人文本,
    无主时不该落给随便哪个登录用户。2026-08-06 之前这里断言的是 200。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner, p.story = "", "无主项目的原文"
    store.save(p, root=tmp_path)
    assert client.get(f"/api/projects/{p.project_id}/story").status_code == 403


def test_voice_sample_rejects_non_owner(tmp_path, monkeypatch):
    """录音是声音生物特征,比自备故事更该收。403 要早于「有没有音色」的 404,
    否则非归属者能拿状态码探出别人有没有传过录音。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone-else"
    p.params.voice = "clone:whatever.wav"
    store.save(p, root=tmp_path)
    assert client.get(f"/api/projects/{p.project_id}/voice-sample").status_code == 403


def test_voice_sample_allows_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = "someone-else"
    store.save(p, root=tmp_path)
    # 归属放行后落到「该作品没有可回听的自定义音色」那条 404,而不是 403
    assert client.get(f"/api/projects/{p.project_id}/voice-sample").status_code == 404


def test_list_projects_omits_story(tmp_path, monkeypatch):
    """列表端点逐字段白名单输出,不得带上原文:20000 字 × N 个项目会把列表拖垮。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.story = "很久以前" * 100
    store.save(p, root=tmp_path)
    assert "story" not in client.get("/api/projects").json()[0]


# ---------- 回听自定义音色 ----------

def test_serialize_reports_voice_sample_only_when_index_has_it(tmp_path, monkeypatch):
    """详情只给布尔位,音频本体走独立端点按需取——与 has_story 同一套取舍:
    详情端点在管线跑动时被前端每 2 秒轮一次,不该让它顺带背额外东西。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = Project(project_id="vs01", scenic_spot="雷峰塔")
    p.params.voice = "clone:shanhai_voice_abc.wav"
    with patch("shanhai.api.store.project_dir", return_value=tmp_path / "vs01"):
        assert api._serialize(p)["has_voice_sample"] is False   # 索引里还没有
        store.remember_voice_sample("clone:shanhai_voice_abc.wav", "vs_A.wav", root=tmp_path)
        assert api._serialize(p)["has_voice_sample"] is False   # 索引有了但文件不在
        (store.voice_sample_dir(tmp_path) / "vs_A.wav").write_bytes(b"RIFFfake")
        assert api._serialize(p)["has_voice_sample"] is True


def test_serialize_no_voice_sample_for_preset_voice(tmp_path, monkeypatch):
    """判据是"索引里查得到"而不是"voice 以 clone: 开头":那个前缀是上游 TTS 返回的约定,
    我们代码里从未强制过,拿它当判据是猜。预置音色自然查不到。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = Project(project_id="vs02", scenic_spot="雷峰塔")
    p.params.voice = "alloy"
    with patch("shanhai.api.store.project_dir", return_value=tmp_path / "vs02"):
        assert api._serialize(p)["has_voice_sample"] is False


def test_voice_sample_endpoint_streams_the_wav(tmp_path, monkeypatch):
    """录音是**真人声音**,而 /files 挂载没有任何身份校验(靠随机盐保密)。
    回听走带 Depends(current_user) 的端点,至少和作品本身同一个可见性级别。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    p.params.voice = "clone:v1.wav"
    store.save(p, root=tmp_path)
    store.remember_voice_sample("clone:v1.wav", "vs_B.wav", root=tmp_path)
    (store.voice_sample_dir(tmp_path) / "vs_B.wav").write_bytes(b"RIFFhello")
    r = client.get(f"/api/projects/{p.project_id}/voice-sample")
    assert r.status_code == 200
    assert r.content == b"RIFFhello"
    # URL 不含音色标识,换音色后路径不变但指向另一个文件;不禁缓存浏览器会一直放旧录音。
    assert r.headers["cache-control"] == "no-store"


def test_update_voice_clears_every_page_audio(tmp_path, monkeypatch):
    """换音色的核心回归:此前只复位 status,而 s5 的续跑复用分支只看 audio/silent +
    文件在不在,于是旧嗓子的 mp3 被原样复用——用户点了【补全重生成】听到的还是旧音色。
    voice_en 一并清空,否则英文轨的显式覆盖会盖过新音色。"""
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    p.params.voice, p.params.voice_en = "clone:old.wav", "clone:old.wav"
    p.storyboard = [StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc=f"v{i}",
                                   characters=[], caption=f"cap{i}", emotion="宁静",
                                   image=f"pages/page_{i:02d}.png",
                                   audio=f"audio/page_{i:02d}.mp3", duration_ms=1000)
                    for i in (1, 2)]
    p.storyboard[0].tracks["en"] = LocalizedTrack(caption="en1", audio="audio/page_01.en.mp3",
                                                  duration_ms=2000)
    p.status.update({"s4": "done", "s5": "done", "s5_en": "done"})
    store.save(p, root=tmp_path)

    r = client.patch(f"/api/projects/{p.project_id}/params/voice",
                     json={"voice": "clone:new.wav"})
    assert r.status_code == 200
    got = store.load(p.project_id, root=tmp_path)
    assert got.params.voice == "clone:new.wav" and got.params.voice_en == ""
    assert all(c.audio == "" and c.duration_ms == 0 for c in got.storyboard)
    assert got.storyboard[0].tracks["en"].audio == ""
    assert got.storyboard[0].image == "pages/page_01.png"   # 画面不受影响
    assert "s5" not in got.status and "s5_en" not in got.status
    assert got.status["s4"] == "done"


def test_voice_sample_endpoint_404_when_no_custom_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    store.save(p, root=tmp_path)
    assert client.get(f"/api/projects/{p.project_id}/voice-sample").status_code == 404


def test_voice_sample_endpoint_404_for_missing_project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    assert client.get("/api/projects/nosuch/voice-sample").status_code == 404


# ---------- 账号管理端点 ----------
# 用真 cookie 客户端(_cookie_client)而不是依赖覆盖:本组要验的正是「改密后旧 cookie 失效」,
# 而 _login_override 把 current_user 整个换掉了,校验逻辑根本不会执行。

def _real_client() -> TestClient:
    """让开本文件的 autouse 依赖覆盖。那个 fixture 把 current_user 恒定成 "testuser",
    而本组要验的恰恰是真实 current_user 的行为(is_admin 判据、pwd_ver 比对、disabled 拦截),
    不清掉的话每个断言测的都是替身。"""
    api.app.dependency_overrides.clear()
    return TestClient(api.app)


def _login_as(name: str, password: str) -> TestClient:
    c = _real_client()
    assert c.post("/api/login", json={"username": name, "password": password}).status_code == 200
    return c


def _admin_client(tmp_path, monkeypatch, name="boss"):
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    auth.add_user(name, "pw-" + name, admin=True)
    return _login_as(name, "pw-" + name)


def test_users_endpoints_reject_non_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    auth.add_user("plain", "pw-plain")
    c = _real_client()
    c.post("/api/login", json={"username": "plain", "password": "pw-plain"})
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/users", json={"username": "x1", "password": "goodpassword"}).status_code == 403
    assert c.patch("/api/users/plain", json={"is_admin": True}).status_code == 403


def test_admin_creates_user_who_can_then_log_in(tmp_path, monkeypatch):
    c = _admin_client(tmp_path, monkeypatch)
    r = c.post("/api/users", json={"username": "newbie", "password": "goodpassword"})
    assert r.status_code == 200
    assert {u["username"] for u in c.get("/api/users").json()} == {"boss", "newbie"}
    fresh = _real_client()
    assert fresh.post("/api/login",
                      json={"username": "newbie", "password": "goodpassword"}).status_code == 200


def test_create_user_duplicate_is_409(tmp_path, monkeypatch):
    """409 而不是 400:这是冲突不是参数错。更要紧的是它**没有**静默覆盖 boss 的密码。"""
    c = _admin_client(tmp_path, monkeypatch)
    assert c.post("/api/users", json={"username": "boss", "password": "goodpassword"}).status_code == 409
    assert auth.verify_login("boss", "pw-boss")          # 原密码完好


def test_list_users_never_returns_password_hash(tmp_path, monkeypatch):
    c = _admin_client(tmp_path, monkeypatch)
    for row in c.get("/api/users").json():
        assert set(row) == {"username", "is_admin", "disabled"}


def test_self_password_change_requires_old_password(tmp_path, monkeypatch):
    c = _admin_client(tmp_path, monkeypatch)
    assert c.post("/api/users/boss/password",
                  json={"new_password": "newpassword1"}).status_code == 400      # 没带原密码
    assert c.post("/api/users/boss/password",
                  json={"old_password": "wrong", "new_password": "newpassword1"}
                  ).status_code == 400                                            # 原密码错


def test_changing_own_password_invalidates_the_old_cookie(tmp_path, monkeypatch):
    """**决策「改密后旧会话失效」的执法点。** 签名 cookie 无服务端存储、默认 14 天有效,
    不比对 pwd_ver 的话改完密码旧 cookie 还能再用两周。"""
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    auth.add_user("wuzi", "oldpassword")
    c = _real_client()
    c.post("/api/login", json={"username": "wuzi", "password": "oldpassword"})
    assert c.get("/api/me").status_code == 200
    r = c.post("/api/users/wuzi/password",
               json={"old_password": "oldpassword", "new_password": "newpassword1"})
    assert r.status_code == 200
    assert c.get("/api/me").status_code == 401           # 同一个 cookie 立刻失效
    fresh = _real_client()
    assert fresh.post("/api/login",
                      json={"username": "wuzi", "password": "newpassword1"}).status_code == 200


def test_admin_reset_invalidates_the_targets_session(tmp_path, monkeypatch):
    """管理员重置别人密码不需要原密码,且对方所有设备立刻掉线——重置往往正是因为号可能被盗。"""
    admin = _admin_client(tmp_path, monkeypatch)
    admin.post("/api/users", json={"username": "victim", "password": "goodpassword"})
    victim = _real_client()
    victim.post("/api/login", json={"username": "victim", "password": "goodpassword"})
    assert victim.get("/api/me").status_code == 200
    assert admin.post("/api/users/victim/password",
                      json={"new_password": "resetpassword"}).status_code == 200
    assert victim.get("/api/me").status_code == 401


def test_disabling_a_user_kills_session_and_login(tmp_path, monkeypatch):
    admin = _admin_client(tmp_path, monkeypatch)
    admin.post("/api/users", json={"username": "leaver", "password": "goodpassword"})
    leaver = _real_client()
    leaver.post("/api/login", json={"username": "leaver", "password": "goodpassword"})
    assert leaver.get("/api/me").status_code == 200
    assert admin.patch("/api/users/leaver", json={"disabled": True}).status_code == 200
    assert leaver.get("/api/me").status_code == 401                    # 现有会话断掉
    fresh = _real_client()
    assert fresh.post("/api/login",
                      json={"username": "leaver", "password": "goodpassword"}).status_code == 401


def test_admin_cannot_change_own_flags(tmp_path, monkeypatch):
    """防止把自己锁在门外:要降级/停用自己,让另一个管理员来做。"""
    c = _admin_client(tmp_path, monkeypatch)
    assert c.patch("/api/users/boss", json={"is_admin": False}).status_code == 400
    assert c.patch("/api/users/boss", json={"disabled": True}).status_code == 400


def test_cannot_demote_the_last_admin_via_http(tmp_path, monkeypatch):
    c = _admin_client(tmp_path, monkeypatch)
    c.post("/api/users", json={"username": "boss2", "password": "goodpassword", "is_admin": True})
    assert c.patch("/api/users/boss2", json={"is_admin": False}).status_code == 200   # 还剩 boss
    # 现在只剩 boss 一个管理员,而 boss 不能改自己 → 最后一个管理员在任何路径下都降不掉
    assert c.patch("/api/users/boss", json={"is_admin": False}).status_code == 400


def test_patch_user_only_touches_sent_fields(tmp_path, monkeypatch):
    """model_fields_set:只发 disabled 时不该把 is_admin 静默重置。"""
    c = _admin_client(tmp_path, monkeypatch)
    c.post("/api/users", json={"username": "u2", "password": "goodpassword", "is_admin": True})
    r = c.patch("/api/users/u2", json={"disabled": True})
    assert r.status_code == 200 and r.json() == {"username": "u2", "is_admin": True, "disabled": True}


def test_user_endpoints_blocked_in_readonly(tmp_path, monkeypatch):
    c = _admin_client(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "_READONLY", True)
    assert c.post("/api/users", json={"username": "x", "password": "goodpassword"}).status_code == 403
    assert c.post("/api/users/boss/password",
                  json={"old_password": "pw-boss", "new_password": "newpassword1"}).status_code == 403
    assert c.patch("/api/users/boss", json={"disabled": True}).status_code == 403
    assert c.get("/api/users").status_code == 200        # 只读不挡读


def test_legacy_cookie_without_pwd_ver_still_works(tmp_path, monkeypatch):
    """上线不该把现有登录的人踢下线:老 session 无 pwd_ver、老账号记录也无,两边都取 0 相等。"""
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    users.write_text(json.dumps({"users": [
        {"username": "old", "password_hash": auth.hash_password("goodpassword")}]}),
        encoding="utf-8")
    c = _real_client()
    assert c.post("/api/login", json={"username": "old", "password": "goodpassword"}).status_code == 200
    assert c.get("/api/me").status_code == 200


# ---------- 用户 BGM 库 ----------

def _seed_bgm(tmp_path, monkeypatch, owner="alice", item_id="b1"):
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    (tmp_path / store.BGM_DIRNAME).mkdir(parents=True, exist_ok=True)
    (tmp_path / store.BGM_DIRNAME / f"{item_id}.mp3").write_bytes(b"ID3fake")
    item = {"id": item_id, "owner": owner, "name": "存好的", "source": "upload",
            "file": f"{item_id}.mp3", "duration_ms": 30000}
    store.update_bgm_items(lambda items: items.append(item))
    return item


def test_bgm_list_is_per_user(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="alice", item_id="a1")
    _seed_bgm(tmp_path, monkeypatch, owner="someone-else", item_id="o1")
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    # _login_override 恒为 testuser,所以两条都不是他的
    assert client.get("/api/bgm").json() == []
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    assert [r["id"] for r in client.get("/api/bgm").json()] == ["t1"]


def test_bgm_list_admin_sees_all(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="alice", item_id="a1")
    monkeypatch.setattr(api, "is_admin", lambda user: True)
    assert [r["id"] for r in client.get("/api/bgm").json()] == ["a1"]


def test_bgm_row_never_leaks_the_salted_filename(tmp_path, monkeypatch):
    """file 是盘上的随机盐文件名,没有任何理由让客户端看到——它是"靠不可推导保密"的一环。"""
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    row = client.get("/api/bgm").json()[0]
    assert set(row) == {"id", "name", "source", "owner", "duration_ms"}


def test_bgm_audio_rejects_other_owner(tmp_path, monkeypatch):
    """404 而不是 403:不区分"不存在"与"不是你的",不给探测面。"""
    _seed_bgm(tmp_path, monkeypatch, owner="someone-else", item_id="o1")
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    assert client.get("/api/bgm/o1/audio").status_code == 404


def test_bgm_audio_streams_for_owner(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    r = client.get("/api/bgm/t1/audio")
    assert r.status_code == 200 and r.content == b"ID3fake"
    assert r.headers["cache-control"] == "no-store"


def test_bgm_delete_removes_entry_and_file(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    assert client.delete("/api/bgm/t1").status_code == 200
    assert store.load_bgm_items() == []
    assert not (tmp_path / store.BGM_DIRNAME / "t1.mp3").exists()


def test_bgm_delete_rejects_other_owner(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="someone-else", item_id="o1")
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    assert client.delete("/api/bgm/o1").status_code == 404
    assert len(store.load_bgm_items()) == 1               # 没被删掉


def test_patch_project_bgm_sets_ref_and_implies_switch(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    p.params.bgm = False
    store.save(p, root=tmp_path)
    r = client.patch(f"/api/projects/{p.project_id}/params/bgm", json={"bgm_ref": "t1"})
    assert r.status_code == 200
    saved = store.load(p.project_id, root=tmp_path)
    assert saved.params.bgm_ref == "t1"
    assert saved.params.bgm is True            # 选了具体某首就隐含"要配乐"


def test_patch_project_bgm_rejects_other_owners_item(tmp_path, monkeypatch):
    """归属校验在端点做(这里知道是谁在操作),不在 S5 里做。"""
    _seed_bgm(tmp_path, monkeypatch, owner="someone-else", item_id="o1")
    monkeypatch.setattr(api, "is_admin", lambda user: False)
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    store.save(p, root=tmp_path)
    assert client.patch(f"/api/projects/{p.project_id}/params/bgm",
                        json={"bgm_ref": "o1"}).status_code == 404


def test_patch_project_bgm_empty_ref_returns_to_ai(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    p = store.create_project("雷峰塔", root=tmp_path)
    p.owner = TEST_USER          # 生产路径必设 owner,夹具不补就成了「无主作品」
    p.params.bgm_ref = "t1"
    store.save(p, root=tmp_path)
    assert client.patch(f"/api/projects/{p.project_id}/params/bgm",
                        json={"bgm_ref": ""}).status_code == 200
    assert store.load(p.project_id, root=tmp_path).params.bgm_ref == ""


def test_bgm_write_endpoints_blocked_in_readonly(tmp_path, monkeypatch):
    _seed_bgm(tmp_path, monkeypatch, owner="testuser", item_id="t1")
    monkeypatch.setattr(api, "_READONLY", True)
    assert client.delete("/api/bgm/t1").status_code == 403
    assert client.get("/api/bgm").status_code == 200       # 只读不挡读


def test_files_hides_whole_bgm_namespace(tmp_path, monkeypatch):
    """与 _voice_samples 同一道闸:库按用户隔离,不该经一个只认"登录了没"的静态挂载漏出去
    ——否则任何登录用户读一次 _bgm/index.json 就能顺着文件名把别人的库整个下走。"""
    import shutil as _sh
    c = _cookie_client(tmp_path, monkeypatch)
    d = store.DEFAULT_ROOT / store.BGM_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text('{"items": []}', encoding="utf-8")
    (d / "bgm_x.mp3").write_bytes(b"ID3fake")
    try:
        base = f"/files/{store.BGM_DIRNAME}"
        assert c.get(f"{base}/index.json").status_code == 404
        assert c.get(f"{base}/bgm_x.mp3").status_code == 404
    finally:
        _sh.rmtree(d, ignore_errors=True)


# ---------- 配置连通性测试(POST /api/config/test) ----------

_HERMES = "http://127.0.0.1:8642/v1"          # 纯 OpenAI 兼容(线上 hermes-agent 的形状)
_OLLAMA = "http://127.0.0.1:11434/v1"


def _probe_spy(record: list):
    """替身 _probe_llm:记录每个组合解析出的 (provider, base_url, model),不发真请求。
    这样可以断言"测了哪几个组合",而不必起真服务——真 HTTP 由端到端脚本覆盖。
    判负规则模拟真实世界:拿 Ollama 原生协议去打只会说 OpenAI 的服务(8642)必然 404。"""
    def fake(s):
        record.append((s.llm_provider, s.llm_endpoint[0], s.llm_model))
        broken = s.llm_provider == "ollama" and "8642" in s.llm_endpoint[0]
        return (not broken), "替身"
    return fake


def test_config_test_reports_each_stage_endpoint_separately(_isolated_config_path, monkeypatch):
    """**本端点存在的理由。** 复刻 2026-08-08 线上那份配置的形状:用户在自己那层配了本机
    Ollama,而 stages.s1 被管理员钉死指向 hermes。

    只测"用户那一层"会显示**一个**组合、一切正常——但生成时 s1 根本不走那个地址。
    按环节逐个解析才看得见这种分叉,这正是只测单层做不到的事。

    (跨层的 provider 穿透本身已由 runtime_config._apply_layer 在合并期堵死,所以这里 s1
    解析出的是 hermes + openai 而非 hermes + ollama;这条顺带成了那个修复的回归证据。)"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(
            llm_base_url=_OLLAMA, llm_model="glm", llm_provider="ollama")},
        stages={"s1": runtime_config.ConfigOverride(
            llm_base_url=_HERMES, llm_model="hermes-agent")},
    ))
    seen: list = []
    monkeypatch.setattr(api, "_probe_llm", _probe_spy(seen))
    r = client.post("/api/config/test", json={"scope": "user:alice", "config": {}})
    assert r.status_code == 200
    body = r.json()
    combos = {(x["base_url"], x["provider"]) for x in body["results"]}
    assert combos == {(_HERMES, "openai"), (_OLLAMA, "ollama")}
    hermes = [x for x in body["results"] if x["base_url"] == _HERMES][0]
    assert hermes["stages"] == ["s1"]        # 用户自己从没配过这个地址,但 s1 就是走它
    assert body["ok"] is True                # 协议与端点配套,这份配置是好的


def test_config_test_surfaces_a_broken_stage(_isolated_config_path, monkeypatch):
    """某个环节真的配坏时(这里:管理员显式给纯 OpenAI 服务写了 ollama 协议),
    整体判负且点名是哪个环节——用户在保存前就知道,不必等一轮生成跑完 20 分钟。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(llm_base_url=_OLLAMA, llm_model="glm")},
        stages={"s1": runtime_config.ConfigOverride(
            llm_base_url=_HERMES, llm_model="hermes-agent", llm_provider="ollama")},
    ))
    monkeypatch.setattr(api, "_probe_llm", _probe_spy([]))
    body = client.post("/api/config/test",
                       json={"scope": "user:alice", "config": {}}).json()
    assert body["ok"] is False
    bad = [x for x in body["results"] if not x["ok"]]
    assert len(bad) == 1 and bad[0]["stages"] == ["s1"] and bad[0]["base_url"] == _HERMES


def test_config_test_dedupes_identical_stages(_isolated_config_path, monkeypatch):
    """四个 LLM 环节配置相同时只探一次——否则每点一次测试就打上游四遍。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(llm_base_url=_OLLAMA, llm_model="m")}))
    seen: list = []
    monkeypatch.setattr(api, "_probe_llm", _probe_spy(seen))
    r = client.post("/api/config/test", json={"scope": "user:alice", "config": {}})
    assert len(seen) == 1                                  # 只探一次
    assert r.json()["results"][0]["stages"] == list(api._LLM_STAGES)


def test_config_test_uses_candidate_without_persisting(_isolated_config_path, monkeypatch):
    """测的是**未保存的候选值**,且测完不能落盘——否则"先测后存"就成了"测即是存"。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(llm_base_url=_OLLAMA, llm_model="old")}))
    before = runtime_config.load_overrides().model_dump()
    seen: list = []
    monkeypatch.setattr(api, "_probe_llm", _probe_spy(seen))
    client.post("/api/config/test", json={
        "scope": "user:alice",
        "config": {"users": {"alice": {"llm_model": "candidate-model"}}}})
    assert seen[0][2] == "candidate-model"                  # 候选值真的生效了
    assert runtime_config.load_overrides().model_dump() == before   # 但没写进盘


def test_config_test_never_echoes_api_key(_isolated_config_path, monkeypatch):
    """响应逐字段锁死:base_url 可回显(global/stages 本就对所有登录用户可见),密钥绝不。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(
            llm_base_url=_OLLAMA, llm_api_key="sk-super-secret", llm_model="m")}))
    monkeypatch.setattr(api, "_probe_llm", _probe_spy([]))
    r = client.post("/api/config/test", json={"scope": "user:alice", "config": {}})
    assert set(r.json()["results"][0]) == {
        "stages", "provider", "base_url", "model", "ok", "detail", "elapsed_ms"}
    assert "sk-super-secret" not in r.text


def test_config_test_rejects_readonly(monkeypatch):
    monkeypatch.setattr(api, "_READONLY", True)
    r = client.post("/api/config/test", json={"scope": "user:testuser", "config": {}})
    assert r.status_code == 403


def test_config_test_scope_permissions(tmp_path, monkeypatch):
    """分权与 PUT /api/config 同判据:影响面超出自己的只认 is_admin。"""
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    auth.add_user("plain", "pw-plain")
    c = _login_as("plain", "pw-plain")
    monkeypatch.setattr(api, "_probe_llm", lambda s: (True, "替身"))
    assert c.post("/api/config/test", json={"scope": "global", "config": {}}).status_code == 403
    assert c.post("/api/config/test", json={"scope": "s1", "config": {}}).status_code == 403
    assert c.post("/api/config/test",
                  json={"scope": "user:someone", "config": {}}).status_code == 403
    assert c.post("/api/config/test",
                  json={"scope": "user:plain", "config": {}}).status_code == 200


def test_config_test_rejects_unknown_and_non_llm_scope(_isolated_config_path, monkeypatch):
    monkeypatch.setattr(api, "is_admin", lambda u: True)
    monkeypatch.setattr(api, "_probe_llm", lambda s: (True, "替身"))
    assert client.post("/api/config/test", json={"scope": "s9", "config": {}}).status_code == 400
    # s4 是纯图像环节:测它没有意义,明说而不是静默返回空结果
    assert client.post("/api/config/test", json={"scope": "s4", "config": {}}).status_code == 400


def test_config_test_uses_short_timeout_not_llm_timeout(_isolated_config_path, monkeypatch):
    """**防"点一下挂 45 分钟"的执法点。**

    线上 llm_timeout 是 900 秒、重试 2 次:一次失败最坏 3×900+2+4 ≈ 45 分钟。测试是给人点的,
    必须自带短超时;而且 retries 必须是 0——配置错了重试多少次都是错,只会让用户多等三倍。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(
            llm_base_url="https://cloud.example.com/v1", llm_model="m", llm_timeout=900.0)}))
    built: dict = {}
    calls: dict = {}

    class FakeLLM:
        def __init__(self, base_url, api_key, model, timeout=300):
            built.update(timeout=timeout, base_url=base_url, model=model)

        def chat(self, system, user, temperature=0.7, retries=2, max_tokens=None):
            calls.update(retries=retries, max_tokens=max_tokens)
            return "好"

    monkeypatch.setattr(api, "LLMClient", FakeLLM)
    r = client.post("/api/config/test", json={"scope": "user:alice", "config": {}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert built["timeout"] == api.LLM_TEST_TIMEOUT_S      # 不是 900
    # 上界锁的是"人还愿意等",不是某个具体秒数。2026-08-08 从 20 抬到 60(线上换成思考型
    # 模型后回一个字要 10~20 秒,20 秒会约 1/5 概率误报失败),这条断言当时跟着一起改的——
    # 若哪天又要往上抬,先想清楚"人真的会等这么久吗",别顺手把上界跟着放大。
    assert built["timeout"] <= 60, "测试超时必须是人能等的量级"
    assert calls["retries"] == 0
    # ⚠️ 真正防长尾的是这个,不是超时值。思考型模型为了回一个字会先思考 185~1524 个 token
    # (实测耗时 8.8~74.3 秒,长尾无上界),抬超时抬多少都会有一条尾巴;掐输出长度才治本。
    # 漏传它 → 超时值再大也会偶发误报,而误报会让人去改一个本来正确的配置。
    assert calls["max_tokens"] == api.LLM_TEST_MAX_TOKENS


def test_config_test_treats_truncated_empty_output_as_success(_isolated_config_path, monkeypatch):
    """思考型模型被 max_tokens 截断后 content 可能为空——**那不算失败**。

    探活要回答的是"这个端点能不能正常应答我们这种请求"(协议对不对、鉴权过不过、模型名认不认),
    不是"它说了什么"。判成失败会把一个完全可用的配置报成坏的,而假阴性会让人去改对的东西。
    这条容易被后人当 bug 顺手"修"掉,所以单独锁住。"""
    runtime_config.save_overrides(runtime_config.AppConfig(
        users={"alice": runtime_config.UserOverride(
            llm_base_url="https://cloud.example.com/v1", llm_model="thinky")}))

    class SilentLLM:
        def __init__(self, *a, **k):
            pass

        def chat(self, system, user, temperature=0.7, retries=2, max_tokens=None):
            return ""          # 思考没结束就被截断,一个字都没轮到

    monkeypatch.setattr(api, "LLMClient", SilentLLM)
    body = client.post("/api/config/test",
                       json={"scope": "user:alice", "config": {}}).json()
    assert body["ok"] is True
    assert "截断" in body["results"][0]["detail"]      # 但要如实说明是截断,不能假装拿到了内容
