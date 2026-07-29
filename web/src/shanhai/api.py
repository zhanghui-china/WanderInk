"""FastAPI 薄封装:把现有 CLI 管线(S0–S6)包成 HTTP,供 web 前端调用。

设计要点:
- 不新增生成逻辑,复用 steps/* 与 cli._clients;生成耗时数分钟,故走后台线程。
- 进度直接读 project.status(每步 store.save 落盘),前端轮询 GET /api/projects/{id}。
- 产物(图/音/mp4)由 StaticFiles 挂 projects/ 目录托管为 /files/<id>/...。
- 若 web/dist 存在(前端已 build),挂到 / 作为单页应用;dev 时前端另起 Vite 连本服务。
"""
import json
import mimetypes
import os
import secrets
import shutil
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from shanhai import editing, export, ffmpeg, runtime_config, store, uploads
from shanhai.auth import current_user, is_admin, verify_login
from shanhai.cli import (_AUDIENCES, _MINUTES, _TONES, _clients,
                         resolve_stage_clients)
from shanhai.config import Settings, load_env
from shanhai.runtime_config import (STAGE_CLIENTS, AppConfig, apply_put,
                                     config_view, image_concurrency,
                                     load_overrides, resolve_settings,
                                     update_overrides)
from shanhai.safety import find_sensitive
from shanhai.schema import Project
from shanhai.steps import (s0_legend, s1_script, s2_storyboard, s3_characters,
                           s4_pages, s5_audio, s5t_translate, s6_compose)
from shanhai.loras import LORA_PRESETS
from shanhai.styles import STYLE_PRESETS
from shanhai.version import build_info

# 可产出的附加语种轨(主语言中文不在其中,它走原有流水线)。加一门语言只需扩 s5t_translate.LANGUAGES。
TRACK_LANGS = tuple(s5t_translate.LANGUAGES)
# 主语言码,与 s5_audio/s6_compose 同源,不在这里另写字面量
MAIN_LANG = s5_audio.DEFAULT_LANG

app = FastAPI(title="WanderInk · 有声连环画生成器")

# 显式注册 .vtt:浏览器只接受 Content-Type 为 text/vtt 的 <track src>,给成
# application/octet-stream 会被直接拒绝——而且是**静默**拒绝,字幕就是不出来、控制台
# 也未必报。StaticFiles 靠 mimetypes 猜,而它取决于运行环境的 /etc/mime.types 等系统
# 文件(实测本机 macOS 与 DGX 当前都能猜对,但那是环境的功劳不是我们的)。一行的保险。
mimetypes.add_type("text/vtt", ".vtt")

# 把 .env 加载进 os.environ,供下面的 os.getenv 与之后的 Settings() 读取。
# override=False:已存在的进程环境变量(如 systemd EnvironmentFile 注入)优先于 .env。
load_env()

# CORS 来源可经 SHANHAI_CORS_ORIGINS(逗号分隔)收敛;默认 * 便于本地 dev。
# 此处直接读环境变量而非构造 Settings():middleware 在 import 期注册,
# 而 Settings 需要 base_url/api_key,import 期强制校验会在缺 .env 的环境下崩溃。
_CORS_ORIGINS = [o.strip() for o in os.getenv("SHANHAI_CORS_ORIGINS", "*").split(",") if o.strip()]

# 只读模式(公网暴露用):关闭 POST 新建生成,访客仅能浏览已有作品,不触发上游/烧额度。
_READONLY = os.getenv("SHANHAI_READONLY", "").strip().lower() in ("1", "true", "yes", "on")

