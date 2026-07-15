import os
import subprocess
import sys
import threading
from concurrent.futures import Future
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
    assert d["content_summary"] == {"total": 1, "imaged": 1, "narrated": 1, "silent": 0}


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
    assert d["content_summary"] == {"total": 2, "imaged": 1, "narrated": 0, "silent": 1}
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


def test_pipeline_records_step_and_total_timing():
    # 每步开始/结束都要落 started_at/elapsed_s,整体落 pipeline_started_at/pipeline_finished_at,
    # 供前端时间线展示每步及总耗时。
    from unittest.mock import MagicMock
    p = Project(project_id="timingid", scenic_spot="雷峰塔")
    mock_settings = MagicMock()
    mock_settings.image_endpoint = ("https://example.com/v1", "key")  # _image_concurrency 需要能解包
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
        api._pipeline("timingid", runtime_config.AppConfig(), "自备故事")

    assert p.status["pipeline"] != "running"   # 已跑到终态(mock 未产出可交付内容,具体终态值不是本测试重点)
    assert p.status["pipeline_started_at"]
    assert p.status["pipeline_finished_at"]
    for step in ("s0", "s1", "s2", "s3", "s4", "s5", "s6"):
        assert p.status[f"{step}_started_at"]
        float(p.status[f"{step}_elapsed_s"])   # 能转成 float,解析失败即测试失败


@patch("shanhai.api.Settings")
def test_run_step_records_step_timing(_settings):
    from unittest.mock import MagicMock
    p = Project(project_id="stepTimingId", scenic_spot="雷峰塔")
    with patch("shanhai.api.store.load", return_value=p), \
         patch("shanhai.api.store.save"), \
         patch("shanhai.api._clients", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("shanhai.api.s6_compose") as s6:
        s6.run.return_value = p
        api._run_step("stepTimingId", "s6", runtime_config.AppConfig())

    assert p.status["s6_started_at"]
    float(p.status["s6_elapsed_s"])
    assert p.status["pipeline_started_at"]
    assert p.status["pipeline_finished_at"]


def test_image_concurrency_serial_for_local_backend():
    # 本地 shim(127.0.0.1/localhost)背后是团队共用的单张 GPU,并发只会互相拖慢/冲突。
    # _env_file=None:隔离运行机器上真实 .env 的 SHANHAI_IMAGE_BASE_URL 等值,
    # 否则测试结果会随部署环境(Mac/DGX)漂移,而不是只测 base_url 本身。
    from shanhai.config import Settings
    s = Settings(_env_file=None, base_url="http://127.0.0.1:8091/v1", api_key="x")
    assert api._image_concurrency(s) == 1
    s2 = Settings(_env_file=None, base_url="http://localhost:8091/v1", api_key="x")
    assert api._image_concurrency(s2) == 1


def test_image_concurrency_parallel_for_remote_backend():
    from shanhai.config import Settings
    s = Settings(_env_file=None, base_url="https://api.tu-zi.com/v1", api_key="x")
    assert api._image_concurrency(s) == api.s4_pages.CONCURRENCY


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
    """把 runtime_config.CONFIG_PATH 指到 tmp_path,隔离测试对真实 config.json 的读写。"""
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", tmp_path / "config.json")


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
    """/files 静态托管按运行时 CONFIG_PATH.name 动态拦截配置文件:文件存在但仍 404 且不泄露内容。"""
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", store.DEFAULT_ROOT / "secrets.json")
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
