import os
import subprocess
import sys
import threading
from concurrent.futures import Future
from unittest.mock import patch

from fastapi.testclient import TestClient

from shanhai import api, store
from shanhai.schema import CharacterCard, Legend, Project, Script, StoryboardCell

client = TestClient(api.app)


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


@patch("shanhai.api._run_step")           # 不真跑单步
@patch("shanhai.api.Settings")            # 不读 .env / 建真实客户端
def test_run_step_queues_job(_settings, mock_run_step):
    p = Project(project_id="stepid", scenic_spot="雷峰塔")
    with patch("shanhai.api.store.load", return_value=p):
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
