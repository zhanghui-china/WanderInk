"""FastAPI 薄封装:把现有 CLI 管线(S0–S6)包成 HTTP,供 web 前端调用。

设计要点:
- 不新增生成逻辑,复用 steps/* 与 cli._clients;生成耗时数分钟,故走后台线程。
- 进度直接读 project.status(每步 store.save 落盘),前端轮询 GET /api/projects/{id}。
- 产物(图/音/mp4)由 StaticFiles 挂 projects/ 目录托管为 /files/<id>/...。
- 若 web/dist 存在(前端已 build),挂到 / 作为单页应用;dev 时前端另起 Vite 连本服务。
"""
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shanhai import editing, export, runtime_config, store
from shanhai.cli import (_AUDIENCES, _MINUTES, _TONES, _clients,
                         resolve_stage_clients)
from shanhai.config import Settings, load_env
from shanhai.runtime_config import (STAGE_CLIENTS, AppConfig, apply_put,
                                     config_view, load_overrides,
                                     resolve_settings, update_overrides)
from shanhai.schema import Project
from shanhai.steps import (s0_legend, s1_script, s2_storyboard, s3_characters,
                           s4_pages, s5_audio, s6_compose)
from shanhai.styles import STYLE_PRESETS

app = FastAPI(title="WanderInk · 有声连环画生成器")

# 把 .env 加载进 os.environ,供下面的 os.getenv 与之后的 Settings() 读取。
# override=False:已存在的进程环境变量(如 systemd EnvironmentFile 注入)优先于 .env。
load_env()

# CORS 来源可经 SHANHAI_CORS_ORIGINS(逗号分隔)收敛;默认 * 便于本地 dev。
# 此处直接读环境变量而非构造 Settings():middleware 在 import 期注册,
# 而 Settings 需要 base_url/api_key,import 期强制校验会在缺 .env 的环境下崩溃。
_CORS_ORIGINS = [o.strip() for o in os.getenv("SHANHAI_CORS_ORIGINS", "*").split(",") if o.strip()]

# 只读模式(公网暴露用):关闭 POST 新建生成,访客仅能浏览已有作品,不触发上游/烧额度。
_READONLY = os.getenv("SHANHAI_READONLY", "").strip().lower() in ("1", "true", "yes", "on")

app.add_middleware(
    CORSMiddleware, allow_origins=_CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
)

# 管线耗时数分钟且吃满上游配额:用单 worker 线程池串行化,天然限流,
# 避免多项目并发把上游打到 503(叠加 S4 内部 ×3 并发会放大过载)。
MAX_PENDING = 8  # 未完成作业(排队+运行)上限,超出则拒绝新建
_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_JOBS: dict[str, Future] = {}

# 端点是同步 def,Starlette 在 anyio 线程池并发跑同一 handler,故共享态需锁保护。
# 两级锁,层级单向、临界区互不重叠,不嵌套持有,故无死锁:
#   _JOBS_LOCK        —— 保护 _JOBS 的清理+背压+提交(create_project/run_step)与一致快照读。
#   _project_lock(id) —— 保护单项目「load→改→save」全程(各编辑端点 + 导出),防写者互相丢更新。
# 关键:_editable/_job_of 在进入 per-project 锁「之前」就已释放 _JOBS_LOCK(顺序获取,非嵌套);
# 提交路径只碰 _JOBS_LOCK,读改写落盘只碰 per-project 锁,两个临界区不重叠。
_JOBS_LOCK = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.Lock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    """按项目 id 惰性建锁并复用;guard 临界区极短(仅字典查/建)。"""
    with _PROJECT_LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[project_id] = lock
        return lock


def _job_of(project_id: str) -> Future | None:
    """在 _JOBS_LOCK 下取该项目当前作业句柄的一致快照(避免与提交路径竞态)。"""
    with _JOBS_LOCK:
        return _JOBS.get(project_id)


