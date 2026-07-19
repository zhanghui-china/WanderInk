# WanderInk Web 技术白皮书

> 版本: v2.2  
> 更新: 2026-07-20  
> 项目: WanderInk · 漫游墨绘

---

## 一、系统架构总览

### 1.1 架构设计理念

WanderInk Web 系统采用 **"Supervisor + 顺序管线 + 可插拔 Provider"** 的三层架构设计：

```
┌─────────────────────────────────────────────────────────────┐
│                        用户层 (Web)                          │
│  React 18 + Vite 5 + Tailwind CSS + TypeScript              │
│  登录认证 / 项目管理 / 实时进度 / 可视化编辑                   │
├─────────────────────────────────────────────────────────────┤
│                       编排层 (API)                           │
│  FastAPI + Pydantic + ThreadPoolExecutor                    │
│  S0–S6 七步管线 / 后台线程执行 / 断点续跑 / 协作式取消        │
├─────────────────────────────────────────────────────────────┤
│                       数据层 (Project)                        │
│  聚合根模式 / project.json 单源事实 / 原子写落盘              │
├─────────────────────────────────────────────────────────────┤
│                       Provider 层                            │
│  LLM / Image / TTS / Music 四大 Provider                    │
│  OpenAI 兼容协议 / 本地后端全局单并发锁                       │
├─────────────────────────────────────────────────────────────┤
│                       模型服务层                             │
│  Hermes Agent / ComfyUI / Qwen-TTS / ACE-STEP               │
│  本地 vLLM / Ollama / 云端 StepFun 模型                     │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 核心设计原则

| 原则 | 说明 |
|---|---|
| **单一聚合根** | 所有中间状态（传说、剧本、分镜、角色、页面、成片）都附着于同一个 `Project` 对象，序列化到 `project.json` |
| **CLI / HTTP 双入口** | `shanhai` (Typer CLI) 和 `shanhai-web` (FastAPI) 复用同一套 `steps/*` 和 Provider 层 |
| **OpenAI 兼容协议** | 四大 Provider 均遵循 OpenAI 兼容协议，支持通过 `.env` 或 Web 配置面板切换端点和模型 |
| **后台线程执行** | 生成任务耗时数分钟，通过 `ThreadPoolExecutor` 在后台线程执行，前端轮询获取进度 |
| **断点续跑** | 基于 `project.json` 的断点续跑机制，只填充缺失部分，不重做已完成工作 |

---

## 二、后端技术栈

### 2.1 技术选型

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | ≥3.12 | 语言基础 |
| FastAPI | ≥0.111 | HTTP API 框架 |
| Pydantic | ≥2.7 | 数据验证与序列化 |
| Pydantic Settings | ≥2.3 | 环境配置管理 |
| Typer | ≥0.12 | CLI 命令行工具 |
| httpx | ≥0.27 | HTTP 客户端（支持重试） |
| Pillow | ≥10.3 | 图像处理 |
| Uvicorn | ≥0.30 | ASGI 服务器 |
| bcrypt | ≥4.0 | 口令哈希 |
| itsdangerous | ≥2.0 | SessionMiddleware 签名 Cookie |

### 2.2 目录结构

```
src/shanhai/
├── api.py              # FastAPI 主入口，HTTP 薄封装
├── cli.py              # Typer CLI 入口
├── config.py           # Settings 配置类（.env 基线）
├── runtime_config.py   # 运行时配置覆盖（全局默认 + 按环节覆盖）
├── schema.py           # Pydantic 数据模型（Project/Legend/Script/...）
├── store.py            # 持久化层（create/load/save/atomic_write_text）
├── auth.py             # 认证模块（bcrypt 口令校验）
├── editing.py          # 编辑操作（更新/插入/删除/重排序单元格）
├── export.py           # 导出模块（PDF/ZIP 生成）
├── ffmpeg.py           # FFmpeg 命令构造
├── loras.py            # LoRA 预设配置
├── styles.py           # 画风预设配置
├── paneling.py         # 多格漫画布局
├── typeset.py          # 字幕排版
├── safety.py           # AI 合规检查
├── providers/          # Provider 层
│   ├── __init__.py
│   ├── _http.py        # 共享重试策略 + 本地后端全局单并发锁
│   ├── llm.py          # LLM Provider（OpenAI 兼容）
│   ├── llm_ollama.py   # Ollama LLM Provider
│   ├── image.py        # 图像生成 Provider
│   ├── tts.py          # TTS Provider
│   └── music.py        # 音乐生成 Provider
└── steps/              # S0–S6 七步管线
    ├── s0_legend.py    # 传说检索
    ├── s1_script.py    # 剧本改编
    ├── s2_storyboard.py # 分镜设计
    ├── s3_characters.py # 角色设定
    ├── s4_pages.py     # 漫画页生成
    ├── s5_audio.py     # 配音与音乐
    └── s6_compose.py   # 合成输出
```

---

## 三、前端技术栈

### 3.1 技术选型

| 组件 | 版本 | 用途 |
|---|---|---|
| React | ^18.3.1 | UI 框架 |
| React DOM | ^18.3.1 | DOM 渲染 |
| TypeScript | ^5.5.3 | 类型系统 |
| Vite | ^5.3.4 | 构建工具 |
| Tailwind CSS | ^3.4.6 | CSS 框架 |
| Bun | latest | 包管理器 |

### 3.2 目录结构

```
web/web/src/
├── main.tsx            # 应用入口
├── App.tsx             # 主组件（路由/布局）
├── api.ts              # HTTP API 封装（统一错误处理）
├── types.ts            # TypeScript 类型定义（与后端 schema 对应）
├── stages.ts           # 管线阶段定义
├── styles.ts           # 样式常量
├── index.css           # Tailwind CSS 入口
├── components/         # UI 组件
│   ├── LoginPage.tsx          # 登录页
│   ├── NewProjectForm.tsx     # 新建项目表单
│   ├── ProjectList.tsx        # 项目列表
│   ├── ProjectDetail.tsx      # 项目详情（核心组件）
│   ├── ProgressSteps.tsx      # 进度步骤条
│   ├── GeneratingBars.tsx     # 生成动画条
│   ├── ImageLightbox.tsx      # 图片灯箱
│   ├── CharacterRedrawDialog.tsx # 角色重绘对话框
│   ├── QueuePanel.tsx         # 队列面板
│   ├── SettingsPanel.tsx      # 配置面板
│   ├── ScenicSpotPicker.tsx   # 景区选择器
│   └── decor.tsx              # 装饰组件（卡片头/印章/边框）
└── data/
    └── scenicSpots.ts         # 5A 景区数据
```

---

## 四、S0–S6 管线设计

### 4.1 管线流程

系统将景区故事创作分解为 **S0–S6 七个顺序步骤**，覆盖五种模态：文本、图像、语音、音乐、视频：

| 步骤 | 名称 | 输出 | 依赖 Client |
|---|---|---|---|
| S0 | LEGEND 传说检索 | 2–5 个候选传说 | LLM |
| S1 | SCRIPT 剧本改编 | 结构化剧本（幕/场/对话/旁白） | LLM（Hermes 编剧大师） |
| S2 | BOARD 分镜设计 | 逐页分镜表（视觉描述/情绪/文本） | LLM（Hermes 导演大师） |
| S3 | ROLE 角色设定 | 角色卡 + 前三视图 | LLM + Image |
| S4 | PAGES 漫画页生成 | 逐页 1920×1080 视觉 | Image |
| S5 | VOICE 配音与音乐 | 逐页旁白音频 + BGM | TTS + Music |
| S6 | FILM 合成输出 | MP4（字幕/水印/片尾） | FFmpeg |

### 4.2 管线执行机制

#### 后台线程执行

`api.py` 使用 `ThreadPoolExecutor(max_workers=4)` 将管线任务提交到后台线程：

```python
_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_JOBS: dict[str, Future] = {}

def _pipeline(project_id: str, cfg: AppConfig, story: str | None) -> None:
    """在后台线程里从 S0 一路跑到 MP4"""
    p = store.load(project_id)
    settings, clients = resolve_stage_clients(cfg)
    # S0 传说检索
    p = s0_legend.run(p, clients["s0"][0])
    # S1–S6 循环执行
    stages = [
        ("s1", lambda: s1_script.run(...)),
        ("s2", lambda: s2_storyboard.run(...)),
        ("s3", lambda: s3_characters.run(...)),
        ("s4", lambda: s4_pages.run(...)),
        ("s5", lambda: s5_audio.run(...)),
        ("s6", lambda: s6_compose.run(...)),
    ]
    for name, fn in stages:
        fn()
        _locked_save(p)
```

#### 进度轮询

前端每 3 秒轮询 `GET /api/projects/{id}` 获取最新状态，进度直接读取 `project.status`（每步 `store.save` 落盘）。

#### 协作式取消

取消操作采用协作式标记，不在当前环节内部打断，而是在下一个环节切换点生效：

```python
_CANCELLED: set[str] = set()

def _check_cancelled(project_id: str) -> bool:
    """消费取消标记（命中即移除，不重复触发）"""
    with _JOBS_LOCK:
        if project_id in _CANCELLED:
            _CANCELLED.discard(project_id)
            return True
        return False
```

---

## 五、Provider 层设计

### 5.1 OpenAI 兼容协议

四大 Provider（LLM / Image / TTS / Music）均遵循 OpenAI 兼容协议：

```python
def _clients(s: Settings) -> tuple[LLMClient, ImageClient, TTSClient, MusicClient]:
    llm_base, llm_key = s.llm_endpoint
    img_base, img_key = s.image_endpoint
    tts_base, tts_key = s.tts_endpoint
    music_base, music_key = s.music_endpoint
    return (
        LLMClient(llm_base, llm_key, s.llm_model, timeout=s.llm_timeout),
        ImageClient(img_base, img_key, s.image_model, s.image_api_mode, ...),
        TTSClient(tts_base, tts_key, s.tts_model),
        MusicClient(music_base, music_key, s.music_model),
    )
```

### 5.2 本地后端全局单并发锁

`providers/_http.py` 的 `local_backend_guard` 实现本地 GPU 资源的全局单并发保护：

```python
_local_lock = threading.Lock()

@contextmanager
def local_backend_guard(base_url: str):
    """本地 Spark 后端全局单并发:GPU 物理共享,跨环节跨用户排队"""
    if is_local_endpoint(base_url):
        with _local_lock:
            yield
    else:
        yield
```

**设计意图**：DGX Spark 上 Ollama/ComfyUI/Qwen-TTS/ACE-Step 共享单张 GPU，并发请求会争抢显存导致推理拖慢甚至超时（实测并发命中同卡时 LLM 调用从数十秒拖到近 900s）。

### 5.3 重试策略

`request_with_retry` 实现统一的重试逻辑：

| 可重试错误 | 处理方式 |
|---|---|
| `httpx.TransportError` | 连接阶段错误（ConnectError/ConnectTimeout/PoolTimeout）始终重试；读取阶段错误仅幂等请求重试 |
| 瞬时状态码（429/500/502/503/504） | 始终重试 |
| 其他错误 | 不重试，原样抛出 |

```python
def request_with_retry(do_request, retries, *, idempotent=True, base_url=None):
    for attempt in range(retries + 1):
        try:
            with local_backend_guard(base_url) if base_url else nullcontext():
                r = do_request()
        except httpx.TransportError as e:
            connect_phase = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
            if attempt == retries or not (idempotent or connect_phase):
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in TRANSIENT_STATUS and attempt < retries:
            time.sleep(2 * (attempt + 1))
            continue
        return r
```

---

## 六、并发模型

### 6.1 两级锁设计

系统采用**两级锁**机制，层级单向、临界区互不重叠，避免死锁：

```
┌─────────────────────────────────────────────────────────────┐
│  _JOBS_LOCK (全局)                                          │
│  ├── 保护 _JOBS 的清理、背压检查、提交                       │
│  └── 保护 _CANCELLED 的读写                                 │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  _PROJECT_LOCKS[project_id] (按项目)                        │
│  ├── 保护单项目「load→改→save」全程                         │
│  └── 防写者互相丢更新                                       │
└─────────────────────────────────────────────────────────────┘
```

**锁序规则**：恒为 `project→jobs`，即先获取项目锁，再获取作业锁，从不反向。

### 6.2 背压机制

系统设置 `MAX_PENDING=8` 作为未完成作业上限，超出则拒绝新建：

```python
MAX_PENDING = 8  # 未完成作业上限

@app.post("/api/projects")
def create_project(body: NewProject, user: str = Depends(current_user)):
    with _JOBS_LOCK:
        # 清理已完成作业
        for done in [k for k, f in list(_JOBS.items()) if f.done()]:
            del _JOBS[done]
        # 背压检查
        if len(_JOBS) >= MAX_PENDING:
            raise HTTPException(429, f"生成队列已满(上限 {MAX_PENDING}),请稍后再试")
        # 创建项目并提交后台任务
        _JOBS[p.project_id] = _EXECUTOR.submit(_pipeline, p.project_id, cfg, body.story)
```

### 6.3 单步重跑

支持编辑后局部重生成，不必整条管线重来：

```python
_STEP_NAMES = ("s2", "s3", "s4", "s5", "s6")

def _run_step(project_id: str, name: str, cfg: AppConfig) -> None:
    """后台线程跑单步"""
    if name == "s2":
        p = s2_storyboard.run(p, llm, use_skill=...)
    elif name == "s3":
        p = s3_characters.run(p, llm, image, workdir, ...)
    # ...
    if name != "s6":
        p.output.clear()  # 重跑上游使已合成产物失效
        # 级联清理下游环节状态
        idx = _STEP_NAMES.index(name)
        for step in _STEP_NAMES[idx + 1:]:
            for key in (step, f"{step}_started_at", f"{step}_elapsed_s"):
                p.status.pop(key, None)
```

---

## 七、数据持久化

### 7.1 原子写机制

`store.py` 的 `atomic_write_text` 保证写入的原子性：

```python
def atomic_write_text(path: Path, text: str) -> None:
    """先写唯一临时名再 os.replace 发布"""
    tmp = path.parent / f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
```

**设计意图**：多线程并发写同一路径时各写各的临时文件，读者永远只见完整的旧文件或新文件，避免撕裂写（torn write）。

### 7.2 聚合根模式

`Project` 作为唯一聚合根，包含所有中间状态：

```python
class Project(BaseModel):
    project_id: str
    scenic_spot: str
    owner: str = ""
    created_at: str = ""
    params: GenerationParams
    status: dict[str, str]
    legend_candidates: list[Legend]
    legend: Legend | None
    script: Script | None
    style_preset: str
    storyboard: list[StoryboardCell]
    bgm: str = ""
    output: dict[str, str]
```

### 7.3 断点续跑

基于 `project.json` 的断点续跑机制：

1. 每步执行完成后调用 `store.save(p)` 落盘
2. 重启后 `reconcile_zombie_jobs` 将 `running/queued` 状态改写为 `error`
3. 前端检测到 `error` 状态后可选择重跑或从断点续跑

---

## 八、运行时配置

### 8.1 三层叠加机制

配置采用**三层叠加**，后者压前者，只有"已设置（非 None）"的字段才覆盖：

```
Settings()  (.env / 进程环境变量, 必填基线)
   └─ 叠加 config.json.global        (全局默认覆盖)
        └─ 叠加 config.json.stages[stage]   (该环节覆盖)
```

### 8.2 配置视图与脱敏

| 操作 | 密钥字段处理 |
|---|---|
| GET `/api/config` | 已配置 → `"••••••"`，未配置 → `None` |
| PUT `/api/config` | `"__UNCHANGED__"` 或 `"••••••"` → 保持原值，`""` → 清除（继承） |
| .env 基线视图 | 返回 `bool`（是否已配置） |

### 8.3 环节覆盖示例

```json
{
  "global": {
    "base_url": "https://api.example.com",
    "api_key": "••••••",
    "llm_model": "Step-3.7-Flash"
  },
  "stages": {
    "s1": {
      "llm_model": "hermes-agent"
    },
    "s2": {
      "llm_model": "hermes-agent"
    },
    "s3": {
      "image_model": "Qwen-Image-Edit-2511",
      "image_base_url": "http://127.0.0.1:8091"
    }
  }
}
```

---

## 九、安全机制

### 9.1 认证与授权

- **口令存储**：bcrypt 哈希后存储在 `users.json`
- **会话管理**：Starlette `SessionMiddleware` 签名 Cookie，无服务端 session 表
- **权限控制**：普通用户只能编辑自己的项目，管理员可删除任意项目
- **只读模式**：通过 `SHANHAI_READONLY` 环境变量开启，关闭所有写入操作

### 9.2 路径遍历防护

```python
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

def project_dir(project_id: str, root: Path = DEFAULT_ROOT) -> Path:
    """project_id 落盘路径的唯一入口"""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(f"非法 project_id: {project_id!r}")
    return root / project_id
```

### 9.3 静态文件访问控制

`_ArtifactStatic` 类禁止下载敏感文件：

```python
class _ArtifactStatic(StaticFiles):
    async def get_response(self, path: str, scope):
        protected = {"project.json", runtime_config._config_path().name.lower()}
        if Path(path).name.lower() in protected:
            raise HTTPException(404)
        return await super().get_response(path, scope)
```

### 9.4 请求体校验

- 枚举参数校验（`minutes`/`audience`/`tone`/`style`）
- 字符串长度限制（`caption` max_length=80，`story` max_length=20000）
- Pydantic `validate_assignment` 确保属性赋值也校验

---

## 十、前后端通信

### 10.1 API 设计规范

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/login` | POST | 用户登录 |
| `/api/logout` | POST | 用户登出 |
| `/api/me` | GET | 获取当前用户信息 |
| `/api/projects` | POST | 新建项目 |
| `/api/projects` | GET | 项目列表 |
| `/api/projects/{id}` | GET | 项目详情 |
| `/api/projects/{id}` | DELETE | 删除项目（管理员） |
| `/api/projects/{id}/cancel` | POST | 取消生成任务 |
| `/api/projects/{id}/export` | POST | 导出 PDF/ZIP |
| `/api/projects/{id}/cells/{index}` | PATCH | 更新单元格 |
| `/api/projects/{id}/cells/{index}/redraw` | POST | 重绘页面 |
| `/api/projects/{id}/cells/{index}/revoice` | POST | 重配音 |
| `/api/projects/{id}/steps/{name}` | POST | 单步重跑 |
| `/api/config` | GET/PUT | 配置管理 |
| `/api/queue` | GET | 全局队列 |
| `/api/meta` | GET | 枚举选项 |

### 10.2 错误处理

前端 `api.ts` 统一处理 HTTP 错误：

```typescript
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    const msg = typeof detail === 'string' && detail !== ''
      ? detail
      : detail != null ? JSON.stringify(detail) : `HTTP ${res.status}`
    throw new ApiError(msg, res.status)
  }
  return res.json() as Promise<T>
}
```

### 10.3 缓存策略

- **文件缓存**：静态文件 URL 追加 `?v=<mtime>` 做 cache-busting
- **Session Cookie**：生产环境可通过 `SHANHAI_SESSION_HTTPS_ONLY=true` 启用 Secure 标志

---

## 十一、前端组件架构

### 11.1 核心组件职责

| 组件 | 职责 |
|---|---|
| `ProjectDetailView` | 项目详情主视图，包含页面列表、编辑操作、步骤重跑 |
| `ProgressSteps` | 管线进度可视化（S0–S6 步骤条） |
| `NewProjectForm` | 新建项目表单（景区选择、参数配置） |
| `SettingsPanel` | 运行时配置面板（端点/模型覆盖） |
| `QueuePanel` | 全局生成队列展示 |
| `ScenicSpotPicker` | 5A 景区快速选择器 |

### 11.2 状态管理

前端采用**React 内置状态 + 轮询**模式，不使用复杂状态管理库：

```typescript
function ProjectDetailView({ project, meta, onChanged }) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [insertAfter, setInsertAfter] = useState<number | null>(null)
  const [stepBusy, setStepBusy] = useState<string | null>(null)
  
  // 每 3 秒轮询一次
  useEffect(() => {
    const timer = setInterval(() => {
      api.get(project.project_id).then(onChanged).catch(() => {})
    }, 3000)
    return () => clearInterval(timer)
  }, [project.project_id, onChanged])
}
```

### 11.3 样式设计

采用**Tailwind CSS + 自定义主题**方案：

```css
:root {
  --color-ink: #2c2c2c;
  --color-ink-soft: #6b6b6b;
  --color-cinnabar: #c3423f;
  --color-jade: #4a8c5a;
  --color-rice: #f8f4e8;
  --color-paper: #fffcf5;
  --color-kraft: #e8e0d0;
}
```

---

## 十二、部署架构

### 12.1 服务组合

| 服务 | 端口 | 用途 |
|---|---|---|
| `shanhai-web` | 8080 | FastAPI 主服务 |
| `shanhai-image` | 8091 | ComfyUI 图像服务（OpenAI 兼容） |
| `shanhai-tts` | 8090 | Qwen-TTS 语音服务（OpenAI 兼容） |
| `shanhai-music` | 8092 | ACE-STEP 音乐服务（OpenAI 兼容） |
| Hermes Agent | 8642 | 编剧大师/导演大师技能服务 |
| vLLM | 8000 | 本地 LLM 推理服务 |
| Ollama | 11434 | 本地 LLM（glm-4.7-flash） |

### 12.2 配置文件

```bash
# .env 示例
SHANHAI_BASE_URL=https://api.example.com
SHANHAI_API_KEY=your-api-key
SHANHAI_LLM_MODEL=Step-3.7-Flash
SHANHAI_IMAGE_MODEL=gpt-image-2
SHANHAI_TTS_MODEL=Qwen3-TTS
SHANHAI_MUSIC_MODEL=ace-step-v1.5xl
SHANHAI_HOST=0.0.0.0
SHANHAI_PORT=8080
SHANHAI_CORS_ORIGINS=https://your-domain.com
SHANHAI_SESSION_SECRET=your-secret-key
```

---

## 十三、性能优化

### 13.1 列表端点优化

项目列表端点绕过 Pydantic 全量校验，直接读取 JSON 取所需字段：

```python
@app.get("/api/projects")
def list_projects(user: str = Depends(current_user)) -> list[dict]:
    loaded = []
    for meta in store.DEFAULT_ROOT.glob("*/project.json"):
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        item = {
            "project_id": d.get("project_id") or meta.parent.name,
            "scenic_spot": d.get("scenic_spot", ""),
            "owner": d.get("owner", ""),
            "pipeline": (d.get("status") or {}).get("pipeline", "pending"),
            "mp4": _mp4_url((d.get("output") or {}).get("mp4", "")),
        }
        loaded.append(item)