class BodySizeLimitMiddleware:
    """在 FastAPI 解析请求体**之前**按大小拒绝,纯 ASGI 层。

    为什么必须在这一层:FastAPI 的路由 handler 在被调用前就已经 `await request.form()`
    把整个 multipart 解析完、spool 到临时文件了,而且这发生在 solve_dependencies 之前——
    也就是说 **连未登录请求的 body 也会先完整落盘,再返回 401**。实测灌 200 MiB 分块请求,
    服务端会老老实实全部写进临时文件才回 413。写在 handler 里的任何上限(包括
    uploads.read_limited 和读 Content-Length 的快速失败)都晚了一步,挡不住任何字节。

    两道判据:Content-Length 命中直接拒(省掉整趟传输);没有这个头的分块传输则包住
    receive、累计计数,超限即拒。只对声明了 body 的请求生效,GET 等不受影响。"""

    def __init__(self, app, max_bytes: int) -> None:  # noqa: ANN001 — ASGI 约定
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._too_large(send)
        received = 0

        async def counting_receive():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.max_bytes:
                    # 断流:后续 body 不再交给下游,下游解析器会因请求体不完整而报错;
                    # 但此时我们已经先把 413 发出去了,客户端看到的是明确的拒绝。
                    raise _BodyTooLarge
            return msg

        try:
            await self.app(scope, counting_receive, send)
        except _BodyTooLarge:
            await self._too_large(send)

    async def _too_large(self, send) -> None:  # noqa: ANN001
        mib = self.max_bytes // 1024 // 1024
        body = json.dumps({"detail": f"请求体超过 {mib} MiB 上限"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    """内部信号:body 累计超限,由 BodySizeLimitMiddleware 自己捕获,不外泄。"""


app.add_middleware(
    CORSMiddleware, allow_origins=_CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
)

# 登录态用 Starlette 签名 cookie(SessionMiddleware),无需服务端 session 表。
# secret 固定则重启后 cookie 仍有效;缺省时用进程内临时值,重启即令全员登出。
_SESSION_SECRET = os.getenv("SHANHAI_SESSION_SECRET")
if not _SESSION_SECRET:
    _SESSION_SECRET = secrets.token_hex(32)
    print("[警告] 未设置 SHANHAI_SESSION_SECRET,已生成进程内临时密钥:"
          "每次重启会使签名 cookie 失效、所有人被登出。生产部署请在环境变量中固定该值。",
          file=sys.stderr)
# 默认不加 Secure(当前部署为 tailnet/内网直连 HTTP,加了 Secure 反而会让 cookie 完全发不出去);
# 若未来接 HTTPS(反代终止 TLS 或直连 HTTPS),置 SHANHAI_SESSION_HTTPS_ONLY=true 收紧。
_SESSION_HTTPS_ONLY = os.getenv("SHANHAI_SESSION_HTTPS_ONLY", "").strip().lower() in ("1", "true", "yes", "on")
app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET, https_only=_SESSION_HTTPS_ONLY)

# 最后 add 的 middleware 在最外层,也就是最先看到请求——body 上限必须在最外层,
# 否则 CORS/Session 之后、路由解析 form 之前的窗口仍会让超大 body 落盘。
# 留出 multipart 边界与表单字段的开销,故略高于 uploads.MAX_UPLOAD_BYTES。
app.add_middleware(BodySizeLimitMiddleware, max_bytes=uploads.MAX_UPLOAD_BYTES + (1 << 20))

# 云端环节可真并行,本地 Spark 端点的串行化交给 providers/_http.py 的
# local_backend_guard 全局锁(按物理 GPU 排队,不按线程池排队)。
# 并发数按 MAX_PENDING=8 的一半估算,避免过多项目同时抢云端配额。
MAX_PENDING = 8  # 未完成作业(排队+运行)上限,超出则拒绝新建
_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_JOBS: dict[str, Future] = {}
# 协作式取消标记:project_id 命中则在下一个环节切换点(_pipeline/_run_step)提前退出,
# 不能打断正卡在网络调用里的当前环节。仿 _JOBS 的定义风格,同样受 _JOBS_LOCK 保护读写。
_CANCELLED: set[str] = set()

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


def _check_cancelled(project_id: str) -> bool:
    """在 _JOBS_LOCK 下查询并消费该项目的取消标记(命中即移除,不重复触发)。"""
    with _JOBS_LOCK:
        if project_id in _CANCELLED:
            _CANCELLED.discard(project_id)
            return True
        return False


def _is_cancelled(project_id: str) -> bool:
    """非消费性查询,供并发 worker 快速探测是否应尽快收尾;真正消费(讨掉标记)
    仍由 _check_cancelled 在环节边界统一做一次,不在这里重复消费。"""
    with _JOBS_LOCK:
        return project_id in _CANCELLED


def _locked_save(p: Project) -> None:
    """后台管线/单步在 per-project 锁内落盘,与编辑/导出端点的读改写互斥,不与它们交错写盘。
    锁序恒为 project→jobs:调用方(后台线程)不持 _JOBS_LOCK,故此处只取 project 锁,无嵌套倒置。
    注意:仅供不持 project 锁的后台函数用;已在锁内的端点仍用 store.save(p)(threading.Lock 不可重入)。"""
    with _project_lock(p.project_id):
        store.save(p)


def _save_error(project_id: str, e: Exception) -> None:
    """步骤异常兜底:不落半损坏的内存态(步骤函数可能在校验抛错前已改坏一半 project,
    如 s2 先赋值 storyboard 再校验),改从磁盘重载重跑前的干净快照,只把 error 状态写回
    再落盘,保住磁盘上完整的 storyboard/产物引用。重载失败(项目文件本身损坏)则静默放弃,
    避免在异常处理里再抛异常导致状态完全不写。"""
    try:
        p = store.load(project_id)
    except Exception:  # noqa: BLE001 — 兜底不可再抛;重载失败就放弃写状态,保留磁盘原样
        return
    p.status["pipeline"] = f"error: {e}"
    p.status["pipeline_finished_at"] = _now_iso()
    _locked_save(p)


# ---------- 后台管线 ----------

def _deliverable_status(p: Project) -> str:
    """诚实闸门:据当前状态判定「整体是否已交付一部成片」。
    无 output['mp4'](未合成/编辑后已失效)→ partial:尚未合成,而非 done(单步重跑 s2–s5 会走到这)。
    有 mp4 但无成图页面 → error(理论不该发生,防御);
    有 mp4 但入选成片页数 < 总页数(S4 缺图 / S5 缺音的页被 s6 跳过)和/或含静音兜底页 → 降级 done;
    全部都没有(全程出图 + 全程有音 + 全程真人解说)才是纯 done。"""
    if not p.output.get("mp4"):
        return "partial: 尚未合成成片"
    if not p.is_deliverable():
        return "error: 生成未产出可交付内容(无成图页面)"
    s = p.content_summary()
    total = s["total"]
    # composed 与 s6_compose._content_cells 的入选契约一致(confirmed 且图/音齐备)。
    # content_summary['imaged'] 只看 confirmed+image、不含 audio,故"有图但音轨被清(audio='')"的页
    # 会逃过 imaged<total 判定却入不了成片——单算 composed 才能诚实反映真正入选的页数。
    composed = sum(1 for c in p.storyboard if c.status == "confirmed" and c.image and c.audio)
    notes = []
    if s["imaged"] < total:
        notes.append(f"{s['imaged']}/{total} 页出图(其余生成失败已跳过)")
    if composed < total:
        notes.append(f"{composed}/{total} 页入选成片(其余缺图/缺音被跳过)")
    if s["silent"] > 0:
        notes.append(f"{s['narrated']}/{total} 页真人解说,{s['silent']} 页静音兜底")
    if notes:
        return f"done(降级:{'; '.join(notes)})"
    return "done"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 会做"已有产物就跳过"的环节:只有它们可能整轮空转,也只有它们需要空跑守卫。
# s0/s1/s2 每次都真的重写文本(产物只落在 project.json 里,文件指纹看不见),
# track_*/未知环节同理——都不设守卫,一律照实记耗时。
_SKIPPABLE_STEPS = frozenset({"s3", "s4", "s5", "s6"})


def _artifact_fingerprint(workdir: Path) -> tuple[int, int, int]:
    """项目产物目录的指纹:(文件数, 最新 mtime_ns, 总字节数)。任何一个产物文件被写过,指纹就变。

    为什么不按环节数产物个数(第一版就是那么写的,被审计推翻):重跑时"计数能代表增量"这个
    前提对四个环节没有一个成立——s6_compose 完全不幂等、每次全量重编码却写回同名的
    output["mp4"];s5 的 BGM 每轮无条件重烧、静音兜底页会被真人音轨原地替换;s4 重画失败页
    时 cell.image 前后都是真值;s3 对第 5 个及以后的角色每次都重算。数个数一个都抓不住,
    直接问文件系统"这一轮到底有没有文件被写过"才是可靠的判据。

    排除 project.json:它是我们自己每步都要落盘的状态文件,算进来指纹必变、守卫永远失效。
    已知取舍:s3 对超出 MAX_TURNAROUND 的角色只重算 feature_prompt(写进 project.json、
    不落文件),这种"只改数据不产文件"的重跑会被判成空跑而不记耗时——够边缘,不为它加复杂度。"""
    count = size = 0
    newest = 0
    for root, _dirs, files in os.walk(workdir):
        for fn in files:
            if root == str(workdir) and fn == "project.json":
                continue
            try:
                st = os.stat(os.path.join(root, fn))
            except OSError:      # 并发删改导致的瞬时缺失:跳过即可,指纹只需"够敏感"
                continue
            count += 1
            size += st.st_size
            newest = max(newest, st.st_mtime_ns)
    return count, newest, size


class _StepStart(NamedTuple):
    """一次环节运行的凭证:_mark_step_started 造,原样交给 _mark_step_elapsed。
    started_at 只揣在内存里,等收尾时和另外两个键一起原子写入(理由见 _mark_step_started)。"""
    t0: float
    started_at: str
    fingerprint: tuple[int, int, int] | None   # None = 该环节不设空跑守卫


def _mark_step_started(p: Project, name: str, workdir: Path) -> _StepStart:
    """标记该环节"本次运行"开始。

    {name}_started_at / _finished_at / _elapsed_s 三个键描述同一次运行,而且是**最近一次**真实
    运行、不是历史累计——用户拍板的语义:总时长 = 各步骤最后一次耗时相加。

    **这三个键一律等到 _mark_step_elapsed 里一次性写入,开工时一个都不碰**。第一版是开工就
    改写 started_at、顺手 pop 掉 finished_at,被审计打回:紧跟着就有一次无条件落盘,若步骤体
    抛异常或进程被杀,盘上会留下"本轮的 started_at + 上一轮的 elapsed_s + 没有 finished_at"
    这种分属两次运行的错配状态,前端会永久显示"进行中"却同时列着上一轮的耗时,而且下一次
    空跑还会把它原样还原、自锁住。改成收尾时原子写入后,中途死掉只是保留上一轮的完整记录
    (陈旧但自洽),不会产生有毒状态,守卫也退化成"什么都不写"这一个动作。

    运行期间的"进行中"显示改用单独的 {name}_running_since:它不属于那三个自洽键,前端只在
    pipeline 正在跑时才认它,残留一个陈旧值无害。"""
    p.status[f"{name}_running_since"] = _now_iso()
    fp = _artifact_fingerprint(workdir) if name in _SKIPPABLE_STEPS else None
    return _StepStart(time.monotonic(), _now_iso(), fp)


def _mark_step_elapsed(p: Project, name: str, start: _StepStart, workdir: Path) -> bool:
    """原子写入该环节本次运行的起止与耗时,直接覆盖上一轮的值(不累加)。返回是否真的记了。

    空跑守卫:产物指纹与开工时完全一致 → 本轮被幂等逻辑全量跳过、一个文件都没写,
    三个计时键原样不动(实测:DGX 上 5 个 s5_elapsed_s=2.0 的作品,每页音频都在盘上、
    一页没重做,那 2 秒纯粹是加载遍历的空转;把真实的十几分钟覆盖成 2 秒,正是用户报的
    那个 bug)。指纹必须在这里重算——环节函数返回的是新的 p、产物也刚落盘。

    守卫**只看指纹**,不再附加"此前已有耗时记录"这个前置条件。第一版有,结果是计时键被级联
    清空之后的首次空跑照样绕过守卫:「石坊温热」的 s3 就这样被写成 0.0 秒(4 个三视图早就在
    盘上、一个没重画,真实耗时 0.0016 秒),前端显示「0秒」、总耗时也跟着少算一整个环节。
    一次没产出任何文件的运行,无论此前有没有记录,记下来的耗时都不代表这个环节的工作量。
    代价:此前从没记过时间、且本轮又确实没产文件的环节,那一行会留空(悬停显示「尚未生成」)
    而不是显示一个 0 秒——留空是诚实的,0 秒是假的。

    返回值给调用方判断要不要走"下游产物已过期"的级联:本轮什么都没重做,下游自然也没过期。"""
    p.status.pop(f"{name}_running_since", None)
    if start.fingerprint is not None and _artifact_fingerprint(workdir) == start.fingerprint:
        return False
    p.status[f"{name}_started_at"] = start.started_at
    p.status[f"{name}_elapsed_s"] = f"{time.monotonic() - start.t0:.1f}"
    p.status[f"{name}_finished_at"] = _now_iso()   # 本次真实完成的墙钟时刻,前端直接读它展示
    # "结束"时间,不用 开始时间+elapsed 现算(两者中间可能隔着排队/取消,现算会偏)。
    return True


def _pipeline(project_id: str, cfg: AppConfig, story: str | None) -> None:
    """在后台线程里从 S0 一路跑到 MP4,每步落盘,pipeline 状态写入 project.status。"""
    try:
        # 序言也纳入 try:store.load/resolve_stage_clients/_clients 抛异常(project.json 损坏、
        # 畸形 base_url 触发 httpx.InvalidURL、ImportError 等)会被 Future 静默吞掉(无人调 .result()),
        # 项目永久卡 queued、前端无限轮询;走 _save_error 落 error 状态才诚实。
        p = store.load(project_id)
        workdir = store.project_dir(project_id)
        # 逐环节解析生效 Settings 与 client(同一 cfg 快照,作业内配置一致;不同环节可用不同端点/模型)。
        settings, clients = resolve_stage_clients(cfg)
        if not p.params.use_hermes_agent:
            # 开关关闭的真实语义:S0/S1 跳过按环节覆盖(用 resolve_settings(None) 只叠全局层),
            # 回退到全局默认 LLM——保留"原始通过 LLM 生成剧本/分镜"的路径,不依赖任何特定 skill。
            # 注意与字段名 use_hermes_agent 的字面义解耦:仅当 hermes 恰配成 s0/s1 stage 覆盖时两者等价。
            for st in ("s0", "s1"):
                settings[st] = resolve_settings(None, cfg)
                clients[st] = _clients(settings[st])
        p.status["pipeline"] = "running"
        p.status["pipeline_started_at"] = _now_iso()
        _locked_save(p)
        s0_start = _mark_step_started(p, "s0", workdir)
        if story is not None:
            p = s0_legend.from_text(p, clients["s0"][0], story)
        else:
            p = s0_legend.run(p, clients["s0"][0])
            if not p.legend_candidates:
                _mark_step_elapsed(p, "s0", s0_start, workdir)
                p.status["pipeline"] = "error: 未检索到可靠传说,请提供自备故事"
                p.status["pipeline_finished_at"] = _now_iso()
                _locked_save(p)
                return
            p.legend = p.legend_candidates[0]
        _mark_step_elapsed(p, "s0", s0_start, workdir)
        _locked_save(p)
        stages = [
            ("s1", lambda: s1_script.run(p, clients["s1"][0],
                                         use_skill=runtime_config.use_master_skill(p, settings["s1"], "s1"))),
            ("s2", lambda: s2_storyboard.run(p, clients["s2"][0],
                                             use_skill=runtime_config.use_master_skill(p, settings["s2"], "s2"))),
            ("s3", lambda: s3_characters.run(p, clients["s3"][0], clients["s3"][1], workdir,
                                             settings["s3"].image_size,
                                             on_progress=lambda: _locked_save(p),
                                             concurrency=image_concurrency(settings["s3"]),
                                             cancel_check=lambda: _is_cancelled(project_id))),
            ("s4", lambda: s4_pages.run(p, clients["s4"][1], workdir, settings["s4"].image_size,
                                        strict=settings["s4"].strict_consistency,
                                        on_progress=lambda: _locked_save(p),
                                        concurrency=image_concurrency(settings["s4"]),
                                        cancel_check=lambda: _is_cancelled(project_id))),
            ("s5", lambda: s5_audio.run(p, clients["s5"][2], settings["s5"].tts_voice, workdir,
                                        clients["s5"][3],
                                        cancel_check=lambda: _is_cancelled(project_id))),
            ("s6", lambda: s6_compose.run(p, workdir)),
        ]
        for _name, fn in stages:
            if _check_cancelled(project_id):  # 协作式取消:环节切换点检查,不打断正在跑的环节
                p.status["pipeline"] = "cancelled"
                p.status["pipeline_finished_at"] = _now_iso()
                _locked_save(p)
                return
            step_start = _mark_step_started(p, _name, workdir)
            fn()
            _mark_step_elapsed(p, _name, step_start, workdir)
            _locked_save(p)
        p.status["pipeline"] = _deliverable_status(p)
        p.status["pipeline_finished_at"] = _now_iso()
        _locked_save(p)
    except Exception as e:  # noqa: BLE001 — 后台线程需兜住任何异常并记录到项目状态
        _save_error(project_id, e)   # 重载干净快照写 error,不落半损坏的内存 p
    finally:
        # 收尾清掉本次作业残留的取消标记:若取消发生在最后一环节执行期间(其后再无
        # _check_cancelled 会读到它),标记会一直留在 _CANCELLED 里,误伤该项目下次重跑。
        with _JOBS_LOCK:
            _CANCELLED.discard(project_id)


# ---------- 序列化:把落盘相对路径转成可访问 URL ----------

def _version_suffix(path: Path) -> str:
    """给存在的文件追加 ?v=<mtime> 做 cache-busting;文件不存在则不加(返回空串)。
    /files 静态挂载不发 Cache-Control,重绘/重排后同名文件会被浏览器缓存挡住不回源。"""
    try:
        return f"?v={int(path.stat().st_mtime)}"
    except OSError:
        return ""


def _file_url(project_id: str, rel: str, workdir: Path | None = None) -> str | None:
    """cell.image/audio、character.turnaround_image 都是相对项目目录的路径。
    传入 workdir 时对存在的文件追加 ?v=<mtime>,避免重绘/重排后旧图被缓存挡住。"""
    if not rel:
        return None
    url = f"/files/{project_id}/{rel}"
    if workdir is not None:
        url += _version_suffix(workdir / rel)
    return url


def _mp4_url(mp4: str) -> str | None:
    """output['mp4'] 形如 'projects/<id>/output/final.mp4',去掉 projects/ 前缀挂到 /files。
    成片重合成后同名,顺带追加 ?v=<mtime> 做 cache-busting。"""
    if not mp4:
        return None
    return "/files/" + mp4.split("projects/", 1)[-1] + _version_suffix(Path(mp4))


def _serialize(p: Project) -> dict:
    workdir = store.project_dir(p.project_id)
    # ⚠️ 这个字典是**逐字段挑选**的,不是 model_dump——给 StoryboardCell 加了字段并不会
    # 自动出现在 API 响应里,必须在这里补一行。而前端的 types.ts 把字段声明成必填,
    # TypeScript 只校验编译期类型、校验不到运行时真实响应,漏了会**完全静默**:
    # `pg.xxx` 恒为 undefined,`undefined > 0` 为 false,那一块 UI 就是永远不渲染。
    # image_gen_ms 就这么漏了(提交 83e7a10 加了字段、写入、前端渲染和类型,唯独没加这里),
    # 「生成 X.Xs」从上线起一次都没显示过。tests/test_api.py 现在有一条锁住键集合的用例。
    pages = [{
        "index": c.index, "caption": c.caption, "emotion": c.emotion,
        "status": c.status, "duration_ms": c.duration_ms, "silent": c.silent,
        "scene_ref": c.scene_ref, "visual_desc": c.visual_desc, "characters": c.characters,
        "image_gen_ms": c.image_gen_ms,
        # 这一页实际走的生成路径与本次指定的 LoRA:只有 "edit" 那条路带 LoRA 节点,
        # "text2img" 的模板没有,所选 LoRA 对那些页静默不生效——前端据此给出提示。
        "image_route": c.image_route, "image_lora": c.image_lora,
        # 这一页生成时缺三视图锚点的角色——一致性无保证,必须让用户看得见。
        "missing_refs": c.missing_refs,
        "image": _file_url(p.project_id, c.image, workdir),
        "audio": _file_url(p.project_id, c.audio, workdir),
        "tracks": {lg: {"caption": t.caption, "duration_ms": t.duration_ms,
                        "silent": t.silent,
                        "audio": _file_url(p.project_id, t.audio, workdir)}
                   for lg, t in c.tracks.items()},
    } for c in p.storyboard]
    characters = [{
        "name": c.name, "role": c.role,
        "image": _file_url(p.project_id, c.turnaround_image, workdir),
        "reference_image": _file_url(p.project_id, c.reference_image, workdir),
    } for c in (p.script.characters if p.script else [])]
    return {
        "project_id": p.project_id,
        "scenic_spot": p.scenic_spot,
        "owner": p.owner,
        "style_preset": p.style_preset,
        "params": p.params.model_dump(),
        "status": p.status,
        "pipeline": p.status.get("pipeline", "pending"),
        "legend": p.legend.model_dump() if p.legend else None,
        # 只给布尔位,原文走 /api/projects/{id}/story 按需拉。原文上限 20000 字,
        # 而详情端点在管线跑动时被前端每 2 秒轮询一次(App.tsx 的 tick),
        # 把它放进这里等于每 2 秒重传一遍 ~60KB,而绝大多数轮询根本没人在看原文。
        "has_story": bool(p.story),
        "script_title": p.script.title if p.script else None,
        "characters": characters,
        "pages": pages,
        "deliverable": p.is_deliverable(),
        # 页维度来自 content_summary(纯模型方法);角色维度要看盘上的文件(参考图/三视图
        # 是否存在)才能算准分母,故在这里算——_serialize 手里有 workdir,模型方法没有。
        "content_summary": {**p.content_summary(),
                            **dict(zip(("characters_imaged", "characters_total"),
                                       s3_characters.turnaround_progress(p, workdir)))},
        "mp4": _mp4_url(p.output.get("mp4", "")),
        "zip": _mp4_url(p.output.get("zip", "")),
        "pdf": _mp4_url(p.output.get("pdf", "")),
        # 附加语种成片:{"en": "/files/..."};没生成过就是空字典
        "track_mp4": {lg: _mp4_url(p.output.get(f"mp4_{lg}", ""))
                      for lg in TRACK_LANGS if p.output.get(f"mp4_{lg}")},
        # 网页播放器用的 WebVTT 外挂字幕:{"zh": "/files/...", "en": ...}。
        # MP4 里那几条 mov_text 内嵌轨浏览器根本不解析,网页显示字幕只能靠 <track> + VTT。
        "subtitles": {lg: _file_url(p.project_id, f"output/final.{lg}.vtt", workdir)
                      for lg in (MAIN_LANG, *TRACK_LANGS)
                      if (workdir / "output" / f"final.{lg}.vtt").exists()},
        # 附加语种成片有**自己一整套**字幕:每页画面停留多久由该成片的配音决定,
        # 中英配音长短不同 → 两条成片的时间轴不同 → 字幕文件必须按成片分开。
        # 拿主片那套挂到英文播放器上,末页字幕会超出片长永远不显示(实测偏差累积到 24 秒)。
        "track_subtitles": {
            tl: {lg: _file_url(p.project_id, f"output/final.{tl}.{lg}.vtt", workdir)
                 for lg in (MAIN_LANG, *TRACK_LANGS)
                 if (workdir / "output" / f"final.{tl}.{lg}.vtt").exists()}
            for tl in TRACK_LANGS if p.output.get(f"mp4_{tl}")},
    }


# ---------- 登录 / 身份 ----------

class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: LoginBody, request: Request) -> dict:
    """校验 bcrypt 口令,成功则写签名 session cookie;失败 401。此端点自身不要求登录。"""
    if not verify_login(body.username, body.password):
        raise HTTPException(401, "用户名或密码错误")
    request.session["user"] = body.username
    return {"username": body.username}