def _locked_save(p: Project) -> None:
    """后台管线/单步在 per-project 锁内落盘,与编辑/导出端点的读改写互斥,不与它们交错写盘。
    锁序恒为 project→jobs:调用方(后台线程)不持 _JOBS_LOCK,故此处只取 project 锁,无嵌套倒置。
    注意:仅供不持 project 锁的后台函数用;已在锁内的端点仍用 store.save(p)(threading.Lock 不可重入)。"""
    with _project_lock(p.project_id):
        store.save(p)


# ---------- 后台管线 ----------

def _deliverable_status(p: Project) -> str:
    """诚实闸门:据当前状态判定「整体是否已交付一部成片」。
    无 output['mp4'](未合成/编辑后已失效)→ partial:尚未合成,而非 done(单步重跑 s2–s5 会走到这)。
    有 mp4 但无成图页面 → error(理论不该发生,防御);
    有 mp4 且含静音兜底页 → 降级 done;全程真人解说才 → 纯 done。"""
    if not p.output.get("mp4"):
        return "partial: 尚未合成成片"
    if not p.is_deliverable():
        return "error: 生成未产出可交付内容(无成图页面)"
    s = p.content_summary()
    if s["silent"] > 0:
        return f"done(降级:{s['narrated']}/{s['total']} 页真人解说,{s['silent']} 页静音兜底)"
    return "done"


def _pipeline(project_id: str, cfg: AppConfig, story: str | None) -> None:
    """在后台线程里从 S0 一路跑到 MP4,每步落盘,pipeline 状态写入 project.status。"""
    p = store.load(project_id)
    workdir = store.project_dir(project_id)
    # 逐环节解析生效 Settings 与 client(同一 cfg 快照,作业内配置一致;不同环节可用不同端点/模型)。
    settings, clients = resolve_stage_clients(cfg)
    try:
        p.status["pipeline"] = "running"
        _locked_save(p)
        if story is not None:
            p = s0_legend.from_text(p, clients["s0"][0], story)
        else:
            p = s0_legend.run(p, clients["s0"][0])
            if not p.legend_candidates:
                p.status["pipeline"] = "error: 未检索到可靠传说,请提供自备故事"
                _locked_save(p)
                return
            p.legend = p.legend_candidates[0]
        _locked_save(p)
        stages = [
            ("s1", lambda: s1_script.run(p, clients["s1"][0])),
            ("s2", lambda: s2_storyboard.run(p, clients["s2"][0])),
            ("s3", lambda: s3_characters.run(p, clients["s3"][0], clients["s3"][1], workdir,
                                             settings["s3"].image_size)),
            ("s4", lambda: s4_pages.run(p, clients["s4"][1], workdir, settings["s4"].image_size,
                                        strict=settings["s4"].strict_consistency)),
            ("s5", lambda: s5_audio.run(p, clients["s5"][2], settings["s5"].tts_voice, workdir,
                                        clients["s5"][3])),
            ("s6", lambda: s6_compose.run(p, workdir)),
        ]
        for _name, fn in stages:
            fn()
            _locked_save(p)
        p.status["pipeline"] = _deliverable_status(p)
        _locked_save(p)
    except Exception as e:  # noqa: BLE001 — 后台线程需兜住任何异常并记录到项目状态
        p.status["pipeline"] = f"error: {e}"
        _locked_save(p)


# ---------- 序列化:把落盘相对路径转成可访问 URL ----------

def _file_url(project_id: str, rel: str) -> str | None:
    """cell.image/audio、character.turnaround_image 都是相对项目目录的路径。"""
    return f"/files/{project_id}/{rel}" if rel else None


def _mp4_url(mp4: str) -> str | None:
    """output['mp4'] 形如 'projects/<id>/output/final.mp4',去掉 projects/ 前缀挂到 /files。"""
    if not mp4:
        return None
    return "/files/" + mp4.split("projects/", 1)[-1]


