import os
import subprocess
import sys
from concurrent.futures import Future
from unittest.mock import patch

from fastapi.testclient import TestClient

from shanhai import api
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


def test_get_missing_project_404():
    assert client.get("/api/projects/does_not_exist_xyz").status_code == 404


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