@app.post("/api/logout")
def logout(request: Request) -> dict:
    """清空 session(cookie 随之失效),此端点不要求登录。"""
    request.session.clear()
    return {}


@app.get("/api/me")
def me(user: str = Depends(current_user)) -> dict:
    """已登录返回用户名+管理员标记;未登录经 current_user 抛 401(前端靠状态码判断登录态)。"""
    return {"username": user, "is_admin": is_admin(user)}


# ---------- 接口 ----------

def _editable(project_id: str, user: str) -> Project:
    """编辑端点公共校验+载入,须在持有 _project_lock(project_id) 时调用:此时 job-check 成为
    真正的屏障(锁内确认无未完成作业才改),并载入最新快照,杜绝 check→加锁 的 TOCTOU 与丢更新。
    只读模式拒绝写入;有未完成后台作业拒绝并发编辑;项目须存在;非所有者不可编辑
    (历史项目 owner 为空,视为无主,不做归属限制)。锁序 project→jobs(此处再取 _JOBS_LOCK)。"""
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,禁止编辑")
    f = _job_of(project_id)  # 调用方已持 project 锁,此处再取 _JOBS_LOCK 一致快照(project→jobs)
    if f is not None and not f.done():
        raise HTTPException(409, "该项目有未完成的生成作业,请等待完成后再编辑")
    try:
        p = store.load(project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    if p.owner and p.owner != user:
        raise HTTPException(403, "只能编辑自己的项目")
    return p


class NewProject(BaseModel):
    scenic_spot: str
    minutes: int = 3
    audience: str = "大众"
    tone: str = "温情"
    style: str = "guofeng_ink"
    story: str | None = Field(default=None, max_length=20000)  # 自备故事上限,防超大体喂 LLM
    voice: str = ""
    speed: float = 1.0
    multi_panel: bool = False
    bgm: bool = True
    burn_subtitles: bool = True
    # 命名沿用历史,真实机制见 _pipeline:关闭时 S0/S1 跳过按环节覆盖、回退全局默认 LLM,
    # 而非字面的"是否用编剧大师"——仅当 hermes 恰配成 s0/s1 stage 覆盖时两者才等价。
    use_hermes_agent: bool = True
    master_skill: bool = False   # S1 编剧大师+S2 导演大师深度创作(需对应环节为 hermes-agent 后端)


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
    # 与 s0_legend.from_text 用同一把尺子,但提前到落盘前:否则原文先写进 project.json、
    # 后台线程里才拒绝生成,未过审文本永久留在盘上并经 GET /api/projects/{id} 泄给任意登录用户。
    if body.story:
        hits = find_sensitive(body.story)
        if hits:
            raise HTTPException(400, f"自备故事涉及敏感内容({'、'.join(hits)}),已阻止生成")


@app.post("/api/projects")
def create_project(body: NewProject, user: str = Depends(current_user)) -> dict:
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
        p.owner = user
        p.params.duration_min = body.minutes
        p.params.audience = body.audience
        p.params.tone = body.tone
        p.params.voice = body.voice
        p.params.speed = body.speed
        p.params.multi_panel = body.multi_panel
        p.params.bgm = body.bgm
        p.params.burn_subtitles = body.burn_subtitles
        p.params.use_hermes_agent = body.use_hermes_agent
        p.params.master_skill = body.master_skill
        p.style_preset = body.style
        # 原文落盘:S0 只把它压成 ≤200 字梗概写进 legend.summary,不存这里原文就随栈帧消失
        p.story = body.story
        p.status["pipeline"] = "queued"
        store.save(p)
        _CANCELLED.discard(p.project_id)  # 兜底:清掉上一轮作业可能残留的陈旧取消标记,不污染本次
        _JOBS[p.project_id] = _EXECUTOR.submit(_pipeline, p.project_id, cfg, body.story)
    return {"project_id": p.project_id}


@app.post("/api/projects/{project_id}/cancel")
def cancel_project(project_id: str, user: str = Depends(current_user)) -> dict:
    """取消自己提交的生成任务:排队中未开始执行则直接取消;已在执行则只能协作式标记,
    在下一个环节切换点生效(不能打断正卡在网络调用里的当前环节)。"""
    try:
        p = store.load(project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    if p.owner != user:
        raise HTTPException(403, "只能取消自己的生成任务")
    # 「取 f→判定 done/cancel→标记 _CANCELLED」整段收进 _JOBS_LOCK,与 _pipeline/_run_step finally
    # 里同锁的 _CANCELLED.discard 互斥:否则作业恰在此窗口跑完时,discard 先执行、随后此处 add 让
    # 标记永久残留,污染该项目下次重跑。锁内只决定「直接取消 or 协作式标记」,不做磁盘 IO(锁序
    # 恒为 project→jobs 且 _JOBS_LOCK 内不落盘);直接取消的写盘出锁后在 _project_lock 内做。
    with _JOBS_LOCK:
        f = _JOBS.get(project_id)
        if f is None or f.done():  # 无作业,或作业已跑完(尚未被清理出 _JOBS):都无可取消对象,
            # 若在此处误标记 _CANCELLED,该标记再无人消费(已完成的 _pipeline/_run_step 早已跑过
            # 自己的清理),会一直残留污染该项目下次重跑,故一律拒绝而非静默标记。
            raise HTTPException(400, "该项目当前没有可取消的生成任务")
        cancelled = f.cancel()  # 还在排队没被线程池取走→True 直接取消;已在执行→False 走协作式标记
        if not cancelled:  # 已在执行:标记协作式取消,由 _pipeline/_run_step 在环节切换点消费
            _CANCELLED.add(project_id)
    if cancelled:  # 排队未开始已直接取消:锁外在 _project_lock 内重载最新快照写 cancelled,
        with _project_lock(project_id):  # 不用锁外读的陈旧 p,避免覆盖窗口期内并发编辑端点的改动
            p = store.load(project_id)
            p.status["pipeline"] = "cancelled"
            p.status["pipeline_finished_at"] = _now_iso()
            store.save(p)
        return {"cancelled": True}
    return {"cancelling": True}


@app.get("/api/projects")
def list_projects(user: str = Depends(current_user)) -> list[dict]:
    # PERF:列表端点每次登录/管线跑完都触发,项目多时逐个 store.load(Pydantic 全量校验)会成最慢端点。
    # 这里只输出 5 个字段,故直接读 project.json、json.loads 取所需,跳过全量反序列化;损坏/半写的 json 仍跳过。
    loaded = []
    for meta in store.DEFAULT_ROOT.glob("*/project.json"):
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
            mtime = meta.stat().st_mtime
        except Exception:  # noqa: BLE001 — 跳过损坏/半写的项目,不让列表整体失败
            continue
        if not isinstance(d, dict):  # 合法 JSON 但非对象(null/[]/42/字符串):下面 d.get 会抛,同样跳过
            continue
        item = {
            "project_id": d.get("project_id") or meta.parent.name,
            "scenic_spot": d.get("scenic_spot", ""), "owner": d.get("owner", ""),
            "pipeline": (d.get("status") or {}).get("pipeline", "pending"),
            "mp4": _mp4_url((d.get("output") or {}).get("mp4", "")),
        }
        loaded.append((item, d.get("created_at", ""), mtime))
    # 有 created_at 的项目整体排在前面(新到旧);历史项目(无 created_at)用 mtime 兜底,同样新到旧排在后面
    loaded.sort(key=lambda t: (1 if t[1] else 0, t[1] or t[2]), reverse=True)
    return [item for item, _created, _mtime in loaded]


@app.get("/api/queue")
def get_queue(user: str = Depends(current_user)) -> list[dict]:
    """全局生成队列:基于内存态 _JOBS(无需持久化)实时组装,各自 store.load 拿最新状态。
    顺带清掉已完成(f.done())的作业:否则完成条目会滞留队列(前端带动画、每 3s 轮询读盘),
    直到下次 create/run_step 提交才惰性清理。清理用 items() 快照后再 del,避免并发迭代改写。"""
    with _JOBS_LOCK:
        for done in [k for k, f in list(_JOBS.items()) if f.done()]:
            del _JOBS[done]
        ids = list(_JOBS.keys())
    out = []
    for project_id in ids:
        try:
            p = store.load(project_id)
        except (FileNotFoundError, ValueError):  # 跳过已被删除/损坏的项目
            continue
        out.append({
            "project_id": project_id, "owner": p.owner,
            "scenic_spot": p.scenic_spot, "pipeline": p.status.get("pipeline", "pending"),
        })
    return out


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: str = Depends(current_user)) -> dict:
    try:
        p = store.load(project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    return _serialize(p)


@app.get("/api/projects/{project_id}/story")
def get_project_story(project_id: str, user: str = Depends(current_user)) -> dict:
    """自备故事原文,单独一个端点:用户点开按钮时才拉一次(见 _serialize 里 has_story 的注释)。"""
    try:
        p = store.load(project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, f"项目不存在: {project_id}") from e
    return {"story": p.story}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user: str = Depends(current_user)) -> dict:
    """管理员专用:彻底删除作品目录(project.json + 全部生成产物),不可恢复。
    与其它编辑端点不同,不复用 _editable 的"owner 为空即可编辑"规则——删除权限只看
    is_admin,与作品归属无关(避免无主项目被任意登录用户删除)。"""
    if not is_admin(user):
        raise HTTPException(403, "仅管理员可删除作品")
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,禁止删除")
    with _project_lock(project_id):
        f = _job_of(project_id)
        if f is not None and not f.done():
            raise HTTPException(409, "有生成任务正在进行,无法删除")
        workdir = store.project_dir(project_id)
        if not workdir.is_dir():
            raise HTTPException(404, f"项目不存在: {project_id}")
        shutil.rmtree(workdir)
    return {"deleted": True}


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str, user: str = Depends(current_user)) -> dict:
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
def patch_cell(project_id: str, index: int, body: CellPatch,
               user: str = Depends(current_user)) -> dict:
    if body.characters is not None and len(body.characters) > 50:   # A7:编辑路径同样挡超大 characters
        raise HTTPException(400, "characters 数量上限 50")
    with _project_lock(project_id):
        p = _editable(project_id, user)
        try:
            editing.update_cell(p, index, caption=body.caption, visual_desc=body.visual_desc,
                                emotion=body.emotion, characters=body.characters)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/cells/{index}/redraw")