def _serialize(p: Project) -> dict:
    pages = [{
        "index": c.index, "caption": c.caption, "emotion": c.emotion,
        "status": c.status, "duration_ms": c.duration_ms, "silent": c.silent,
        "scene_ref": c.scene_ref, "visual_desc": c.visual_desc, "characters": c.characters,
        "image": _file_url(p.project_id, c.image),
        "audio": _file_url(p.project_id, c.audio),
    } for c in p.storyboard]
    characters = [{
        "name": c.name, "role": c.role,
        "image": _file_url(p.project_id, c.turnaround_image),
    } for c in (p.script.characters if p.script else [])]
    return {
        "project_id": p.project_id,
        "scenic_spot": p.scenic_spot,
        "style_preset": p.style_preset,
        "params": p.params.model_dump(),
        "status": p.status,
        "pipeline": p.status.get("pipeline", "pending"),
        "legend": p.legend.model_dump() if p.legend else None,
        "script_title": p.script.title if p.script else None,
        "characters": characters,
        "pages": pages,
        "deliverable": p.is_deliverable(),
        "content_summary": p.content_summary(),
        "mp4": _mp4_url(p.output.get("mp4", "")),
        "zip": _mp4_url(p.output.get("zip", "")),
        "pdf": _mp4_url(p.output.get("pdf", "")),
    }


# ---------- 接口 ----------

def _editable(project_id: str) -> Project:
    """编辑端点公共校验+载入,须在持有 _project_lock(project_id) 时调用:此时 job-check 成为
    真正的屏障(锁内确认无未完成作业才改),并载入最新快照,杜绝 check→加锁 的 TOCTOU 与丢更新。
    只读模式拒绝写入;有未完成后台作业拒绝并发编辑;项目须存在。锁序 project→jobs(此处再取 _JOBS_LOCK)。"""
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,禁止编辑")
    f = _job_of(project_id)  # 调用方已持 project 锁,此处再取 _JOBS_LOCK 一致快照(project→jobs)
    if f is not None and not f.done():
        raise HTTPException(409, "该项目有未完成的生成作业,请等待完成后再编辑")
    try:
        return store.load(project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e


class NewProject(BaseModel):
    scenic_spot: str
    minutes: int = 3
    audience: str = "大众"
    tone: str = "温情"
    style: str = "guofeng_ink"
    story: str | None = Field(default=None, max_length=20000)  # 自备故事上限,防超大体喂 LLM
    voice: str = ""
    speed: float = 1.0


def _validate(body: NewProject) -> None:
    if not body.scenic_spot.strip():
        raise HTTPException(400, "scenic_spot 不能为空")
    if body.minutes not in _MINUTES:
        raise HTTPException(400, f"minutes 须为 {list(_MINUTES)}")
    if body.audience not in _AUDIENCES:
        raise HTTPException(400, f"audience 须为 {list(_AUDIENCES)}")
    if body.tone not in _TONES:
        raise HTTPException(400, f"tone 须为 {list(_TONES)}")
    if body.style not in STYLE_PRESETS:
        raise HTTPException(400, f"style 须为 {list(STYLE_PRESETS)}")
    if not 0.5 <= body.speed <= 2.0:
        raise HTTPException(400, "speed 须落在 [0.5, 2.0]")


@app.post("/api/projects")
def create_project(body: NewProject) -> dict:
    """新建项目并在后台启动完整管线,立即返回 project_id 供前端轮询。"""
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,生成请在本机或 tailnet 内进行")
    _validate(body)
    Settings()  # 急切校验 .env 必填项(base_url/api_key):坏环境立刻失败,不建孤儿 queued 项目
    cfg = load_overrides()  # 配置快照,挪到临界区之前(_JOBS_LOCK 内不做磁盘 I/O);作业内配置一致
    # 清理→背压→建项目→提交 整段在 _JOBS_LOCK 内,保证 check-cleanup-submit 原子:
    # 清理用 items() 快照后再 del,避免并发迭代改写;背压判定与写回 _JOBS 不被抢跑。
    # 背压先于建项目,故 429 时不留下孤儿 queued 项目(与原语义一致)。
    with _JOBS_LOCK:
        for done in [k for k, f in list(_JOBS.items()) if f.done()]:
            del _JOBS[done]
        if len(_JOBS) >= MAX_PENDING:
            raise HTTPException(429, f"生成队列已满(上限 {MAX_PENDING}),请稍后再试")
        p = store.create_project(body.scenic_spot)
        p.params.duration_min = body.minutes
        p.params.audience = body.audience
        p.params.tone = body.tone
        p.params.voice = body.voice
        p.params.speed = body.speed
        p.style_preset = body.style
        p.status["pipeline"] = "queued"
        store.save(p)
        _JOBS[p.project_id] = _EXECUTOR.submit(_pipeline, p.project_id, cfg, body.story)
    return {"project_id": p.project_id}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    out = []
    for meta in sorted(store.DEFAULT_ROOT.glob("*/project.json")):
        try:
            p = store.load(meta.parent.name)
        except Exception:  # noqa: BLE001 — 跳过损坏/半写的项目,不让列表整体失败
            continue
        out.append({
            "project_id": p.project_id, "scenic_spot": p.scenic_spot,
            "pipeline": p.status.get("pipeline", "pending"),
            "mp4": _mp4_url(p.output.get("mp4", "")),
        })
    return out


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    try:
        p = store.load(project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    return _serialize(p)


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str) -> dict:
    """合成 PDF/ZIP 导出物(纯本地、无上游成本,故不受只读拦截)。
    但须避让运行中的生成作业:管线正边写边跑时导出会读到半成品、且导出的 save 会与
    管线的 save 互相丢更新(甚至回滚管线进度),故有未完成作业时 409;读改写落盘全程持 per-project 锁。"""
    with _project_lock(project_id):
        # job-check 移入锁内成为真正屏障(锁序 project→jobs);导出不做只读拦截(有意,见上)。
        f = _job_of(project_id)
        if f is not None and not f.done():
            raise HTTPException(409, "该项目有生成作业进行中,请稍后再导出")
        try:
            p = store.load(project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, f"项目不存在: {project_id}") from e
        p = export.build_exports(p, store.project_dir(project_id))
        store.save(p)
    return {
        "pdf": _mp4_url(p.output.get("pdf", "")),
        "zip": _mp4_url(p.output.get("zip", "")),
    }


class CellPatch(BaseModel):
    caption: str | None = None                                   # caption 由 schema max_length=80 兜
    visual_desc: str | None = Field(default=None, max_length=2000)
    emotion: str | None = None
    characters: list[str] | None = None


@app.patch("/api/projects/{project_id}/cells/{index}")
def patch_cell(project_id: str, index: int, body: CellPatch) -> dict:
    if body.characters is not None and len(body.characters) > 50:   # A7:编辑路径同样挡超大 characters
        raise HTTPException(400, "characters 数量上限 50")
    with _project_lock(project_id):
        p = _editable(project_id)
        try:
            editing.update_cell(p, index, caption=body.caption, visual_desc=body.visual_desc,
                                emotion=body.emotion, characters=body.characters)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/cells/{index}/redraw")
def redraw_cell(project_id: str, index: int) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id)
        try:
            editing.mark_redraw(p, index)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/cells/{index}/revoice")
