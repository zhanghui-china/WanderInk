"""FastAPI 薄封装:把现有 CLI 管线(S0–S6)包成 HTTP,供 web 前端调用。

设计要点:
- 不新增生成逻辑,复用 steps/* 与 cli._clients;生成耗时数分钟,故走后台线程。
- 进度直接读 project.status(每步 store.save 落盘),前端轮询 GET /api/projects/{id}。
- 产物(图/音/mp4)由 StaticFiles 挂 projects/ 目录托管为 /files/<id>/...。
- 若 web/dist 存在(前端已 build),挂到 / 作为单页应用;dev 时前端另起 Vite 连本服务。
"""
import os
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shanhai import export, store
from shanhai.cli import _AUDIENCES, _MINUTES, _TONES, _clients
from shanhai.config import Settings
from shanhai.schema import Project
from shanhai.steps import (s0_legend, s1_script, s2_storyboard, s3_characters,
                           s4_pages, s5_audio, s6_compose)
from shanhai.styles import STYLE_PRESETS

app = FastAPI(title="山海 · 有声连环画生成器")

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


# ---------- 后台管线 ----------

def _pipeline(project_id: str, s: Settings, story: str | None) -> None:
    """在后台线程里从 S0 一路跑到 MP4,每步落盘,pipeline 状态写入 project.status。"""
    p = store.load(project_id)
    workdir = store.project_dir(project_id)
    llm, image, tts = _clients(s)
    try:
        p.status["pipeline"] = "running"
        store.save(p)
        if story is not None:
            p = s0_legend.from_text(p, llm, story)
        else:
            p = s0_legend.run(p, llm)
            if not p.legend_candidates:
                p.status["pipeline"] = "error: 未检索到可靠传说,请提供自备故事"
                store.save(p)
                return
            p.legend = p.legend_candidates[0]
        store.save(p)
        stages = [
            ("s1", lambda: s1_script.run(p, llm)),
            ("s2", lambda: s2_storyboard.run(p, llm)),
            ("s3", lambda: s3_characters.run(p, llm, image, workdir, s.image_size)),
            ("s4", lambda: s4_pages.run(p, image, workdir, s.image_size,
                                        strict=s.strict_consistency)),
            ("s5", lambda: s5_audio.run(p, tts, s.tts_voice, workdir)),
            ("s6", lambda: s6_compose.run(p, workdir)),
        ]
        for _name, fn in stages:
            fn()
            store.save(p)
        p.status["pipeline"] = "done"
        store.save(p)
    except Exception as e:  # noqa: BLE001 — 后台线程需兜住任何异常并记录到项目状态
        p.status["pipeline"] = f"error: {e}"
        store.save(p)


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
        "status": c.status, "duration_ms": c.duration_ms,
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
        "mp4": _mp4_url(p.output.get("mp4", "")),
        "zip": _mp4_url(p.output.get("zip", "")),
        "pdf": _mp4_url(p.output.get("pdf", "")),
    }


# ---------- 接口 ----------

class NewProject(BaseModel):
    scenic_spot: str
    minutes: int = 3
    audience: str = "大众"
    tone: str = "温情"
    style: str = "guofeng_ink"
    story: str | None = None
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


@app.post("/api/projects")
def create_project(body: NewProject) -> dict:
    """新建项目并在后台启动完整管线,立即返回 project_id 供前端轮询。"""
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,生成请在本机或 tailnet 内进行")
    _validate(body)
    # 清理已完成作业句柄,避免 _JOBS 无界增长;并按未完成数做背压。
    for done in [k for k, f in _JOBS.items() if f.done()]:
        del _JOBS[done]
    if len(_JOBS) >= MAX_PENDING:
        raise HTTPException(429, f"生成队列已满(上限 {MAX_PENDING}),请稍后再试")
    s = Settings()
    p = store.create_project(body.scenic_spot)
    p.params.duration_min = body.minutes
    p.params.audience = body.audience
    p.params.tone = body.tone
    p.params.voice = body.voice
    p.params.speed = body.speed
    p.style_preset = body.style
    p.status["pipeline"] = "queued"
    store.save(p)
    _JOBS[p.project_id] = _EXECUTOR.submit(_pipeline, p.project_id, s, body.story)
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
    except FileNotFoundError as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    return _serialize(p)


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str) -> dict:
    """合成 PDF/ZIP 导出物(纯本地、无上游成本,故不受只读拦截)。"""
    try:
        p = store.load(project_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    p = export.build_exports(p, store.project_dir(project_id))
    store.save(p)
    return {
        "pdf": _mp4_url(p.output.get("pdf", "")),
        "zip": _mp4_url(p.output.get("zip", "")),
    }


@app.get("/api/meta")
def meta() -> dict:
    """前端建项目表单用的枚举选项。"""
    return {
        "minutes": list(_MINUTES), "audiences": list(_AUDIENCES),
        "tones": list(_TONES), "styles": list(STYLE_PRESETS),
        "voices": Settings().tts_voices_list,
        "readonly": _READONLY,
    }


# 产物静态托管 + 前端 build 产物(存在才挂,避免 dev 期报错)
store.DEFAULT_ROOT.mkdir(exist_ok=True)
app.mount("/files", StaticFiles(directory=str(store.DEFAULT_ROOT)), name="files")

_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if _WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run("shanhai.api:app", host="127.0.0.1", port=8080, reload=False)
