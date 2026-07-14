# 多用户 + 按后端类型分级并发设计

- **日期**:2026-07-14
- **状态**:已与用户确认设计,待写实现计划。
- **背景**:DGX 部署给团队共用后,`shanhai-web` 是单进程单 worker(`_EXECUTOR = ThreadPoolExecutor(max_workers=1)`),所有生成任务全局串行,且完全没有身份概念——本次会话里反复出现的"不明来源任务"排查(`stepid` 系列事件)侧面暴露了"看不清谁在跑什么"这个真实痛点。用户想加:①团队内固定成员登录 ②归属可见但操作受限(能看全部作品,只能取消自己的生成,能看整个队列)③按后端类型分级并发(云端可并行,本地 Spark/DGX 模型全局单并发,因为 Ollama 与 ComfyUI 共享同一张物理 GPU,已在本次会话实测到二者抢卡导致 LLM 调用从数十秒拖到近超时的现象)。

## 现状基线(已核实,设计以此为准)

- `src/shanhai/api.py:44` `_READONLY`、`:52` `MAX_PENDING = 8`、`:53` `_EXECUTOR = ThreadPoolExecutor(max_workers=1)`、`:54` `_JOBS: dict[str, Future]`、`:62` `_JOBS_LOCK` —— 全部是进程内全局单例,无用户概念,重启即清零(`reconcile_zombie_jobs` 专门处理这个）。
- `src/shanhai/providers/_http.py` 的 `request_with_retry` 是 llm/image/tts/music **四个 provider 全部网络调用的唯一收口点**(`llm.py:24`、`image.py:62/70/82/112`、`tts.py:21`、`music.py:33` 均经此函数)。
- `src/shanhai/config.py:50-64` 的 `llm_endpoint`/`image_endpoint`/`tts_endpoint`/`music_endpoint` 四个 property,各自返回 `(base_url, api_key)`,是每个 provider 构造时拿到的最终端点。
- `src/shanhai/schema.py:75-86` `Project` 模型当前 10 个字段,**无 owner/user 相关字段**。
- `web/src/api.ts` **没有统一的 fetch 封装**,14 个接口调用点各自裸调 `fetch()`,均未带 `Authorization`/`Cookie`。
- `pyproject.toml` 当前依赖:`httpx/pydantic/pydantic-settings/python-dotenv/typer/pillow/fastapi/uvicorn`——**无密码哈希库、无 session/JWT 库、无数据库库**。
- DGX 部署是单实例(`shanhai-web.service`),同一进程同时服务内网直连和 cpolar 公网隧道,无反向代理身份注入层。

## 全局约束

- 十人以内团队固定成员,后台预先配好账号,**不做自助注册/密码找回**。
- 不引入数据库(见"项 4"的结论与理由)。
- 现有 `_READONLY` 全局只读开关保持不变、独立于本次的用户体系(公网只读场景不受影响)。
- 每项改动配单测;`src/` 保持 ruff-clean。

---

## 项 1 · 登录与身份(Cookie session,新增 `src/shanhai/auth.py`)

**目标**:团队成员用用户名+密码登录,后续请求携带身份,不用改前端 14 个 fetch 调用点。

**为什么是 Cookie session 不是 Bearer token**:`web/src/api.ts` 没有统一 fetch 封装,Bearer 方案要逐个改 14 处调用点加 header;Cookie 是浏览器自动随同源请求携带,前端几乎不用动(仅需给 `fetch` 加 `credentials: 'same-origin'`,同源场景其实默认已含,视浏览器而定,需在实现阶段验证)。

**账号存储**:新建 `users.json`(项目根,不入库,和 `config.json` 同一 gitignore 待遇),结构:
```json
{"users": [{"username": "wuzi", "password_hash": "$2b$..."}, ...]}
```
- 新增依赖 `bcrypt`(哈希)、`itsdangerous`(Starlette `SessionMiddleware` 依赖的签名 cookie)。
- 用 `store.py` 已有的原子写模式(唯一临时名 + `os.replace`)读写 `users.json`,不新建存储抽象。
- 提供一个一次性 CLI 子命令或脚本供管理员建账号(如 `shanhai adduser <name>`,交互输入密码后 bcrypt 哈希落盘)——**不做自助注册页面**。

**登录端点**:
- `POST /api/login`(body: `username`/`password`)→ 校验 bcrypt → 成功则 `request.session["user"] = username`(Starlette `SessionMiddleware` 已处理签名 cookie 的下发/校验,无需服务端 session 表)→ 返回 200。
- `POST /api/logout` → `request.session.clear()`。
- `GET /api/me` → 返回当前登录用户名(前端用来判断登录态、决定是否跳转登录页)。

**鉴权依赖**:新增 `src/shanhai/auth.py` 的 `current_user(request: Request) -> str`(FastAPI `Depends`),未登录抛 `HTTPException(401)`。**除 `POST /api/login`、`GET /api/logout` 和静态资源(`/`、`/files/*`)外,其余全部端点(含 `GET /api/meta`)都要求登录**——整个 SPA 进门先登录,不做"匿名可看列表"的折中。前端 `App.tsx` 顶层先 `GET /api/me` 判断登录态,未登录直接渲染 `LoginPage`,不发起其它任何 API 请求。

**前端**:新增 `web/src/components/LoginPage.tsx`(用户名密码表单,复用 `NewProjectForm.tsx` 的卡片/field/label 样式);`App.tsx` 顶层先 `GET /api/me` 判断登录态,未登录渲染 `LoginPage`;header 加"当前用户 + 退出"按钮。

**测试**:`tests/test_auth.py`(新建)——bcrypt 校验成功/失败、session cookie 下发后带 cookie 请求受保护端点通过、不带 cookie 返回 401、logout 后原 cookie 失效。

---

## 项 2 · 归属、队列可见性与"取消自己的生成"

**目标**:所有人看到全部作品;新建作品记录创建者;能看到全局生成队列;只能取消自己提交的任务。

**Schema**:`src/shanhai/schema.py` 的 `Project` 加一个字段:
```python
owner: str = ""   # 建作品时的登录用户名;历史项目(改造前所建)留空,前端显示"未知"
```
`api.py` 的 `create_project`(现约 238 行)从 `current_user` 依赖拿到用户名,建 `Project` 时填入 `owner`。

**队列可见性**:新增 `GET /api/queue`,基于现有的 `_JOBS`(内存态,无需持久化)实时组装:
```python
[{"project_id": ..., "owner": ..., "pipeline": ..., "scenic_spot": ...}, ...]
```
遍历 `_JOBS` 键(project_id),各自 `store.load` 拿 `owner`/`scenic_spot`/`status.pipeline` 拼装返回。前端新增一个"生成队列"面板(简单列表,复用现有卡片样式),不需要新的持久化——`_JOBS` 本身已经是"当前在跑/排队什么"的权威状态。

**取消端点**:新增 `POST /api/projects/{id}/cancel`。
- 鉴权:`store.load(id).owner != current_user` → `403`。
- 若 `_JOBS[id]` 尚未开始执行(`Future.cancel()` 返回 `True`,即还在排队没被线程池取走)→ 直接取消成功,项目状态改回可编辑态。
- 若已经在跑 → **协作式取消**(见下),不能真正抢占正在执行的同步调用。

**协作式取消的技术边界(如实写入 spec,不回避)**:Python `ThreadPoolExecutor` 的 `Future.cancel()` 对已开始运行的任务无效——不能强行打断一个正卡在 `httpx` 阻塞调用里的线程。方案:在 `_pipeline`/`_run_step` 的环节循环之间(S0→S1→S2...的每次切换点)检查一个共享的"取消标记"(如 `_CANCELLED: set[str]`,`project_id` 命中则在下一个环节开始前提前返回、把 `status["pipeline"]` 置为 `"cancelled"`),而不是在单次 LLM/图像调用内部检查。**用户点"取消"后,如果当前正卡在某一步的网络调用里,要等这一步返回(可能是几十秒到超时时长)才会真正停下**——这是当前同步阻塞式 provider 调用架构下的固有限制,不在本次范围内改成真正的可抢占式(那需要把 provider 调用改成可中断的异步/协程,是更大的重构)。

**测试**:`tests/test_api.py` 扩展——非 owner 调用 `/cancel` 返回 403;owner 调用排队中的任务成功取消;`GET /api/queue` 正确反映 `_JOBS` 内容与 `owner`。

---

## 项 3 · 按后端类型分级并发(`providers/_http.py` 加全局锁 + `_EXECUTOR` 放开并发)

**目标**:多个云端环节能真并行跑;任何解析到本地 Spark(loopback)的调用全局单并发,不管哪个环节哪个用户。

**判断"是不是本地 Spark"**:不新增配置项,直接用已解析出的 `base_url` 判断 host:
```python
# providers/_http.py 新增
from urllib.parse import urlparse
import threading
from contextlib import contextmanager

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_local_lock = threading.Lock()

def is_local_endpoint(base_url: str) -> bool:
    return urlparse(base_url).hostname in _LOCAL_HOSTS

@contextmanager
def local_backend_guard(base_url: str):
    """本地 Spark 后端全局单并发:GPU 物理共享(Ollama/ComfyUI/CosyVoice2/ACE-Step 同卡),
    跨环节跨用户排队,避免争抢显存导致的推理拖慢(见 2026-07-13 DGX 实测:并发命中同卡时
    LLM 调用从数十秒拖到接近 900s 超时)。云端 base_url 不受影响,直接放行。"""
    if is_local_endpoint(base_url):
        with _local_lock:
            yield
    else:
        yield
```
这个 DGX 上 `127.0.0.1:11434`(Ollama)/`:8091`(image-shim→ComfyUI)/`:8090`(tts_shim→CosyVoice2)/`:8092`(music-shim→ACE-Step)全部命中 `127.0.0.1`,自动落入同一把锁,**不需要为每个 shim 单独打标**。云端 provider(如 tu-zi)的 `https://` 地址天然不匹配,不受影响。

**接入点**:四个 provider(`llm.py`/`image.py`/`tts.py`/`music.py`)在各自发起请求的方法里(`chat`/`generate`/`synthesize`/`generate`),用 `with local_backend_guard(self._base_url):` 包住原有的 `request_with_retry(...)` 调用。需要各 provider 的 `__init__` 保存一份 `self._base_url`(当前部分 provider 只保存了 `httpx.Client`,base_url 被吞进 client 内部,需要补存一份原始字符串供 `is_local_endpoint` 判断)。

**放开执行器并发**:`api.py:53` 的 `_EXECUTOR = ThreadPoolExecutor(max_workers=1)` 改成有意义的并发数(建议 `max_workers=4`,可后续按 `MAX_PENDING=8` 的一半估算,具体数字实现阶段可调)。放开后多个 project 的 pipeline 能同时占用线程,云端环节真并行;一旦某个线程的当前环节命中本地 Spark 端点,会在 `local_backend_guard` 上排队等,只卡这一步,不卡该 project 其它已完成环节或其它 project 的云端环节。

**测试**:`tests/test_local_backend_guard.py`(新建)——`is_local_endpoint` 对 `127.0.0.1`/`localhost`/云端 URL 的判定;两个线程并发调用 `local_backend_guard("http://127.0.0.1:...")` 时确认互斥(用一个共享计数器 + `time.sleep` 断言同一时刻只有一个进入临界区);`local_backend_guard("https://...")` 不阻塞(两个线程能同时进入)。

---

## 项 4 · 数据库:结论是不引入

拆解完前三项后,归属(`Project.owner` 字段,存 `project.json`)、认证(`users.json` + bcrypt)、队列(`_JOBS` 内存实时扫描)全部不需要真正的数据库,和项目一贯的"扁平文件存储、拒绝过早引入 DB"哲学一致。

**唯一 SQLite 会有意义的场景(本次不做,记录以备将来考虑)**:如果未来需要**跨重启的任务历史/审计**(比如"上周谁跑了什么、失败率多少""谁在什么时候取消过某个任务"这类查询)——现状 `_JOBS` 一重启就清空,`project.json` 也不记录操作日志。这个需求目前没有被提出,不在本次范围内。

---

## 验证

1. `uv run pytest -q` 全量回归 + 上述三个新测试文件全绿。
2. 本地起服务:未登录访问受保护接口返回 401;登录后正常使用;用两个账号分别建作品,确认列表都能看到彼此的、但只能取消自己的。
3. 并发验证(可选,需要真实两个云端可并行的环节 + 一个本地 Spark 环节同时触发,构造较复杂,实现阶段视时间决定是否做真实端到端验证,或仅靠 `test_local_backend_guard.py` 的互斥单测覆盖)。
4. DGX 部署前需要新增 `users.json` 初始账号(用新增的 CLI 命令为团队成员逐个建号),同时确认 `.gitignore` 已覆盖 `users.json`(含密码哈希,不能入库)。