def revoice_cell(project_id: str, index: int) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id)
        try:
            editing.mark_revoice(p, index)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


class CellInsert(BaseModel):
    after_index: int
    caption: str
    visual_desc: str = Field(max_length=2000)
    emotion: str = "宁静"
    characters: list[str] | None = None


@app.post("/api/projects/{project_id}/cells")
def create_cell(project_id: str, body: CellInsert) -> dict:
    if body.characters is not None and len(body.characters) > 50:
        raise HTTPException(400, "characters 数量超限(≤ 50)")
    with _project_lock(project_id):
        p = _editable(project_id)
        workdir = store.project_dir(project_id)
        try:
            editing.insert_cell(p, workdir, body.after_index, caption=body.caption,
                                visual_desc=body.visual_desc, emotion=body.emotion,
                                characters=body.characters)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.delete("/api/projects/{project_id}/cells/{index}")
def delete_cell(project_id: str, index: int) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id)
        workdir = store.project_dir(project_id)
        try:
            editing.delete_cell(p, workdir, index)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


class ReorderBody(BaseModel):
    order: list[int]


@app.post("/api/projects/{project_id}/cells/reorder")
def reorder_cells(project_id: str, body: ReorderBody) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id)
        workdir = store.project_dir(project_id)
        try:
            editing.reorder_cells(p, workdir, body.order)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/characters/{name}/redraw")