def redraw_cell(project_id: str, index: int, user: str = Depends(current_user)) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id, user)
        try:
            editing.mark_redraw(p, index)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/cells/{index}/revoice")
def revoice_cell(project_id: str, index: int, user: str = Depends(current_user)) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id, user)
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
def create_cell(project_id: str, body: CellInsert, user: str = Depends(current_user)) -> dict:
    if body.characters is not None and len(body.characters) > 50:
        raise HTTPException(400, "characters 数量超限(≤ 50)")
    with _project_lock(project_id):
        p = _editable(project_id, user)
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
def delete_cell(project_id: str, index: int, user: str = Depends(current_user)) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id, user)
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
def reorder_cells(project_id: str, body: ReorderBody, user: str = Depends(current_user)) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id, user)
        workdir = store.project_dir(project_id)
        try:
            editing.reorder_cells(p, workdir, body.order)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/characters/{name}/redraw")
def redraw_character(project_id: str, name: str, user: str = Depends(current_user)) -> dict:
    with _project_lock(project_id):
        p = _editable(project_id, user)
        try:
            editing.mark_character_redraw(p, name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/characters/{name}/reference")
async def upload_character_reference(project_id: str, name: str, request: Request,
                                     file: UploadFile = File(...),
                                     user: str = Depends(current_user)) -> dict:
    """上传角色参考图:净化后落盘,并标记该角色需重绘(前端随后自动触发 S3)。"""
    # 真正的落盘护栏是 BodySizeLimitMiddleware(在 form 解析之前);这里只是解析后的兜底。
    # 解码+重编码放在取锁之前:纯计算,Pillow 几百毫秒不该占着 per-project 锁
    raw = await uploads.read_limited(file)
    png = uploads.to_reference_png(raw)

    rel = uploads.reference_rel_path()   # 带随机盐,每次上传都是新路径
    with _project_lock(project_id):
        p = _editable(project_id, user)
        # 先按名字精确匹配确认角色存在——匹配成功后才落盘。
        # 路径本身由服务端随机生成、完全不含用户字节,故路径穿越无从谈起;这一步守的是
        # "别给不存在的角色凭空造文件"。
        card = next((c for c in (p.script.characters if p.script else []) if c.name == name), None)
        if card is None:
            raise HTTPException(404, f"角色不存在:{name}")
        old = card.reference_image
        uploads.atomic_write(store.project_dir(project_id) / rel, png)
        card.reference_image = rel
        # 路径带随机盐 → 换图不再覆盖同名文件,旧的必须显式删掉,否则每换一次就留一个孤儿
        if old and old != rel:
            (store.project_dir(project_id) / old).unlink(missing_ok=True)
        # 不清 turnaround_image:清了卡片立刻变"未生成",S3 跑起来之前那段空窗很难看;
        # S3 成功时本来就会覆写同名文件。
        editing.mark_character_redraw(p, name)
        store.save(p)
    return _serialize(p)


@app.delete("/api/projects/{project_id}/characters/{name}/reference")
def delete_character_reference(project_id: str, name: str,
                               user: str = Depends(current_user)) -> dict:
    """删除角色参考图,幂等:本来就没有也返回 200。"""
    with _project_lock(project_id):
        p = _editable(project_id, user)
        card = next((c for c in (p.script.characters if p.script else []) if c.name == name), None)
        if card is None:
            raise HTTPException(404, f"角色不存在:{name}")
        if card.reference_image:
            (store.project_dir(project_id) / card.reference_image).unlink(missing_ok=True)
            card.reference_image = ""
        editing.mark_character_redraw(p, name)
        store.save(p)
    return _serialize(p)


# ---------- 自定义音色(录音 → 音色克隆) ----------

@app.post("/api/voice-samples")
async def upload_voice_sample(file: UploadFile = File(...),
                              user: str = Depends(current_user)) -> dict:
    """上传一段录音,注册成可直接当 voice 用的音色句柄。

    **不绑定任何 project**:录音入口同时在「新建作品表单」(那时还没有 project_id)和
    「作品详情页」,共用这一个端点、这一份存储,只在上传完成后由前端分叉成"建作品"或"改 params"。
    因此这里不取 project 锁,只做 _READONLY 与登录校验。

    顺序是「先注册、后落盘」:注册要打 TTS 后端,失败的概率远高于写本地文件,先做能快速失败、
    不留孤儿。本地这份 wav 保留是为了可回听、可溯源,以及上游 input 目录被清理时能重新注册。"""
    if _READONLY:
        raise HTTPException(403, "公开演示为只读,禁止上传")
    raw = await uploads.read_limited_audio(file)
    wav = uploads.to_voice_sample_wav(raw, file.content_type or "")

    s = resolve_settings("s5")   # 音色归 S5 用,须按 S5 实际生效的 TTS 端点注册
    _l, _i, tts, _m = _clients(s)
    try:
        voice = tts.register_clone_voice(wav)
    except Exception as e:  # noqa: BLE001 —— 上游任何失败都转成用户能看懂的 502
        raise HTTPException(502, f"音色注册失败,TTS 后端不可用:{e}") from e

    rel = uploads.voice_sample_rel_path()
    out = store.voice_sample_dir() / rel
    uploads.atomic_write(out, wav)
    return {"voice": voice, "sample_url": f"/files/{store.VOICE_SAMPLE_DIRNAME}/{rel}",
            "duration_ms": ffmpeg.probe_duration_ms(out)}


class VoiceParams(BaseModel):
    voice: str = Field(default="", max_length=200)


@app.patch("/api/projects/{project_id}/params/voice")
def update_project_voice(project_id: str, body: VoiceParams,
                         user: str = Depends(current_user)) -> dict:
    """换作品的配音音色。**只放 voice 这一个字段**——做成通用的 params 编辑会立刻牵出
    "改了 duration_min 要不要重跑 S2"之类一串问题,不值得。

    换音色 = 所有已生成的配音都念错了嗓子,故走与编辑正文同一套下游作废。"""
    with _project_lock(project_id):
        p = _editable(project_id, user)
        p.params.voice = body.voice
        editing.invalidate_from(p, "s5")
        store.save(p)
    return _serialize(p)


# ---------- 单步重跑(编辑后局部重生成) ----------

_STEP_NAMES = ("s2", "s3", "s4", "s5", "s6")

# 某一步重跑后,**哪些环节的产物真的过期了**——按数据依赖列,不按 _STEP_NAMES 里的位置。
# 关键差别在 s2:它换的是 project.storyboard,而 S3(角色三视图)依赖的是 project.script,
# 剧本没动、三视图仍然有效。按位置级联会把 s3 一起作废,而它的图还在、locked 还是 True,
# 用户真去点 S3 时会被空跑守卫判成没干活 → 那格的历史耗时就此永久丢失。
_INVALIDATES: dict[str, tuple[str, ...]] = {
    "s2": ("s4", "s5", "s6"),
    "s3": ("s4", "s5", "s6"),
    "s4": ("s5", "s6"),
    "s5": ("s6",),
    "s6": (),
}


def _run_step(project_id: str, name: str, cfg: AppConfig, cascade: bool = False) -> None:
    """后台线程跑单步;cascade=True 时连同该步作废的下游一起跑完。

    级联要跑哪几步、与要作废哪几步,共用 _INVALIDATES 同一张表——两处各写一份必然漂移,
    而且会漂出"作废了却不重跑"这种最难查的组合。级联放后端而不是让前端串行提交:
    runStep 是入队语义不是完成语义,前端要串起来就得轮询,还会因为用户关掉标签页而断链。"""
    steps = (name, *_INVALIDATES[name]) if cascade else (name,)
    try:
        # 序言纳入 try(同 _pipeline):store.load/resolve_settings/_clients 抛异常(project.json
        # 损坏、畸形 base_url、ImportError 等)否则会被 Future 静默吞掉,项目永久卡 queued。
        p = store.load(project_id)
        workdir = store.project_dir(project_id)
        p.status["pipeline"] = "running"
        p.status["pipeline_started_at"] = _now_iso()
        _locked_save(p)
        for step_name in steps:
            cancelled, p = _run_one_step(p, project_id, step_name, cfg, workdir)
            if cancelled:
                return          # 状态已由 _run_one_step 写好
        p.status["pipeline"] = _deliverable_status(p)
        p.status["pipeline_finished_at"] = _now_iso()
        _locked_save(p)
    except Exception as e:  # noqa: BLE001 — 后台线程需兜住任何异常并记录到项目状态
        _save_error(project_id, e)   # 重载干净快照写 error,不落半损坏的内存 p
    finally:
        # 同 _pipeline:收尾清掉本次作业残留的取消标记,避免误伤该项目下次重跑。
        with _JOBS_LOCK:
            _CANCELLED.discard(project_id)


def _run_one_step(p: Project, project_id: str, name: str, cfg: AppConfig,
                  workdir: Path) -> tuple[bool, Project]:
    """跑一步并处理它的级联作废。返回 (是否被取消, 新的 project)。

    必须把 project 返回出去:各 step 的 run() 返回的是新对象(`p = sX.run(p, ...)`),
    在函数内重绑局部名的话调用方手里还是旧的那个,级联跑第二步时就会拿着上一步之前的
    快照去跑——这类"看着能跑、结果全错"的 bug 最难查。
    异常照常向上抛给 _run_step 的兜底(不在这里吞)。"""
    s = resolve_settings(name, cfg)  # 该环节生效 Settings + client
    llm, image, tts, music = _clients(s)
    if _check_cancelled(project_id):  # 协作式取消:环节开始执行前检查
        p.status["pipeline"] = "cancelled"
        p.status["pipeline_finished_at"] = _now_iso()
        _locked_save(p)
        return True, p
    step_start = _mark_step_started(p, name, workdir)
    # 清掉该环节自己的陈旧终态(如上次成功的 done):否则重跑期间磁盘上仍是旧值,
    # 前端 currentIdx(非 done/非 partial 才算"当前步")会判定错位,动感显示不到这一格。
    p.status.pop(name, None)
    _locked_save(p)
    if name == "s2":
        p = s2_storyboard.run(p, llm, use_skill=runtime_config.use_master_skill(p, s, "s2"))
    elif name == "s3":
        # 跑之前先记下"谁还没有三视图":跑完凡是从无到有的角色,其出场页必须作废重画,
        # 否则那次补画对已 confirmed 的页完全无效(S4 会幂等跳过它们),用户以为修好了其实没有。
        was_missing = editing.missing_turnarounds(p)
        p = s3_characters.run(p, llm, image, workdir, s.image_size,
                              on_progress=lambda: _locked_save(p),
                              concurrency=image_concurrency(s),
                              cancel_check=lambda: _is_cancelled(project_id))
        gained = was_missing - editing.missing_turnarounds(p)
        hit = editing.invalidate_pages_of_characters(p, gained)
        if hit:
            print(f"补出 {'、'.join(sorted(gained))} 的三视图,已作废其出场的第 "
                  f"{'、'.join(str(i) for i in hit)} 页,重跑 S4 会重画")
    elif name == "s4":
        p = s4_pages.run(p, image, workdir, s.image_size, strict=s.strict_consistency,
                          on_progress=lambda: _locked_save(p),
                          concurrency=image_concurrency(s),
                          cancel_check=lambda: _is_cancelled(project_id))
    elif name == "s5":
        p = s5_audio.run(p, tts, s.tts_voice, workdir, music,
                          cancel_check=lambda: _is_cancelled(project_id))
    elif name == "s6":
        p = s6_compose.run(p, workdir)
    regenerated = _mark_step_elapsed(p, name, step_start, workdir)
    if _check_cancelled(project_id):  # 协作式取消:s3/s4/s5 环节内部提前收尾后在此消费标记,
        # 避免明明是用户主动取消却被 _deliverable_status 判成普通 partial/error,状态失真。
        p.status["pipeline"] = "cancelled"
        p.status["pipeline_finished_at"] = _now_iso()
        _locked_save(p)
        return True, p
    # 本轮一个产物文件都没重写(全被幂等逻辑跳过)时不走级联:什么都没变,下游自然没过期。
    # 这一条顺带修掉一个既有 bug——在已出片的项目上点"重新生成 S4",哪怕一页都没重做,
    # 原先也会无条件 p.output.clear() 把 mp4/zip/pdf 全毁掉,用户白白丢一次成片。
    if regenerated and _INVALIDATES[name]:
        p.output.clear()   # 重跑上游步骤使已合成的 mp4/zip/pdf 失效,清掉避免残留"假成片"
        for step in _INVALIDATES[name]:
            editing.clear_step_keys(p.status, step)
        # 多语种轨也要跟着作废,而且**必须**:S2 换掉 storyboard 后 cell.tracks 全空、
        # 英文译文实质上已经全丢,状态却还写着 track_en=done——实测线上作品正是如此,
        # 用户白跑了 8 分钟的英文轨却收不到任何提示。
        for lg in TRACK_LANGS:
            p.status.pop(f"s5t_{lg}", None)
            p.status.pop(f"s5_{lg}", None)
            editing._invalidate_track_output(p, lg)
        if name == "s2":
            # 分镜被整体换掉 → 逐页产物与成片确定全部过期。必须在 s2 成功之后才删:
            # 它可能抛异常(LLM 返回空分镜),那时旧产物还是用户仅有的东西。
            removed = editing.purge_page_artifacts(workdir)
            print(f"分镜已重生成,清理过期产物 {removed} 个文件(角色三视图保留)")
    _locked_save(p)
    return False, p


@app.post("/api/projects/{project_id}/steps/{name}")
def run_step(project_id: str, name: str, cascade: bool = False,
             user: str = Depends(current_user)) -> JSONResponse:
    """单步重跑(编辑后只需局部重生成,不必整条管线重来一遍)。

    cascade=true 时把该步作废的下游一并跑完(见 _INVALIDATES)。放后端而不是让前端
    串行提交:runStep 是入队语义不是完成语义,前端串起来要轮询、还会因关标签页断链。"""
    if name not in _STEP_NAMES:
        raise HTTPException(400, f"未知步骤: {name}")
    _editable(project_id, user)  # 403/409/404 快速前置校验(返回丢弃,下面锁内重载最新快照)
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
            _CANCELLED.discard(project_id)  # 兜底:清掉上一轮作业可能残留的陈旧取消标记,不污染本次
            _JOBS[project_id] = _EXECUTOR.submit(_run_step, project_id, name, cfg, cascade)
    return JSONResponse({"queued": True}, status_code=202)


def _remux_main_subtitles(p: Project, workdir: Path) -> None:
    """英文轨做完后,把新出的字幕轨也补进**中文版**成片。

    为什么需要:中文版是在英文译文还不存在时合成的,只封了一条中文轨;而生成英文轨只
    重跑 lang="en",不会回头动它。结果就是用户在主成片播放器里怎么也找不到英文字幕。
    这一趟是纯 copy(音视频都不重编码),几秒钟。

    失败必须吞掉:中文版少一条字幕轨是瑕疵,让已经跑完的英文轨整个报错是事故。"""
    mp4 = p.output.get("mp4")
    if not mp4:
        return
    try:
        src = Path(mp4)
        src = src if src.exists() else workdir / src
        if not src.exists():
            return
        out_dir = workdir / "output"
        subs = [(out_dir / f"final.{lg}.srt", s6_compose.SUB_LANG_TAGS.get(lg, lg))
                for lg in (MAIN_LANG, *TRACK_LANGS)
                if (out_dir / f"final.{lg}.srt").exists()]
        if len(subs) < 2:      # 只有主语言一条时无事可做
            return
        staged = out_dir / "final.remux.mp4"
        ffmpeg.sh(ffmpeg.mux_subtitles_cmd(
            src, subs, staged, default_lang=s6_compose.SUB_LANG_TAGS[MAIN_LANG]))
        os.replace(staged, src)
    except Exception as e:  # noqa: BLE001 —— 见 docstring:不能让它拖垮已成功的英文轨
        print(f"⚠️ 中文版字幕重封失败(英文轨不受影响):{e}")


def _run_track(project_id: str, lang: str, cfg: AppConfig) -> None:
    """后台线程产出附加语种轨:翻译 → 该语种配音 → 该语种成片。
    复用 _run_step 的状态写入与异常兜底语义;各环节自身幂等,失败后重点一次即可续跑。"""
    try:
        p = store.load(project_id)
        workdir = store.project_dir(project_id)
        p.status["pipeline"] = "running"
        p.status["pipeline_started_at"] = _now_iso()
        _locked_save(p)
        if _check_cancelled(project_id):
            p.status["pipeline"] = "cancelled"
            p.status["pipeline_finished_at"] = _now_iso()
            _locked_save(p)
            return
        track_start = _mark_step_started(p, f"track_{lang}", workdir)
        p.status.pop(f"track_{lang}", None)
        _locked_save(p)

        s_llm = resolve_settings("s2", cfg)      # 译文属文本环节,沿用 S2 的 LLM 配置
        llm, _image, _tts, _music = _clients(s_llm)
        p = s5t_translate.run(p, llm, lang=lang)
        _locked_save(p)

        s_tts = resolve_settings("s5", cfg)
        _l, _i, tts, _m = _clients(s_tts)
        voice = s_tts.tts_voice_en or s_tts.tts_voice
        p = s5_audio.run(p, tts, voice, workdir,
                         cancel_check=lambda: _is_cancelled(project_id), lang=lang)
        _locked_save(p)

        p = s6_compose.run(p, workdir, lang=lang)
        _remux_main_subtitles(p, workdir)
        _mark_step_elapsed(p, f"track_{lang}", track_start, workdir)
        p.status[f"track_{lang}"] = "done" if p.output.get(f"mp4_{lang}") else "partial"
        _locked_save(p)
        if _check_cancelled(project_id):
            p.status["pipeline"] = "cancelled"
        else:
            p.status["pipeline"] = _deliverable_status(p)
        p.status["pipeline_finished_at"] = _now_iso()
        _locked_save(p)
    except Exception as e:  # noqa: BLE001 — 后台线程需兜住任何异常并记录到项目状态
        _save_error(project_id, e)
    finally:
        with _JOBS_LOCK:
            _CANCELLED.discard(project_id)


@app.post("/api/projects/{project_id}/tracks/{lang}")
def run_track(project_id: str, lang: str, user: str = Depends(current_user)) -> JSONResponse:
    """生成附加语种轨(翻译 + 配音 + 成片)。排队/背压/互斥与 run_step 完全同款。"""
    if lang not in TRACK_LANGS:
        raise HTTPException(400, f"未知语种: {lang}")
    _editable(project_id, user)
    Settings()
    cfg = load_overrides()
    with _project_lock(project_id):
        with _JOBS_LOCK:
            for done in [k for k, f in list(_JOBS.items()) if f.done()]:
                del _JOBS[done]
            if project_id in _JOBS:
                raise HTTPException(409, "该项目有未完成的生成作业,请等待完成后再编辑")
            if len(_JOBS) >= MAX_PENDING:
                raise HTTPException(429, f"生成队列已满(上限 {MAX_PENDING}),请稍后再试")
            p = store.load(project_id)
            if not p.storyboard:
                raise HTTPException(400, "请先完成分镜与配图")
            p.status["pipeline"] = "queued"
            store.save(p)
            _CANCELLED.discard(project_id)
            _JOBS[project_id] = _EXECUTOR.submit(_run_track, project_id, lang, cfg)
    return JSONResponse({"queued": True}, status_code=202)


class TrackPatch(BaseModel):
    caption: str = Field(max_length=240)   # 与 schema.LocalizedTrack.caption 同上限


@app.patch("/api/projects/{project_id}/cells/{index}/tracks/{lang}")
def patch_cell_track(project_id: str, index: int, lang: str, body: TrackPatch,
                     user: str = Depends(current_user)) -> dict:
    """人工校对译文。改了文本就作废该页该语种的旧配音与该语种成片——旧音频念的是旧译文。"""
    if lang not in TRACK_LANGS:
        raise HTTPException(400, f"未知语种: {lang}")
    with _project_lock(project_id):
        p = _editable(project_id, user)
        try:
            editing.update_track_caption(p, index, lang, body.caption)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.post("/api/projects/{project_id}/cells/{index}/tracks/{lang}/revoice")
def revoice_cell_track(project_id: str, index: int, lang: str,
                       user: str = Depends(current_user)) -> dict:
    """标记单页该语种需重配音(清掉该页该语种音频与该语种成片)。
    与主语言的 revoice 一样,只做标记;触发合成由前端紧接着调 /tracks/{lang} 完成。"""
    if lang not in TRACK_LANGS:
        raise HTTPException(400, f"未知语种: {lang}")
    with _project_lock(project_id):
        p = _editable(project_id, user)
        try:
            editing.mark_track_revoice(p, index, lang)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store.save(p)
    return _serialize(p)


@app.get("/api/version")
def version() -> dict:
    """当前部署的构建标识。**刻意免鉴权**,两个理由:
    ①部署脚本要靠 curl 它自证"线上正在跑的进程"确实是刚传上去的那一版——原先
      docs/ops-dgx.md 的验证只有 curl -w '%{http_code}',证明不了任何事;
    ②前端要在登录页之前就拿到它(App.tsx 的 `if (!user) return <LoginPage/>` 是提前返回)。
    泄露面仅一个 commit id,内网+隧道的内部工具,不是凭据。

    ⚠️ 位置有讲究:必须声明在文件末尾那个 app.mount("/", StaticFiles(html=True)) **之前**,
    那是兜底 catch-all,写在它后面的路由永远命中不到。"""
    return build_info()


@app.get("/api/meta")
def meta(user: str = Depends(current_user)) -> dict:
    """前端建项目表单用的枚举选项。"""
    return {
        "minutes": list(_MINUTES), "audiences": list(_AUDIENCES),
        "tones": list(_TONES), "styles": list(STYLE_PRESETS),
        # 音色列表须跟随 S5 实际生效的 TTS 后端:S5 用 resolve_settings("s5") 的端点合成,
        # 若这里只解析全局层,用户把 s5 覆盖成本地 CosyVoice 后表单仍列全局音色、选中即令 S5 请求全失败降级静音。
        "voices": resolve_settings("s5").tts_voices_list,
        "loras": list(LORA_PRESETS),
        # 可产出的附加语种轨,如 ["en"];前端据此渲染"生成英文版"这类入口,不硬编码语种
        "track_langs": list(TRACK_LANGS),
        "readonly": _READONLY,
    }


# ---------- 端点/模型配置(全局默认 + 按环节覆盖) ----------
# 合并/脱敏/视图契约集中在 runtime_config(与模型同处、CLI 亦可复用);此处仅 HTTP 薄封装。

@app.get("/api/config")
def read_config(user: str = Depends(current_user)) -> dict:
    return config_view(_READONLY)


@app.put("/api/config")
def write_config(body: AppConfig, user: str = Depends(current_user)) -> dict:
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
    变体在此统一 404。受保护的配置文件名取运行时实际值 runtime_config._config_path()(每次读 SHANHAI_CONFIG_PATH、
    随之变化、可被测试改环境变量),而非 import 期冻结的常量。config.json 默认在 cwd 根、本不在挂载目录内,
    此拦截是运维误把它指进 projects/ 时的防御纵深(真正的护栏仍是把 config.json 留在被托管目录之外)。"""

    async def get_response(self, path: str, scope):  # noqa: ANN001 — 与父类签名一致
        protected = {"project.json", runtime_config._config_path().name.lower()}
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