```

### 13.2 Client 复用

同一作业内配置相同的环节复用同一组 `httpx.Client`，避免每作业泄漏 24 个连接池：

```python
def _client_key(s: Settings) -> tuple:
    """一次 resolve 内的 client 去重键"""
    return (s.llm_provider, s.llm_endpoint, s.llm_model, ...)

def resolve_stage_clients(cfg: AppConfig | None = None) -> ...:
    cache: dict[tuple, tuple[LLMClient, ImageClient, TTSClient, MusicClient]] = {}
    clients = {}
    for st in settings:
        key = _client_key(settings[st])
        if key not in cache:
            cache[key] = _clients(settings[st])
        clients[st] = cache[key]
    return settings, clients
```

### 13.3 异步 I/O

所有外部调用（LLM/Image/TTS/Music）均使用 httpx 异步客户端，避免阻塞线程池。

---

## 十四、测试覆盖

### 14.1 测试结构

```
tests/
├── test_api.py              # API 端点测试
├── test_cli.py              # CLI 命令测试
├── test_config.py           # 配置测试
├── test_store.py            # 持久化测试
├── test_schema.py           # 数据模型测试
├── test_http.py             # HTTP Provider 测试
├── test_local_backend_guard.py  # 并发锁测试
├── test_runtime_config.py   # 运行时配置测试
├── test_safety.py           # 安全检查测试
├── test_editing.py          # 编辑操作测试
├── test_export.py           # 导出测试
├── test_ffmpeg.py           # FFmpeg 测试
├── test_paneling.py         # 漫画布局测试
├── test_styles.py           # 画风测试
├── test_typeset.py          # 字幕排版测试
├── test_s0.py ~ test_s6.py  # 各步骤单元测试
├── test_llm_provider.py     # LLM Provider 测试
├── test_llm_ollama.py       # Ollama Provider 测试
├── test_image_provider.py   # 图像 Provider 测试
├── test_tts.py              # TTS Provider 测试
├── test_music_provider.py   # 音乐 Provider 测试
└── test_auth.py             # 认证测试
```

### 14.2 测试策略

- **单元测试**：覆盖各步骤核心逻辑、Provider 重试机制、并发锁行为
- **集成测试**：通过 `TestClient` 测试 API 端点
- **端到端测试**：完整管线生成（M1 验收测试）

---

## 十五、总结

WanderInk Web 系统是一个**专业级、可干预、可部署**的多模态创意系统，其技术架构体现了以下核心价值：

1. **架构清晰**：三层架构（编排层/数据层/Provider 层）职责明确，易于维护和扩展
2. **高可用设计**：原子写、断点续跑、重试策略、协作式取消确保系统稳定
3. **并发安全**：两级锁机制、本地后端全局单并发锁、背压控制保证多用户协作安全
4. **灵活配置**：三层叠加配置机制支持全局默认和按环节覆盖，无需重启即可切换模型
5. **安全合规**：认证授权、路径遍历防护、敏感文件访问控制、AI 合规水印
6. **测试完备**：300+ 单元测试覆盖核心路径，确保代码质量

系统充分利用 NVIDIA DGX Spark 的统一内存优势，整合 StepFun 大模型、Hermes 专业技能、ComfyUI 图像管线和 FFmpeg 合成能力，实现了"AI 创意工业化"的完整实践。