def redraw_character(project_id: str, name: str) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id)
        try:
            editing.mark_character_redraw(p, name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


# ---------- 单步重跑(编辑后局部重生成) ----------

_STEP_NAMES = ("s2", "s3", "s4", "s5", "s6")


def _run_step(project_id: str, name: str, cfg: AppConfig) -> None:
    """后台线程跑单步,复用 _pipeline 的状态写入 + 异常兜底语义(不复用其整段管线循环,
    因为这里只跑调用方指定的一步)。"""
    p = store.load(project_id)
    workdir = store.project_dir(project_id)
    s = resolve_settings(name, cfg)  # 该环节生效 Settings + client
    llm, image, tts, music = _clients(s)
    try:
        p.status["pipeline"] = "running"
        _locked_save(p)
        if name == "s2":
            p = s2_storyboard.run(p, llm)
        elif name == "s3":
            p = s3_characters.run(p, llm, image, workdir, s.image_size)
        elif name == "s4":
            p = s4_pages.run(p, image, workdir, s.image_size, strict=s.strict_consistency)
        elif name == "s5":
            p = s5_audio.run(p, tts, s.tts_voice, workdir, music)
        elif name == "s6":
            p = s6_compose.run(p, workdir)
        if name != "s6":
            p.output.clear()   # 重跑上游步骤使已合成的 mp4/zip/pdf 失效,清掉避免残留"假成片"
        _locked_save(p)
        p.status["pipeline"] = _deliverable_status(p)
        _locked_save(p)
    except Exception as e:  # noqa: BLE001 — 后台线程需兜住任何异常并记录到项目状态
        p.status["pipeline"] = f"error: {e}"
        _locked_save(p)


@app.post("/api/projects/{project_id}/steps/{name}")
def run_step(project_id: str, name: str) -> JSONResponse:
    """单步重跑(编辑后只需局部重生成,不必整条管线重来一遍)。"""
    if name not in _STEP_NAMES:
        raise HTTPException(400, f"未知步骤: {name}")
    _editable(project_id)  # 403/409/404 快速前置校验(返回丢弃,下面锁内重载最新快照)
    Settings()  # 急切校验 .env 必填项:坏环境立刻失败,不留孤儿 queued
    cfg = load_overrides()  # 锁外读配置快照
    # 锁序恒为 project→jobs:先占 project 锁(与编辑端点同序,queued 落盘不覆盖并发编辑),
    # 内层 _JOBS_LOCK 保证 清理→复检→背压→提交 原子。两锁始终同序嵌套,无死锁。
    with _project_lock(project_id):
        with _JOBS_LOCK:
            for done in [k for k, f in list(_JOBS.items()) if f.done()]:
                del _JOBS[done]
            # _editable 到此有窗口期,期间可能被另一并发请求抢先提交;锁内复检杜绝同项目重复提交。
            if project_id in _JOBS:
                raise HTTPException(409, "该项目有未完成的生成作业,请等待完成后再编辑")
            if len(_JOBS) >= MAX_PENDING:
                raise HTTPException(429, f"生成队列已满(上限 {MAX_PENDING}),请稍后再试")
            # 锁内重载最新快照再写 queued:避免用 _editable 的陈旧 p 覆盖并发编辑(丢更新);
            # queued 先于 submit 落盘,免 202 后轮询读到上次遗留的 done。此处已持 project 锁,
            # 故直接 store.save(而非 _locked_save,threading.Lock 不可重入)。
            p = store.load(project_id)
            p.status["pipeline"] = "queued"
            store.save(p)
            _JOBS[project_id] = _EXECUTOR.submit(_run_step, project_id, name, cfg)
    return JSONResponse({"queued": True}, status_code=202)


@app.get("/api/meta")
def meta() -> dict:
    """前端建项目表单用的枚举选项。"""
    return {
        "minutes": list(_MINUTES), "audiences": list(_AUDIENCES),
        "tones": list(_TONES), "styles": list(STYLE_PRESETS),
        "voices": resolve_settings().tts_voices_list,
        "readonly": _READONLY,
    }


# ---------- 端点/模型配置(全局默认 + 按环节覆盖) ----------
# 合并/脱敏/视图契约集中在 runtime_config(与模型同处、CLI 亦可复用);此处仅 HTTP 薄封装。

@app.get("/api/config")
def read_config() -> dict:
    return config_view(_READONLY)


@app.put("/api/config")
def write_config(body: AppConfig) -> dict:
    """写入端点/模型覆盖。校验(Literal/extra=forbid)由 AppConfig 解析完成:非法 provider/越权字段→422;
    只读→403;未知 stage→400。读-合并-写在 update_overrides 写锁内原子完成(避免并发 PUT 丢更新),
    合并语义(部分更新/密钥哨兵/环节保留与剪枝)见 runtime_config.apply_put。"""
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,禁止修改配置")
    unknown = [st for st in body.stages if st not in STAGE_CLIENTS]
    if unknown:
        raise HTTPException(400, f"未知环节: {unknown}(合法环节 {list(STAGE_CLIENTS)})")
    update_overrides(lambda existing: apply_put(existing, body))
    return config_view(_READONLY)


class _ArtifactStatic(StaticFiles):
    """产物静态托管,但禁下载任何 project.json(含用户 story、legend sources、角色 feature_prompt
    等内部态)与运行时配置文件(含明文密钥)。在 StaticFiles 已把 URL 规范化(折叠 ../ 双斜杠尾斜杠)成
    path 之后按 basename 拦截,故尾随斜杠/双斜杠/x/../project.json/大小写/HEAD 等在具体路由层可绕过的
    变体在此统一 404。受保护的配置文件名取运行时实际值 runtime_config.CONFIG_PATH(随 SHANHAI_CONFIG_PATH
    变化、可被测试 monkeypatch),而非 import 期冻结的常量。config.json 默认在 cwd 根、本不在挂载目录内,
    此拦截是运维误把它指进 projects/ 时的防御纵深(真正的护栏仍是把 CONFIG_PATH 留在被托管目录之外)。"""

    async def get_response(self, path: str, scope):  # noqa: ANN001 — 与父类签名一致
        protected = {"project.json", runtime_config.CONFIG_PATH.name.lower()}
        if Path(path).name.lower() in protected:
            raise HTTPException(404)
        return await super().get_response(path, scope)


# 产物静态托管 + 前端 build 产物(存在才挂,避免 dev 期报错)
store.DEFAULT_ROOT.mkdir(exist_ok=True)
app.mount("/files", _ArtifactStatic(directory=str(store.DEFAULT_ROOT)), name="files")

_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if _WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")


def reconcile_zombie_jobs(root: Path = store.DEFAULT_ROOT) -> int:
    """重启对账:_JOBS 是纯内存态,进程重启后必为空,故磁盘上任何 running/queued 都是
    再无线程推进的僵尸,否则前端将永久轮询。把它们改写为 error 落盘,返回处理条数。
    仅在 main() 启动时调用一次(不挂模块级/startup 事件——否则 pytest import 或 TestClient
    会扫写真实 projects/ 目录造成污染)。"""
    n = 0
    for meta in sorted(root.glob("*/project.json")):
        try:
            p = store.load(meta.parent.name, root=root)
        except Exception:  # noqa: BLE001 — 跳过损坏/半写项目,不阻断对账
            continue
        if p.status.get("pipeline") in ("running", "queued"):
            p.status["pipeline"] = "error: 服务重启,生成中断"
            store.save(p, root=root)
            n += 1
    return n


def main() -> None:
    import uvicorn
    reconcile_zombie_jobs()  # uvicorn.run 之前对账一次,清理上次崩溃/重启残留的僵尸作业
    host = os.getenv("SHANHAI_HOST", "127.0.0.1")  # 内网部署(如 DGX)设 0.0.0.0
    port = int(os.getenv("SHANHAI_PORT", "8080"))
    uvicorn.run("shanhai.api:app", host=host, port=port, reload=False)
