# WanderInk (Mànyóu Mòhuì) Project Documentation

> Competition: 2nd NVIDIA DGX Spark Hackathon

---

## I. Project Overview

### 1.1 Project Naming

**WanderInk (Chinese name: "漫游墨绘" / Mànyóu Mòhuì)** is an end-to-end multimodal AI creative system for scenic spot cultural IP development and digital content production.

The English name **WanderInk** consists of *Wander* and *Ink*. *Wander* implies exploration, roaming, and cultural tracing, representing users traversing history and geography with AI assistance to explore the cultural stories behind scenic spots. *Ink* symbolizes brush and ink, painting, and traditional artistic expression, reflecting how the system transforms cultural content into visual artworks through AI technology.

The Chinese name "漫游墨绘" integrates the journey of exploration with traditional ink artistic conception, embodying both the immersive cultural experience of scenic spots and the new form of AI-enabled traditional cultural creation.

### 1.2 Project Objectives

WanderInk aims to build an **AI-native multimodal content production platform** for cultural tourism scenarios.

Users only need to input a scenic spot name, and the system automatically completes:

- Scenic spot cultural data retrieval and knowledge understanding;
- Intelligent adaptation of folk tales and historical stories;
- Script writing and plot planning;
- Professional storyboard design;
- Character image design and visual style generation;
- Comic page drawing;
- AI voice dubbing and original music generation;
- Video editing and multimedia synthesis.

Ultimately outputting an **audio comic short video** with **1080P HD quality, complete narrative, voice dubbing, and background music**.

**Core slogan**:

> **Scenic spot name in, audio comic out.**

### 1.3 Project Background

WanderInk originates from the technical accumulation and product philosophy of the 1st Spark Hackathon project **[SparkScroll](https://github.com/zhanghui-china/SparkScroll)**, and has comprehensively upgraded from "AI comic generation tool" to "cultural IP multimodal intelligent creation platform" in this project.

Compared with the previous SparkScroll project, this project has achieved technical evolution in four main aspects:

**(1) From single visual generation to multimodal content production**

The first-generation SparkScroll mainly focused on comic generation, while WanderInk further integrates:

- Large language models;
- Image generation models;
- Speech synthesis models;
- Music generation models;
- Video processing capabilities;

Achieving end-to-end generation from story text to complete audiovisual works.

**(2) From generic large model generation to professional creative Agent**

Traditional large models, although capable of text generation, still suffer from insufficient plot structure, lack of cinematographic language, and weak character consistency in film content production.

This project introduces film industry creative processes, encapsulating:

- Screenwriting capability;
- Directing capability;
- Art design capability;
- Audio/video production capability;

Into multiple professional AI Agents/Skills, completing content production through multi-Agent collaboration, making the generated results more in line with film and comic creation norms.

**(3) From fully automated black-box generation to controllable creative workflow**

Addressing the "uncontrollable, difficult to modify" problem in AI content generation, WanderInk builds an intervenable creative workbench:

Users can perform manual review, adjustment, and regeneration for:

- Plot structure;
- Character settings;
- Storyboard content;
- Image effects;
- Voice dubbing and background music;

Achieving a collaborative mode of "AI automatic creation + human creative control".

**(4) From single-machine prototype to multi-user Web application platform**

This project further improves system engineering capabilities, supporting:

- Multi-user access;
- Project management;
- Creative task scheduling;
- Generation process management;
- Web-based interactive experience.

The team structure has also expanded from the previous focus on AI technology exploration to integrate:

- AI engineering;
- Frontend/backend development;
- Film and television creation;
- Product design;

Into a compound team with multi-domain capabilities.

---

## II. Product Features and Core Highlights

### 2.1 End-to-End Fully Automated Pipeline

The system breaks down scenic spot story creation into **S0–S6 seven sequential steps**, covering five modalities: text, image, speech, music, and video. A single pipeline can go from "scenic spot name" to "MP4 final product" without manual tool switching:

| Step | Name | Output |
|---|---|---|
| S0 | LEGEND Legend Retrieval and Verification | 2–5 candidate legends with source annotations |
| S1 | SCRIPT Script Adaptation | Structured script (acts/scenes/dialogues/voice-overs) |
| S2 | BOARD Storyboard Design | Page-by-page storyboard table (visual descriptions/emotions/text) |
| S3 | ROLE Character Setting | Character cards + front/side/back three-views |
| S4 | PAGES Comic Page Generation | Page-by-page 1920×1080 visuals |
| S5 | VOICE Dubbing & Music | Page-by-page narration audio + BGM |
| S6 | FILM Composite Output | MP4 with subtitles/watermark/end credits |

### 2.2 Character Consistency Without Training

The project's biggest technical highlight is the **"three-view + reference image injection"** character consistency approach:

- S3 generates front/side/back three-views for each main character;
- S4 scales three-views to 768px and passes them as image references in generation requests;
- Combined with fixed art style prefix and character feature prompts, constraining cross-page appearance.

In the M0 consistency checkpoint, the sampling evaluation of White Snake Legend with 2 characters × 3 art styles × 24 images achieved **100% zero identity drift**. Signature props (silver hairpin, paper umbrella, etc.) were preserved throughout, validating the engineering path of "maintaining high consistency without fine-tuning LoRA".

### 2.3 Audio-Visual Synchronization and Robust Audio

- S5 writes back `duration_ms` based on real audio duration, and S6 precisely calculates page length and transition offsets accordingly;
- TTS adopts "whole segment single-shot priority + truncation detection degraded to sentence-by-sentence + three tries take longest" strategy, compatible with both strong and weak models;
- When TTS is completely unavailable, the system generates a silent fallback track based on character count estimation, ensuring complete and intact final product structure.

### 2.4 Step-by-Step Intervenable Creative Workbench

Unlike completely automatic "one-click to end", WanderInk supports step-by-step confirmation by default:

- Each step's output is visualized and previewed on the frontend;
- Users can modify scripts, adjust storyboards, redraw individual pages, re-dub individual audio, and drag to reorder;
- After any step crashes, the breakpoint resume mechanism based on `project.json` only fills missing parts without redoing completed work.

### 2.5 Professional Skill-Driven

The project introduces **Hermes Agent** (configured with `Step-3.7-Flash` model underneath) to host two professional skills: "Shanyin Super Screenwriter Master" and "Shanyin Super Director Master", covering all text generation in S1–S3:

- **Shanyin Super Screenwriter Master Skill**: Responsible for S1 script adaptation, outputting scripts that better conform to film narrative structures (cold opening, exposition-development-climax-resolution, ≤3 main characters);
- **Shanyin Super Director Master Skill**: Responsible for S2 storyboard splitting and S3 character feature extraction, outputting storyboard tables with cinematographic language (wide shot/medium shot/close-up, lighting, atmosphere) and emotion tags.

The integration of Hermes injects film industry knowledge into the pipeline, upgrading generated content from "generic LLM text output" to "structured output constrained by professional creative methodologies". WanderInk simply calls the service via OpenAI-compatible protocol with `hermes-agent` as the model name, and the combination of underlying model and skill prompts is managed internally by Hermes.

---

## III. Technical Implementation Plan

### 3.1 Overall Architecture

WanderInk adopts a **"Supervisor + Sequential Pipeline + Pluggable Provider"** three-layer architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                        User Layer (Web)                     │
│  React 18 + Vite 5 + Tailwind CSS + TypeScript              │
│  Login Authentication / Project Management / Real-time      │
│  Progress / Visual Editing                                  │
├─────────────────────────────────────────────────────────────┤
│                       Orchestration Layer (API)             │
│  FastAPI + Pydantic + ThreadPoolExecutor                    │
│  S0–S6 Seven-Step Pipeline / Background Thread Execution    │
│  / Breakpoint Resume / Cooperative Cancellation             │
├─────────────────────────────────────────────────────────────┤
│                       Data Layer (Project)                  │
│  Aggregate Root Pattern / project.json Single Source of     │
│  Truth / Atomic Write Persistence                           │
├─────────────────────────────────────────────────────────────┤
│                       Provider Layer                        │
│  LLM / Image / TTS / Music Four Providers                   │
│  OpenAI-Compatible Protocol / Local Backend Global          │
│  Single-Concurrency Lock                                    │
├─────────────────────────────────────────────────────────────┤
│                       Model Service Layer                   │
│  Hermes Agent / ComfyUI / Qwen-TTS / ACE-STEP               │
│  Local vLLM / Ollama / Cloud StepFun Models                 │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Core Design Principles

The project adopts a **"Supervisor + Sequential Pipeline + Pluggable Provider"** architecture:

- **Single `Project` Aggregate Root**: All intermediate states (candidate legends, scripts, storyboards, character cards, page outputs, final outputs) are attached to the same Pydantic `Project` object, serialized to `projects/<id>/project.json`, serving as the single source of truth;
- **OpenAI-Compatible Provider Layer**: All four providers (LLM / Image / TTS / Music) follow OpenAI-compatible protocols, allowing endpoint, model, and key switching via `.env` or Web configuration panel with zero business code changes;
- **CLI / HTTP Dual Entry**: `shanhai` (Typer CLI) and `shanhai-web` (FastAPI) reuse the same `steps/*` and provider layer; the HTTP endpoint places pipelines in background threads, with frontend polling for progress;
- **FFmpeg Functional Synthesis**: `ffmpeg.py` constructs pure commands; S6 concatenates "opening card → page-by-page → closing card → xfade transition → loudness normalization + BGM".

### 3.3 Overall Architecture Diagram

![WanderInk Overall Architecture](architecture-hd.png)

> Source file: [architecture.svg](architecture.svg) (vector SVG, 34KB, editable)  
> Rendered output: [architecture.png](architecture.png) (1×, 322KB) · [architecture-hd.png](architecture-hd.png) (2× HD, 1.1MB)  
> Recommended to open SVG files directly in browser for best clarity.

> **Legend Explanation**:
> - Light blue = User layer (Web workbench + CLI + multi-user + AI compliance + Hermes skill)
> - Light yellow = Orchestration layer (S0–S6 seven-step pipeline, Supervisor orchestration)
> - Light gray = Project data layer (aggregate root + breakpoint resume mechanism)
> - Light purple = Provider layer (LLM / Image / TTS / Music + local_backend_guard)
> - Light green = Model service layer (Hermes / ComfyUI / Qwen-TTS / ACE-STEP + tu-zi cloud)
> - Light orange = Output layer (FFmpeg synthesizer → MP4 final product)
> - Light cyan = Deployment environment (DGX Spark local + cloud services)
> - `Project` aggregate root is the single source of truth; all steps read/write the same `project.json`, supporting breakpoint resume.
> - S0–S3 text stages all go through Hermes screenwriter/director skills (underlying `Step-3.7-Flash`); images go through tu-zi cloud gpt-image-2.
> - Strictly drawn according to confirmed facts, no unverified data or specific numbers introduced.

### 3.4 Backend Tech Stack Selection

| Component | Version | Purpose |
|---|---|---|
| Python | ≥3.12 | Language foundation |
| FastAPI | ≥0.111 | HTTP API framework |
| Pydantic | ≥2.7 | Data validation and serialization |
| Pydantic Settings | ≥2.3 | Environment configuration management |
| Typer | ≥0.12 | CLI command-line tool |
| httpx | ≥0.27 | HTTP client (with retry support) |
| Pillow | ≥10.3 | Image processing |
| Uvicorn | ≥0.30 | ASGI server |
| bcrypt | ≥4.0 | Password hashing |
| itsdangerous | ≥2.0 | SessionMiddleware signed cookies |

### 3.5 Frontend Tech Stack Selection

| Component | Version | Purpose |
|---|---|---|
| React | ^18.3.1 | UI framework |
| React DOM | ^18.3.1 | DOM rendering |
| TypeScript | ^5.5.3 | Type system |
| Vite | ^5.3.4 | Build tool |
| Tailwind CSS | ^3.4.6 | CSS framework |
| Bun | latest | Package manager |

### 3.6 Pipeline Stage Models and Endpoint Configuration

The table below corresponds one-to-one with the architecture diagram and current `config.json` values, reflecting the "effective model / endpoint" actually called in each stage S0–S5:

| Stage | Purpose | Effective Model | Local Endpoint |
|---|---|---|---|
| S0 Legend | LLM | `Sehyo-Qwen3.5-35B-A3B-NVFP4`, `glm-4.7-flash` (local) / `Step-3.7-Flash` (cloud) | `127.0.0.1:8000` |
| S1 Script | LLM | `hermes-agent` (Screenwriter Master skill, underlying `Step-3.7-Flash`) | `127.0.0.1:8642` |
| S2 Storyboard | LLM | `hermes-agent` (Director Master skill, underlying `Step-3.7-Flash`) | `127.0.0.1:8642` |
| S3 Character Three-views | LLM + Image | LLM: `Sehyo-Qwen3.5-35B-A3B-NVFP4`, `glm-4.7-flash` (local) / `Step-3.7-Flash` (cloud)<br />Image: `gpt-image-2` (cloud) / `Qwen-Image-Edit-2511` (local) | `127.0.0.1:8091` |
| S4 Comic Pages | Image | `gpt-image-2` (cloud) / `Qwen-Image-Edit-2511` (local) | `127.0.0.1:8091` |
| S5 Dubbing/BGM | TTS + Music | `Qwen3-TTS` (local) + `ACE-STEP-v1.5xl` (local) | `127.0.0.1:8090/8092` |

> **Notes**:
>
> - S1–S2 LLM stages all go through local Hermes Agent (`127.0.0.1:8642`), generated by `Step-3.7-Flash` model managed internally by Hermes combined with Screenwriter Master / Director Master skill prompts, constrained by film industry creative methodologies.
> - S3 three-views and S4 page-by-page images go through cloud `gpt-image-2` or local `Qwen-Image-Edit-2511`; three-views passed M0 checkpoint with 100% zero identity drift.
> - S5 dubbing and BGM are all provided locally by DGX (`shanhai-tts` :8090 running `Qwen3-TTS`, `shanhai-music` :8092 running `ACE-STEP 1.5 XL`), protected by `local_backend_guard` global single-concurrency lock.

### 3.7 Multi-Agent Design and Pipeline Execution Mechanism

#### 3.7.1 Multi-Agent Role Design

| Agent | Responsibility | Underlying Capability |
|---|---|---|
| StoryAgent | Scenic spot → Historical story synopsis (S0) | Outline generation |
| ScriptAgent | Story → Script (S1) | Hermes Screenwriter Master skill |
| DirectorAgent | Script → Storyboard (S2) | Hermes Director Master skill |
| CharacterAgent | Script → Character profiles and prop descriptions (S3) | Image editing |
| ImageAgent | Character three-views, storyboard comics (S3/S4) | Image editing |
| VoiceAgent | Storyboard text → Narration voice (S5) | Speech synthesis |
| MusicAgent | Emotion tags → Background music (S5) | Music generation |
| ComposerAgent | Visuals + Voice + Music → MP4 (S6) | FFmpeg + PIL layout |

#### 3.7.2 Background Thread Execution

`api.py` uses `ThreadPoolExecutor(max_workers=4)` to submit pipeline tasks to background threads:

```python
_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_JOBS: dict[str, Future] = {}

def _pipeline(project_id: str, cfg: AppConfig, story: str | None) -> None:
    """Run from S0 to MP4 in background thread"""
    p = store.load(project_id)
    settings, clients = resolve_stage_clients(cfg)
    # S0 Legend Retrieval
    p = s0_legend.run(p, clients["s0"][0])
    # S1–S6 Loop Execution
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

#### 3.7.3 Progress Polling

The frontend polls `GET /api/projects/{id}` every 3 seconds to get the latest status, with progress read directly from `project.status` (persisted at each step via `store.save`).

#### 3.7.4 Cooperative Cancellation

Cancellation uses cooperative marking, not interrupting inside the current stage, but taking effect at the next stage transition point:

```python
_CANCELLED: set[str] = set()

def _check_cancelled(project_id: str) -> bool:
    """Consume cancellation flag (remove on hit, no repeat trigger)"""
    with _JOBS_LOCK:
        if project_id in _CANCELLED:
            _CANCELLED.discard(project_id)
            return True
        return False
```

### 3.8 Provider Layer Design

#### 3.8.1 OpenAI-Compatible Protocol

All four Providers (LLM / Image / TTS / Music) follow OpenAI-compatible protocols:

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

#### 3.8.2 Local Backend Global Single-Concurrency Lock

`providers/_http.py`'s `local_backend_guard` implements global single-concurrency protection for local GPU resources:

```python
_local_lock = threading.Lock()

@contextmanager
def local_backend_guard(base_url: str):
    """Local Spark backend global single concurrency: GPU physically shared, queued across stages/users"""
    if is_local_endpoint(base_url):
        with _local_lock:
            yield
    else:
        yield
```

**Design Intent**: Ollama/ComfyUI/Qwen-TTS/ACE-Step on DGX Spark share a single GPU. Concurrent requests compete for VRAM, causing inference slowdowns or even timeouts (measured LLM calls dragged from tens of seconds to nearly 900s when concurrent requests hit the same card).

#### 3.8.3 Retry Strategy

`request_with_retry` implements unified retry logic:

| Retriable Error | Handling |
|---|---|
| `httpx.TransportError` | Connection phase errors (ConnectError/ConnectTimeout/PoolTimeout) always retry; read phase errors only retry for idempotent requests |
| Transient status codes (429/500/502/503/504) | Always retry |
| Other errors | No retry, re-raise as-is |

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

### 3.9 Concurrency Model

#### 3.9.1 Two-Level Lock Design

The system adopts a **two-level lock** mechanism with one-way hierarchy and non-overlapping critical sections to avoid deadlocks:

```
┌─────────────────────────────────────────────────────────────┐
│  _JOBS_LOCK (Global)                                       │
│  ├── Protects _JOBS cleanup, backpressure check, submit    │
│  └── Protects _CANCELLED read/write                        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  _PROJECT_LOCKS[project_id] (Per-project)                  │
│  ├── Protects single-project "load→modify→save" cycle      │
│  └── Prevents writers from losing updates                  │
└─────────────────────────────────────────────────────────────┘
```

**Lock Order Rule**: Always `project→jobs`, i.e., acquire project lock first, then jobs lock, never reverse.

#### 3.9.2 Backpressure Mechanism

The system sets `MAX_PENDING=8` as the upper limit for unfinished jobs; exceeding it rejects new creation:

```python
MAX_PENDING = 8  # Unfinished job limit

@app.post("/api/projects")
def create_project(body: NewProject, user: str = Depends(current_user)):
    with _JOBS_LOCK:
        # Clean completed jobs
        for done in [k for k, f in list(_JOBS.items()) if f.done()]:
            del _JOBS[done]
        # Backpressure check
        if len(_JOBS) >= MAX_PENDING:
            raise HTTPException(429, f"Generation queue full (limit {MAX_PENDING}), please try again later")
        # Create project and submit background task
        _JOBS[p.project_id] = _EXECUTOR.submit(_pipeline, p.project_id, cfg, body.story)
```

#### 3.9.3 Single-Step Re-run

Supports partial regeneration after editing without re-running the entire pipeline:

```python
_STEP_NAMES = ("s2", "s3", "s4", "s5", "s6")

def _run_step(project_id: str, name: str, cfg: AppConfig) -> None:
    """Run single step in background thread"""
    if name == "s2":
        p = s2_storyboard.run(p, llm, use_skill=...)
    elif name == "s3":
        p = s3_characters.run(p, llm, image, workdir, ...)
    # ...
    if name != "s6":
        p.output.clear()  # Invalidate already-composed output
        # Cascade clear downstream stage status
        idx = _STEP_NAMES.index(name)
        for step in _STEP_NAMES[idx + 1:]:
            for key in (step, f"{step}_started_at", f"{step}_elapsed_s"):
                p.status.pop(key, None)
```

---

### 3.10 Data Persistence

#### 3.10.1 Atomic Write Mechanism

`store.py`'s `atomic_write_text` guarantees write atomicity:

```python
def atomic_write_text(path: Path, text: str) -> None:
    """Write to unique temp file first, then os.replace to publish"""
    tmp = path.parent / f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
```

**Design Intent**: When multiple threads concurrently write to the same path, each writes to its own temp file. Readers always see either the complete old file or new file, avoiding torn writes.

#### 3.10.2 Aggregate Root Pattern

`Project` as the single aggregate root contains all intermediate states:

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

#### 3.10.3 Breakpoint Resume

Breakpoint resume mechanism based on `project.json`:

1. After each step completes, call `store.save(p)` to persist
2. After restart, `reconcile_zombie_jobs` rewrites `running/queued` status to `error`
3. Frontend detects `error` status and can choose to re-run or resume from breakpoint

---

### 3.11 Runtime Configuration

#### 3.11.1 Three-Layer Overlay Mechanism

Configuration uses **three-layer overlay**, with later layers overriding earlier ones, and only "set (non-None)" fields override:

```
Settings()  (.env / process environment variables, mandatory baseline)
   └─ Overlay config.json.global        (global default override)
        └─ Overlay config.json.stages[stage]   (stage-specific override)
```

#### 3.11.2 Configuration Views and Redaction

| Operation | Secret Field Handling |
|---|---|
| GET `/api/config` | Configured → `"••••••"`, Not configured → `None` |
| PUT `/api/config` | `"__UNCHANGED__"` or `"••••••"` → Keep original, `""` → Clear (inherit) |
| .env baseline view | Returns `bool` (whether configured) |

#### 3.11.3 Stage Override Example

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

### 3.12 Security Mechanisms

#### 3.12.1 Authentication and Authorization

- **Password Storage**: bcrypt hashed and stored in `users.json`
- **Session Management**: Starlette `SessionMiddleware` signed cookies, no server-side session table
- **Permission Control**: Regular users can only edit their own projects, admins can delete any project
- **Read-Only Mode**: Enabled via `SHANHAI_READONLY` environment variable, disables all write operations

#### 3.12.2 Path Traversal Protection

```python
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

def project_dir(project_id: str, root: Path = DEFAULT_ROOT) -> Path:
    """Single entry point for project_id storage path"""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")
    return root / project_id
```

#### 3.12.3 Static File Access Control

The `_ArtifactStatic` class prohibits downloading sensitive files:

```python
class _ArtifactStatic(StaticFiles):
    async def get_response(self, path: str, scope):
        protected = {"project.json", runtime_config._config_path().name.lower()}
        if Path(path).name.lower() in protected:
            raise HTTPException(404)
        return await super().get_response(path, scope)
```

#### 3.12.4 Request Body Validation

- Enum parameter validation (`minutes`/`audience`/`tone`/`style`)
- String length limits (`caption` max_length=80, `story` max_length=20000)
- Pydantic `validate_assignment` ensures attribute assignment also validates

### 3.13 Frontend-Backend Communication

#### 3.13.1 API Design Specifications

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/login` | POST | User login |
| `/api/logout` | POST | User logout |
| `/api/me` | GET | Get current user info |
| `/api/projects` | POST | Create new project |
| `/api/projects` | GET | Project list |
| `/api/projects/{id}` | GET | Project details |
| `/api/projects/{id}` | DELETE | Delete project (admin) |
| `/api/projects/{id}/cancel` | POST | Cancel generation task |
| `/api/projects/{id}/export` | POST | Export PDF/ZIP |
| `/api/projects/{id}/cells/{index}` | PATCH | Update cell |
| `/api/projects/{id}/cells/{index}/redraw` | POST | Redraw page |
| `/api/projects/{id}/cells/{index}/revoice` | POST | Re-dub |
| `/api/projects/{id}/steps/{name}` | POST | Single-step re-run |
| `/api/config` | GET/PUT | Configuration management |
| `/api/queue` | GET | Global queue |
| `/api/meta` | GET | Enum options |

#### 3.13.2 Error Handling

Frontend `api.ts` unifies HTTP error handling:

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

#### 3.13.3 Caching Strategy

- **File Cache**: Static file URLs append `?v=<mtime>` for cache-busting
- **Session Cookie**: Secure flag can be enabled via `SHANHAI_SESSION_HTTPS_ONLY=true` in production

### 3.14 Frontend Component Architecture

#### 3.14.1 Core Component Responsibilities

| Component | Responsibility |
|---|---|
| `ProjectDetailView` | Project detail main view, including page list, edit operations, step re-run |
| `ProgressSteps` | Pipeline progress visualization (S0–S6 step bar) |
| `NewProjectForm` | New project form (scenic spot selection, parameter configuration) |
| `SettingsPanel` | Runtime configuration panel (endpoint/model override) |
| `QueuePanel` | Global generation queue display |
| `ScenicSpotPicker` | 5A scenic spot quick selector |

#### 3.14.2 State Management

The frontend adopts **React built-in state + polling** pattern, without complex state management libraries:

```typescript
function ProjectDetailView({ project, meta, onChanged }) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [insertAfter, setInsertAfter] = useState<number | null>(null)
  const [stepBusy, setStepBusy] = useState<string | null>(null)
  
  // Poll every 3 seconds
  useEffect(() => {
    const timer = setInterval(() => {
      api.get(project.project_id).then(onChanged).catch(() => {})
    }, 3000)
    return () => clearInterval(timer)
  }, [project.project_id, onChanged])
}
```

#### 3.14.3 Style Design

Adopts **Tailwind CSS + custom theme** scheme:

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

### 3.15 Deployment Architecture

#### 3.15.1 Service Composition

| Service | Port | Purpose |
|---|---|---|
| `shanhai-web` | 8080 | FastAPI main service |
| `shanhai-image` | 8091 | Qwen-Image-Edit image service (OpenAI-compatible) |
| `shanhai-tts` | 8090 | Qwen-TTS speech service (OpenAI-compatible) |
| `shanhai-music` | 8092 | ACE-STEP music service (OpenAI-compatible) |
| Hermes Agent | 8642 | Screenwriter Master/Director Master skill service |
| vLLM | 8000 | Local LLM inference service |
| Ollama | 11434 | Local LLM |

#### 3.15.2 Configuration Files

```bash
# .env example
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

### 3.16 Performance Optimization

#### 3.16.1 List Endpoint Optimization

Project list endpoint bypasses full Pydantic validation, directly reading JSON and extracting needed fields:

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

#### 3.16.2 Client Reuse

Stages with the same configuration within the same job reuse the same set of `httpx.Client`, avoiding 24 connection pool leaks per job:

```python
def _client_key(s: Settings) -> tuple:
    """Client deduplication key within one resolve"""
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

#### 3.16.3 Async I/O

All external calls (LLM/Image/TTS/Music) use httpx async client to avoid blocking the thread pool.

---

### 3.17 Technical Summary

WanderInk is a **professional, intervenable, and deployable** multimodal creative system whose technical architecture embodies the following core values:

1. **Clear Architecture**: Three-layer architecture (orchestration/data/Provider) with well-defined responsibilities, easy to maintain and extend
2. **High Availability Design**: Atomic write, breakpoint resume, retry strategy, and cooperative cancellation ensure system stability
3. **Concurrency Safety**: Two-level lock mechanism, local backend global single-concurrency lock, and backpressure control guarantee safe multi-user collaboration
4. **Flexible Configuration**: Three-layer overlay configuration mechanism supports global defaults and stage-specific overrides, allowing model switching without restart
5. **Security Compliance**: Authentication/authorization, path traversal protection, sensitive file access control, AI compliance watermarking
6. **Comprehensive Testing**: 300+ unit tests covering core paths ensure code quality

The system fully leverages NVIDIA DGX Spark's unified memory advantages, integrating StepFun large models, Hermes professional skills, ComfyUI image pipelines, and FFmpeg synthesis capabilities, realizing a complete practice of "AI creative industrialization".

### 3.18 Key Technical Details

- **Subtitle and Visual Layering**: `compose_page()` only outputs 1920×1080 full-frame base image; `overlay_layer()` separately generates transparent PNG for subtitles and "AI Generated" watermark; ffmpeg overlay is applied after Ken Burns scaling to avoid text shaking with camera movement or watermark being cropped out of frame.
- **Atomic Write and Reentrant**: `store.save()` uses "write to `.tmp` then `os.replace`" atomic write, persisting at each step; S3 `locked` is idempotent, S4/S5 output existence validation supports breakpoint resume after any step crash.
- **Local Backend Global Single Concurrency**: `providers/_http.py` automatically identifies `127.0.0.1`/`localhost` endpoints through `local_backend_guard`, globally queuing Ollama/vllm/ComfyUI/Qwen-TTS/ACE-Step sharing GPU on DGX Spark, avoiding timeout caused by multi-task GPU contention.
- **Stage-Specific Configuration Override**: Web configuration panel supports "global default + S0–S5 stage-specific override", e.g., S1–S2 all use Hermes screenwriter/director skills (underlying `Step-3.7-Flash`), images use cloud or local ComfyUI, taking effect without restart.

---

## IV. Architecture Optimization Plan

### 4.1 DGX Spark Platform Adaptation Philosophy

The core constraint of DGX Spark (GB10, 128GB unified memory) is "multiple models cannot reside simultaneously". Therefore, this project does not attempt to load all models at once, but adopts a strategy of **time-slice loading + local service shim + global single-concurrency lock**:

1. **Text Phase**: VLLM local LLM (`Sehyo-Qwen3.5-35B-A3B-NVFP4`) permanently resides at ~35GB;
2. **Image Phase**: Bridge to ComfyUI (`Qwen-Image-Edit-2511`) via `shanhai-image.service`;
3. **Audio Phase**: `shanhai-tts.service` (`Qwen3-TTS`) and `shanhai-music.service` (`ACE-STEP 1.5 XL`) time-share GPU;
4. **Composition Phase**: ComposerAgent consumes only ~2GB, using ffmpeg for final output.

This design fully leverages DGX Spark's platform advantages of **large unified memory, single-machine multimodal collaboration, and local closed-loop inference**.

### 4.2 Implemented Key Optimizations

| Optimization | Problem | Solution | Effect |
|---|---|---|---|
| Character Consistency | Cross-page character appearance drift | Three-view reference images + 768px scaled upload + fixed art style prefix | 100% pass M0 checkpoint |
| Vertical Image Filling | gpt-image-2 produces vertical images causing black bars | Complete frame centered + same-image blurred darkening to fill sides | Professional final product appearance |
| S4 Concurrency | Sequential page generation too slow | ThreadPool max 3, local GPU automatically serializes | ~23% speedup in cloud scenarios |
| Silent Fallback | TTS unavailability causes incomplete final product | Generate silent track based on character count estimation | End-to-end output even if TTS completely fails |
| Network Fault Tolerance | Proxy transient 503/RemoteProtocolError | Capture complete `httpx.TransportError` family with exponential backoff | Improved stability in real DGX deployment |
| Configuration Panel | Endpoint changes require file modification and restart | Web UI runtime override, persisted to `config.json` | Model switching without restart |
| GPU TTS | DGX has no online TTS | Deploy Qwen-TTS shim, PyTorch nightly adapts to GB10 sm_121 | Local human voice now working |
| AI BGM | Empty music library | ACE-STEP 1.5 XL generates pure instrumental BGM locally | S5 can now output real background music |

### 4.3 Optimization Plan

The following features have been implemented:

- **Multi-user login and queue visibility**: Cookie session + `users.json`, supports team sharing, only see own cancellation permissions;
- **National 5A scenic spot information entry**: Extracted 359 5A scenic spots from cultural and tourism bureau official website, allowing users to quickly input;

The following features are planned:

- **Multi-panel comic layout per page**: Japanese-style multi-panel storyboards for stronger pacing and visual hierarchy;
- **Character library cross-project reuse**: Store locked characters in global library, reduce redundant generation;
- **PDF refined layout and multi-language output**: Targeting B-end scenic spot operators;
- **Cost cap and redraw budget control**: Prevent remote API quota runaway;
- **Custom three-view support**: Allow users to design their own IP characters;
- **Real scenic spot image composition**: Allow users to upload real scenic spot images for comic and video composition.

---

## V. AI Compliance Handling and Sensitive Information Management

This project builds AI content compliance as a **non-disablable hard rule** into the synthesis process:

1. **"AI Generated" watermark per page**: Transparent overlay layer displays stroked watermark at fixed upper-right corner, not affected by Ken Burns camera movement;
2. **AI generation identification**: Both images and videos clearly marked with "WanderInk AI-assisted generation";
3. **Precise source annotation**: Distinguish by `source_type` between "official history / local gazette / folk legend / literary work / original interpretation", original interpretations do not impersonate legend sources;
4. **Sensitive content filtering**: S0 sends candidate legends involving religion, ethnicity, and modern political figures to sensitive review list; user manual clearly indicates such scenic spots can take "custom story" path;
5. **Child-friendly content protection**: Automatically avoid violence/horror details for child audiences;
6. **Copyright boundary**: Users uploading custom stories must self-certify rights; system outputs clearly mark source and AI generation identity.

Future planning: Introduce input/output bilateral content safety filtering API for automatic review of text-to-image prompts and generated results.

---

## VI. Deployment Plan

### 6.1 Install ComfyUI Environment

```bash
# Login as wuzi user
source ~/.bashrc

# Install dependencies
sudo apt-get install -y sox libsox-fmt-all

# Create conda environment
conda create -n comfyui python=3.12 -y
conda activate comfyui

# Download ComfyUI repository
cd ~
git clone https://github.com/comfyanonymous/ComfyUI.git

# Install PyTorch according to GPU CUDA version
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install ComfyUI dependencies
cd ~/ComfyUI
pip install -r requirements.txt
```

Edit `~/.config/systemd/user/comfyui.service` to configure systemctl service:

```
[Unit]
Description=ComfyUI User Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/home1/wuzi/ComfyUI
Environment="HF_ENDPOINT=https://hf-mirror.com"
ExecStart=/home1/wuzi/anaconda3/bin/conda run --no-capture-output -n comfyui python main.py --listen 0.0.0.0 --port 8188 --use-flash-attention --gpu-only
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

### 6.2 Install Web Service Environment

```bash
# Login as huntun user
source ~/.bashrc

# Download repository
git clone https://github.com/zhanghui-china/WanderInk 
cd ~/WanderInk/web

# Fill in endpoints and models according to environment
cp .env.example .env   

# Create uv environment
uv sync
uv run shanhai-web

# Install web environment
cd web/web
bun install && bun run dev
```

Edit `~/.config/systemd/user/shanhai-web.service` to configure systemctl service:

```
[Unit]
Description=shanhai web (FastAPI + SPA)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/shanhai

[Service]
WorkingDirectory=%h/shanhai
EnvironmentFile=%h/shanhai/.env
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"
ExecStart=%h/.local/bin/uv run shanhai-web
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

Edit `~/.config/systemd/user/shanhai-image.service` to configure systemctl service:

```
[Unit]
Description=shanhai image shim (ComfyUI, OpenAI-compatible)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/image-shim

[Service]
WorkingDirectory=%h/image-shim
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=%h/image-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8091
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Edit `~/.config/systemd/user/shanhai-tts.service` to configure systemctl service:

```
[Unit]
Description=shanhai TTS shim (Qwen3-TTS VoiceDesign via ComfyUI, OpenAI-compatible)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/qwentts-shim

[Service]
WorkingDirectory=%h/qwentts-shim
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=%h/qwentts-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Edit `~/.config/systemd/user/shanhai-music.service` to configure systemctl service:

```
[Unit]
Description=shanhai music shim (ACE-Step via ComfyUI, OpenAI-compatible)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/music-shim

[Service]
WorkingDirectory=%h/music-shim
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=%h/music-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8092
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

### 6.3 Install Hermes Environment

Refer to <https://zhuanlan.zhihu.com/p/2056830749530142643>

For security reasons, the hermes environment is isolated from the wanderink runtime environment to prevent hermes from deleting project code and documents after gaining high permissions.

```bash
# Login as hermes user
source ~/.bashrc

# Install dependencies
sudo apt install ripgrep

# Install hermes
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Configure hermes
hermes setup
```

### 6.4 Model Downloads

#### 6.4.1 Download LLM Model: Sehyo-Qwen3.5-35B-A3B-NVFP4

Edit file `download_model_by_modelscope_Sehyo-Qwen3.5-35B-A3B-NVFP4.py`:

```python
from modelscope import snapshot_download
import os

model_id = "hf/Sehyo-Qwen3.5-35B-A3B-NVFP4"
local_dir = "/home1/wuzi/models/"

model_dir = snapshot_download(
    model_id,
    local_dir=local_dir,
    revision='master'
)

print(f"Download completed, files saved in: {model_dir}")
```

Download the model:

```bash
python download_model_by_modelscope_Sehyo-Qwen3.5-35B-A3B-NVFP4.py
```

#### 6.4.2 Download ComfyUI Image Editing Model: Qwen-Image-Edit-2511

> [!NOTE]
> The system's `~/ComfyUI/models` directory is a symbolic link pointing to the actual storage directory `~/models/comfyui_models`. The following operations will directly download model files to the actual directory.

Download `qwen_image_edit_2511_fp8mixed.safetensors` and save it to `~/models/comfyui_models/diffusion_models/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download Comfy-Org/Qwen-Image-Edit_ComfyUI split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors --local-dir ~/models/comfyui_models/diffusion_models --local-dir-use-symlinks False
  
  # After download, move the file to the correct root directory and clean up empty directories
  mv ~/models/comfyui_models/diffusion_models/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors ~/models/comfyui_models/diffusion_models/
  rm -rf ~/models/comfyui_models/diffusion_models/split_files
  ```

- **Using huggingface-cli**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/diffusion_models
  
  # Download model file
  wget -O ~/models/comfyui_models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors \
    https://hf-mirror.com/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors
  ```

#### 6.4.3 Download ComfyUI Speech Synthesis Model: Qwen3-TTS

Download the complete `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` repository and save it to `~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign` directory.

You can download using one of the following methods:

- **Using huggingface-cli (Recommended, with domestic mirror acceleration)**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign
  
  # Download complete model repository
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign \
    --local-dir ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign \
    --local-dir-use-symlinks False
  ```

* **Using git clone (requires git-lfs installed)**:

  ```bash
  # Clone repository to specified location
  git clone https://hf-mirror.com/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign
  ```

* **Official Hugging Face Link**:
  [Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/tree/main)

#### 6.4.4 Download ComfyUI Music Generation Model: ACE-STEP XL Turbo

Download the music generation model `acestep1.5XL_ComfyUI_aio-marduk191.safetensors` and save it to `~/models/comfyui_models/checkpoints/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download marduk191/acestep1.5XL_ComfyUI_aio-marduk191 acestep1.5XL_ComfyUI_aio-marduk191.safetensors --local-dir ~/models/comfyui_models/checkpoints --local-dir-use-symlinks False
  ```

- **Using huggingface-cli**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/vae
  
  # Download model file
  wget -O ~/models/comfyui_models/vae/qwen_image_vae.safetensors \
    https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors
  ```

- **Official Hugging Face Link**:
  [acestep1.5XL_ComfyUI_aio-marduk191.safetensors](https://huggingface.co/marduk191/acestep1.5XL_ComfyUI_aio-marduk191/tree/main)

#### 6.4.5 Download ComfyUI VAE Model

Download `qwen_image_vae.safetensors` and save it to `~/models/comfyui_models/vae/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download marduk191/acestep1.5XL_ComfyUI_aio-marduk191 acestep1.5XL_ComfyUI_aio-marduk191.safetensors --local-dir ~/models/comfyui_models/checkpoints --local-dir-use-symlinks False
  ```

- **Official Hugging Face Link**:
  [qwen_image_vae.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/vae/qwen_image_vae.safetensors)

#### 6.4.6 Download ComfyUI CLIP/Text Encoder Models

Download `qwen_2.5_vl_7b.safetensors` and `qwen3vl_4b_bf16.safetensors` and save them to `~/models/comfyui_models/text_encoders/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

```bash
# Create directory (if not exists)
mkdir -p ~/models/comfyui_models/text_encoders

# Download qwen_2.5_vl_7b
wget -O ~/models/comfyui_models/text_encoders/qwen_2.5_vl_7b.safetensors \
  https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors

# Download qwen3vl_4b_bf16
wget -O ~/models/comfyui_models/text_encoders/qwen3vl_4b_bf16.safetensors \
  https://hf-mirror.com/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_bf16.safetensors
```

- **Using huggingface-cli**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/checkpoints
  
  # Download model file
  wget -O ~/models/comfyui_models/checkpoints/acestep1.5XL_ComfyUI_aio-marduk191.safetensors \
    https://hf-mirror.com/marduk191/acestep1.5XL_ComfyUI_aio-marduk191/resolve/main/acestep1.5XL_ComfyUI_aio-marduk191.safetensors
  ```

- **Official Hugging Face Links**:

  - [qwen_2.5_vl_7b.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors)
  - [qwen3vl_4b_bf16.safetensors](https://huggingface.co/Comfy-Org/Krea-2/blob/main/text_encoders/qwen3vl_4b_bf16.safetensors)

#### 6.4.7 Download ComfyUI LoRA Models

##### 6.4.7.1 Qwen-Image-Edit-2511

Download `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` and save it to `~/models/comfyui_models/loras/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/loras
  
  # Download LoRA model file
  wget -O ~/models/comfyui_models/loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors \
    https://hf-mirror.com/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors
  ```

- **Official Hugging Face Link**:
  [Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/blob/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors)

##### 6.4.7.2 Other LoRA Models

Other LoRA models can be downloaded from civital.com and saved to `~/models/comfyui_models/loras/` directory after download.

- **LoRA Model Links**:
  - [Real_ani_qwen](https://civitai.com/models/2164588/gen-ani-art-style-qwen-lora)
  - [Qwen-image_2511_Edit_Ball-jointed_Doll V2.0](https://civitai.com/models/2303022/qwen-image2511editball-jointeddoll-v20)
  - [Nano banana figurine style](https://civitai.com/models/1900696/nano-banana-figurine-style-qwen-image-edit)

### 6.5 Download vllm Docker Image

```bash
docker pull vllm/vllm-openai:cu130-nightly
```

### 6.6 System Startup

#### 6.6.1 Start ComfyUI Service

```bash
# Login as wuzi user
source ~/.bashrc

# Start ComfyUI service
systemctl --user restart comfyui
systemctl --user status comfyui
```

The service will start on port `8188`.

#### 6.6.2 Start Web Services

```bash
# Login as huntun user
source ~/.bashrc

# Start Web, TTS, image, and music services
systemctl --user start shanhai-web shanhai-tts shanhai-image shanhai-music

# Check service status
systemctl --user status shanhai-web shanhai-tts shanhai-image shanhai-music
```

The services will start on ports `8090, 8091, 8092`.

#### 6.6.3 Start Hermes Service

```bash
# Login as hermes user
source ~/.bashrc

hermes gateway restart
```

#### 6.6.4 LLM Model Startup

##### 6.6.4.1 vllm docker startup for Qwen3.5-35B-A3B-NVFP4 model

Edit file: `/home1/wuzi/models/Sehyo/Qwen3.5-35B-A3B-NVFP4/model_qwen35_p8000.yaml`

```yaml
host: "0.0.0.0"
port: 8000
reasoning-parser: "qwen3"
enable-auto-tool-choice: true
tool-call-parser: "qwen3_xml"
dtype: auto
max-model-len: 128K
api-key: "sk-my-api-key"
disable-custom-all-reduce: true
generation-config: "vllm"
gpu-memory-utilization: 0.3
language-model-only: true
```

Edit file: `/home1/wuzi/docker/docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh`

```bash
docker run -d --gpus all --rm \
       -v /home1/wuzi/models/Sehyo/:/mnt/ \
       -p 0.0.0.0:8000:8000/tcp \
       --name qwen35_35b_a3b vllm/vllm-openai:cu130-nightly /mnt/Qwen3.5-35B-A3B-NVFP4 \
       --served-model-name DGX-Qwen3.5-35B-A3B \
       --api_key "sk-my-api-key" \
       --config /mnt/Qwen3.5-35B-A3B-NVFP4/model_qwen35_p8000.yaml \
       --speculative-config '{"method": "mtp", "num_speculative_tokens": 1}'
```

Start Sehyo-Qwen3.5-35B-A3B-NVFP4 using docker:

```bash
cd /home1/wuzi/docker
chmod +x docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh
sh ./docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh
docker ps
docker logs [containerid]
```

##### 6.6.4.2 ollama startup for glm-4.7-flash model

Edit `~/.config/systemd/user/ollama-preload.service` to configure systemctl service:

```
[Unit]
Description=Ollama GLM Model Preloader
After=network.target

[Service]
Type=oneshot
ExecStartPre=/usr/bin/sleep 10
ExecStart=/usr/bin/curl --noproxy "*" -X POST http://127.0.0.1:11434/api/generate -d '{"model": "glm-4.7-flash:latest", "keep_alive": -1}'
RemainAfterExit=yes

[Install]
WantedBy=default.target
```

Start Ollama service:

```bash
# Login as wuzi user
source ~/.bashrc

# Start Ollama preload service
systemctl --user restart ollama-preload
systemctl --user status ollama-preload
```

The service will start on port `11434`.

---

## VII. Platform Adaptability and Technology Stack: NVIDIA SDK + NVIDIA/StepFun Models

| Layer | Technology/Model | Purpose |
|---|---|---|
| Hardware Platform | NVIDIA DGX Spark (GB10, 128GB Unified Memory) | Single machine hosting LLM + Image + TTS + Music full pipeline inference |
| LLM | Ollama (`glm-4.7-flash`), vllm (`Sehyo-Qwen3.5-35B-A3B-NVFP4`), Stepfun (`step-3.7-flash`) | Story/Script/Storyboard/Character description |
| Image | ComfyUI + `Qwen-Image-Edit-2511`, `gpt-image-2` | Three-views, storyboard comics, image editing |
| Speech | `Qwen3-TTS` | Narration dubbing |
| Music | `ACE-STEP 1.5 XL` | Background music generation |
| Orchestration | FastAPI + ThreadPool + FFmpeg | Pipeline scheduling and video composition |

NVIDIA DGX Spark's unified memory and local GPU capabilities make "single-machine closed-loop without external cloud services" possible.

### 7.1 NVIDIA and StepFun Models

#### 7.1.1 Sehyo-Qwen3.5-35B-A3B-NVFP4 Model

This project selects **Sehyo/Qwen3.5-35B-A3B-NVFP4** as one of the core text models deployed locally on DGX Spark. The model is quantized based on Qwen3.5-35B-A3B MoE architecture, maintaining the original model's capabilities while completely preserving **MTP (Multi-Token Prediction)** weights. It can further combine vLLM's **Speculative Decoding** technology to improve inference throughput, making it a cost-effective solution in the Qwen3.5 series that balances performance, cost, and deployment efficiency.

The model adopts **NVFP4 (NVIDIA Floating Point 4-bit)** quantization format natively supported by the NVIDIA Blackwell platform. NVFP4 uses a hybrid design of **FP4 weights + FP8 Scale**, significantly reducing model storage and computation overhead while preserving model accuracy as much as possible. Compared to the original BF16 model, NVFP4 achieves approximately **4x model compression ratio**, significantly reducing memory footprint and memory bandwidth pressure, thereby improving GPU data reading efficiency.

Compared with traditional INT4 quantization schemes, NVFP4 uses floating-point representation, providing better expressive ability for weights in different numerical ranges, effectively reducing quantization errors and maintaining model inference accuracy. For MoE (Mixture of Experts) models like Qwen3.5-35B-A3B, NVFP4 can better preserve the capability characteristics of each Expert, reducing the risk of performance degradation after quantization.

Relying on NVIDIA Blackwell architecture's native support for NVFP4, the model inference process can directly utilize Tensor Core for hardware acceleration, achieving higher Token generation speed and Batch throughput while ensuring generation quality. For scenarios in this project such as large model Agent, multi-round dialogue, code generation, and data analysis, it can achieve better deployment effect and operational efficiency under limited computing resources.

**Overall, Sehyo/Qwen3.5-35B-A3B-NVFP4 achieves a good balance between model capability, inference performance, memory footprint, and deployment cost, making it one of the important foundation models for DGX Spark local large model inference platform.**

#### 7.1.2 StepFun Step-3.7-Flash Model

This project integrates the **StepFun Step-3.7-Flash** large language model as one of the core text models for cloud deployment. Step-3.7-Flash is a new-generation high-performance inference model launched by StepFun. While maintaining strong comprehensive capabilities, it has been deeply optimized for inference speed, response latency, and deployment cost, providing stable and efficient model services for scenarios such as AI Agent, multi-round dialogue, code generation, knowledge Q&A, and content creation.

Compared with traditional large-parameter models, Step-3.7-Flash emphasizes **the balance between inference efficiency and actual application experience**. The model has fast first Token response speed (TTFT) and high Token output throughput, maintaining smooth interaction experience in complex task processing, especially suitable for application development needs in hackathon scenarios that require rapid iteration, real-time verification, and high-concurrency calls.

In this project, Step-3.7-Flash mainly undertakes core responsibilities such as hermes base model, scenic spot story search and source verification, and character feature generation. Benefiting from its excellent Chinese understanding ability and tool calling ability, the model can effectively support task decomposition, context reasoning, and result generation in multi-Agent workflows, providing stable and reliable intelligent core capability for the entire system.

At the same time, as an important model and computing power supporter of this AI hackathon, StepFun provides participating teams with high-performance model services and computing resource guarantees, enabling developers to focus more on product innovation and scenario implementation without paying too much attention to underlying infrastructure construction. With the capability support of Step-3.7-Flash, this project quickly completed prototype construction, functional verification, and effect optimization during the development cycle, significantly improving R&D efficiency and project delivery quality.

**Overall, Step-3.7-Flash achieves a good balance between model capability, inference efficiency, response speed, and engineering implementability, providing stable, efficient, and easy-to-integrate large model capability support for this project, making it an important infrastructure for the project's intelligent capabilities.**

#### 7.1.3 ACE Studio & StepFun ACE-Step 1.5 XL Model

This project selects **ACE-Step 1.5 XL**, jointly launched by ACE Studio and StepFun, as the core music generation model in the audio generation stage, providing high-quality soundtrack generation capability for the system. ACE-Step 1.5 XL integrates ACE Studio's technical accumulation in professional AI music generation and StepFun's capabilities in large model training and inference, automatically completing lyrics understanding, melody generation, arrangement creation, and vocal synthesis based on natural language descriptions, achieving end-to-end generation from creativity to complete music works.

Compared with traditional music generation solutions, ACE-Step 1.5 XL has significantly enhanced music structure consistency, vocal naturalness, and style control capability. The model can better understand user input themes, emotions, styles, and scene requirements, generating music works with high audibility and completeness while ensuring melody fluency. Whether it's pop, electronic, rock, Chinese style, or film soundtracks, it can achieve good style transfer and content creation effects.

In this project, ACE-Step 1.5 XL mainly undertakes tasks such as scene background music generation and audio material rapid production. Through natural language-driven music generation, the project can quickly obtain original music content that meets plot and scene requirements without professional music production team participation, significantly reducing content production threshold and improving creation efficiency. The model supports long-duration music content generation, meeting the needs of short dramas, comic videos, promotional videos, and digital content creation.

As one of the important generative AI capabilities in the StepFun ecosystem, ACE-Step 1.5 XL completes the complete AIGC content production pipeline from text generation, image generation to music generation for this project. Through collaborative work with large language models, multi-Agent systems, and visual generation models, it achieves a fully intelligent content production closed loop of "story generation — character shaping — visual creation — music production".

**Overall, ACE-Step 1.5 XL achieves a good balance between music quality, generation efficiency, and creative freedom, providing professional-level AI music creation capability for this project, making it an important part of building a multimodal content generation system.**

### 7.2 Common Tools and NVIDIA SDK

#### 7.2.1 vLLM: Text Large Model Inference Service Framework Selection

In this project, we regard large model inference capability as the core infrastructure of the entire system, rather than just simple model calls. Therefore, during the inference framework selection phase, we focused on multiple dimensions including inference performance, GPU utilization, long context support, multi-model compatibility, and future expansion capabilities. We ultimately chose **vLLM** as the unified large model inference engine, and adopted **Docker containerized deployment** to build the model service layer.

Common large model deployment solutions in the current open-source ecosystem mainly include **Ollama, llama.cpp, SGLang, and vLLM**. Among them, Ollama is more suitable for individual developers to quickly experience and deploy models locally, with advantages such as simple installation and low usage threshold. However, its positioning is more biased towards Model Runtime, and its capabilities are relatively limited in high-concurrency inference, GPU resource scheduling, and production-level service-oriented deployment. llama.cpp is known for its extreme lightweight and cross-platform capabilities, especially suitable for CPU, Mac, and edge device deployment scenarios. However, it is mainly built around the GGUF quantization ecosystem, with relatively limited support for high-end GPUs, ultra-large-scale models, and new-generation quantization formats.

SGLang and vLLM represent the development direction of large model inference frameworks for production environments in the current open-source community. SGLang has strong advantages in Structured Generation, Function Calling, and Agent workflow scenarios, while vLLM, with its mature engineering ecosystem, broader industry application verification, and deep adaptation to NVIDIA GPU ecosystem, has become one of the most widely used inference engines in enterprise-level private deployment and AI Agent platforms.

Considering project requirements and hardware environment, this project ultimately chose vLLM as the unified inference service framework. Firstly, vLLM provides an interface specification fully compatible with OpenAI API, enabling easy integration with Gateway, Agent Orchestrator, and business application layers, achieving unified encapsulation and management of model capabilities. Secondly, vLLM's core technology **PagedAttention** can significantly improve KV Cache utilization, effectively reducing memory fragmentation and memory waste, and fully releasing GPU computing resources in long context and multi-user concurrency scenarios. At the same time, vLLM supports **Continuous Batching**, which can dynamically merge inference requests from different users, improving GPU utilization and overall system throughput.

The core text model adopted in this project, **Sehyo/Qwen3.5-35B-A3B-NVFP4**, retains **MTP (Multi-Token Prediction)** weights, and vLLM already supports advanced inference optimization technologies such as **Speculative Decoding**, which can further improve Token generation speed and inference throughput. In addition, vLLM has good compatibility with NVIDIA Blackwell architecture and new-generation quantization technologies such as FP8 and FP4, enabling it to fully leverage the hardware performance advantages of the DGX Spark platform.

In terms of deployment, this project adopted the official **vLLM Docker image** for containerized deployment, rather than directly installing and running through Python environment. The main reason for this is that the large model inference environment involves many underlying dependencies such as CUDA, PyTorch, NCCL, FlashAttention, and Transformer Engine, and there are often complex compatibility relationships between different versions. Through Docker images, the entire inference environment can be standardized and encapsulated, ensuring that development, testing, and production environments remain consistent, significantly reducing deployment and operation complexity.

At the same time, the NVIDIA Blackwell GPU platform equipped in DGX Spark has high requirements for CUDA Runtime, driver version, and NVFP4 and other new features. The official image has already completed the adaptation and optimization of relevant dependency components, which can reduce environment configuration risks and improve system stability. Containerized deployment also provides good compatibility and scalability for subsequent migration to other NVIDIA GPU platforms, cloud GPU clusters, or Kubernetes environments.

In summary, the **vLLM + Docker** technical solution adopted in this project not only fully leverages the hardware performance advantages of DGX Spark and Blackwell GPU, but also achieves high-throughput, low-latency, and long-context large model inference services through core technologies such as PagedAttention, Continuous Batching, and Speculative Decoding. At the same time, this solution has good engineering capabilities, maintainability, and scalability, providing solid infrastructure support for future multi-model collaboration, multi-Agent workflows, and large-scale concurrent access scenarios.

#### 7.2.2 CUDA 13.0: GPU Computing Platform and CUDA Technology

To fully leverage the hardware performance of the NVIDIA DGX Spark platform, this project adopts **CUDA 13.0** as the underlying GPU computing runtime environment, providing unified high-performance computing infrastructure support for large model inference, multimodal content generation, and AI Agent workflows.

CUDA (Compute Unified Device Architecture) is a general-purpose parallel computing platform and programming model launched by NVIDIA, and is also the de facto standard GPU computing ecosystem in the current artificial intelligence field. Modern large model training and inference frameworks, including PyTorch, TensorRT, vLLM, FlashAttention, and Transformer Engine, are all built on the CUDA ecosystem. CUDA not only manages GPU resource scheduling and computing task execution, but also provides high-performance math libraries, communication libraries, and Tensor Core acceleration capabilities optimized for AI scenarios, serving as the foundational runtime platform for the entire AI technology stack.

Compared with early CUDA versions, CUDA 13.0 has been deeply optimized for NVIDIA's new-generation **Blackwell architecture GPU**, better supporting high-throughput computing requirements in large model inference scenarios. Especially in terms of support for low-precision computing formats such as FP8 and FP4, CUDA 13.0 provides complete software stack support, enabling new-generation quantization models to fully utilize Blackwell Tensor Core computing capabilities, significantly improving inference efficiency while ensuring model accuracy.

The core text model adopted in this project, **Sehyo/Qwen3.5-35B-A3B-NVFP4**, uses NVIDIA's proprietary NVFP4 quantization format, which achieves high compression ratio and high computing efficiency through the combination of FP4 weights and FP8 Scale. CUDA 13.0 can directly call the FP4 Tensor Core instruction set natively supported by Blackwell GPU, enabling the model inference process to complete large-scale inference tasks with lower memory footprint and higher computing throughput, thereby fully releasing DGX Spark's hardware potential.

In multi-GPU communication and inference services, CUDA 13.0 is deeply integrated with NCCL (NVIDIA Collective Communications Library), providing efficient data exchange capabilities for Tensor Parallel, Pipeline Parallel, and distributed inference scenarios. Although this project currently mainly runs in a single-node environment, future expansion to multi-GPU or GPU clusters can still build larger-scale model service capabilities based on CUDA and NCCL, reserving sufficient space for system expansion.

In addition, CUDA 13.0 maintains high compatibility with the current mainstream AI software ecosystem. Key components including PyTorch, vLLM, TensorRT-LLM, FlashAttention, and Transformer Engine have all completed adaptation. Through a unified software stack, the project can obtain a more stable runtime environment and continuous performance optimization support, reducing compatibility risks between different components.

From an engineering practice perspective, CUDA 13.0 not only provides underlying GPU computing capabilities but also undertakes the runtime infrastructure role of the entire AI inference platform. Tasks such as text generation, image generation, music generation, and multi-Agent collaborative inference in the project are all completed through CUDA scheduling GPU resources. With CUDA's high-performance parallel computing capabilities, the system can achieve higher model throughput, lower inference latency, and better resource utilization under limited hardware resources.

In summary, CUDA 13.0, as an important software infrastructure of the NVIDIA Blackwell platform, not only provides a stable and efficient GPU computing environment for this project, but also provides key support for the high-performance operation of NVFP4 quantization models, vLLM inference engines, and multimodal generation models. Through the collaborative optimization of CUDA 13.0 and DGX Spark platform, the project can fully leverage the advantages of the new-generation GPU architecture, providing powerful computing power guarantee for complex AI application scenarios.

#### 7.2.3 Image Generation Service Architecture Optimization — Qwen-Image-Edit-2511 Inference Solution Based on ComfyUI

In the visual content generation module of this project, we adopt **ComfyUI** as the image generation and editing workflow engine, driving the **Qwen-Image-Edit-2511** image editing model to complete core tasks such as character illustration generation, character three-view generation, comic page rendering, and visual consistency optimization.

In the first-generation hackathon project SparkScroll, the image generation service was mainly deployed based on **vLLM-Omni** and its official Docker image, calling the Qwen image model through a unified large model service framework to complete visual content generation. This solution has the advantages of simple deployment and unified interface, enabling rapid verification of product prototypes and generation pipelines. However, in actual production, we found that vLLM-Omni is more biased towards a unified inference service framework for multimodal large models, and its design goals mainly focus on model serviceization and interface standardization, rather than specialized optimization for Diffusion Models or image generation workflows. Therefore, in scenarios such as high-resolution comic page generation, complex character consistency control, and batch image processing, there is room for further optimization in GPU resource utilization and inference efficiency.

To solve this problem, this project introduces ComfyUI as a new-generation image generation execution framework. ComfyUI adopts a Node-Based visual workflow architecture, decomposing steps such as model loading, Prompt processing, sampler scheduling, LoRA loading, image editing, and post-processing into independent nodes, and organizing the execution flow through a computation graph. Compared with the traditional integrated inference call mode, ComfyUI can more flexibly manage model resources and computation processes, thereby effectively improving image generation efficiency.

In the actual deployment process, we built a dedicated image generation workflow for Qwen-Image-Edit-2511, standardizing and encapsulating links such as character setting, reference image input, style control, comic page generation, and image enhancement. At the same time, combined with ComfyUI's optimization capabilities for model caching, memory management, and inference flow, we significantly reduced performance losses caused by repeated model loading and repeated computation.

After actual testing, under the same hardware environment, the image generation solution based on vLLM-Omni in the first-generation SparkScroll project had an average generation time of approximately **5~7 minutes** for a single character three-view or comic page; after upgrading to the ComfyUI workflow, the single image generation time was stably reduced to **1~2 minutes**, with an overall inference efficiency improvement of approximately **3~5 times**. This optimization significantly shortened the waiting time from plot generation to visual output, improving the execution efficiency and user interaction experience of the entire multi-Agent creative process.

In addition to performance improvement, ComfyUI also brings stronger workflow orchestration capabilities. The character design Agent, storyboard Agent, comic generation Agent, and post-processing Agent in the project can all be called through standardized workflows, achieving modular management of the image generation process. If new diffusion models, ControlNet, LoRA, IP-Adapter, or video generation models are introduced in the future, extensions can be completed only by adjusting workflow nodes without large-scale modification of business logic code, significantly improving system maintainability and scalability.

From an architectural perspective, this upgrade reflects the project's evolution from "model call-driven" to "workflow-driven". The first-generation SparkScroll focused more on model capability verification, while this project pays more attention to the engineering efficiency of the content production pipeline. Through the introduction of ComfyUI, we not only achieved significant performance improvement, but also established a standardized generation system suitable for large-scale visual content production.

**Overall, the combination of ComfyUI and Qwen-Image-Edit-2511, while ensuring image quality and character consistency, significantly improves image generation efficiency and system expansion capabilities, providing important support for the project to achieve efficient and stable visual content production, and also laying a good technical foundation for subsequent integration of more multimodal generation models.**

#### 7.2.4 Script and Storyboard Generation Service — Multi-Agent Creative Architecture Based on Hermes Agent Framework and Shanyin Skill Service

In the content creation stage, this project adopts **Hermes Agent Framework** as the multi-Agent collaborative orchestration framework, and encapsulates **Shanyin Super Screenwriter Master** and **Shanyin Super Director Master** as professional Skill services under the Hermes system. Through HTTP API, it provides script generation and storyboard generation capabilities to upper-layer Agents, building an intelligent creative workflow for AI content production scenarios.

**Hermes** is a lightweight orchestration framework for building Agent applications in the era of large models. Its core concept is to decompose complex tasks into multiple service modules with professional capabilities through a **Agent + Skill + Tool** modular architecture, and achieve collaborative work among multiple Agents through unified task scheduling, context management, and capability calling mechanisms.

With the continuous enhancement of large language model capabilities, a single LLM can no longer meet the needs of complex business scenarios. Especially in content production fields such as film, comics, and short dramas, a complete creative process usually involves multiple professional links such as story planning, script writing, director storyboarding, art design, and music production. If only relying on a single model for end-to-end generation, problems such as unstable plot logic, drifting character settings, and insufficient camera language are likely to occur. Therefore, this project adopts Hermes as the intelligent orchestration layer, dynamically calling professional skills of screenwriters and directors through Agents to complete complex creative tasks.

[Shanyin Super Screenwriter Master]: https://github.com/Shanyin-ai/shanyin-screenwriting-master

As a script creation Skill for Hermes, it is mainly responsible for tasks such as story planning, worldview construction, character setting, and script generation.

This Skill provides standardized calling interfaces to Hermes through HTTP API, enabling Agents to call screenwriting capabilities to complete:

- Story background design;
- Character relationship construction;
- Plot structure planning;
- Plot development design;
- Complete script generation.

By encapsulating screenwriting capabilities as independent Skills, the system does not need to solidify complex creative logic in the main Agent, but can dynamically call professional capabilities according to task requirements, achieving modularization and serviceization of creative capabilities.

[Shanyin Super Director Master]: https://github.com/Shanyin-ai/shanyin-director-master

After script generation, Hermes Agent will further call the **Shanyin Super Director Master** Skill to convert literary scripts into director-level storyboard data suitable for visual generation.

This Skill is mainly responsible for:

- Plot scene decomposition;
- Camera planning;
- Shot design;
- Character action description;
- Emotion expression analysis;
- Visual composition design;
- Storyboard script generation.

Output structured scripts and storyboard content through HTTP service interfaces, providing precise Prompt and scene control information for the subsequent Qwen-Image-Edit-2511 image generation model.

Compared with directly generating image descriptions through large language models, this solution adds a professional conversion layer of "screenwriter → director → visual generation", making AI-generated content more in line with film production processes, improving plot consistency, camera continuity, and character performance capabilities.

The **Shanyin Super Screenwriter Master** and **Shanyin Super Director Master** adopted in this project are both open-source software projects. The project author [Shanyin](https://github.com/Shanyin-ai) is a well-known AIGC art creator, independent director, and screenwriter in China, with rich practical experience in AI content creation. He has won honors such as **2025 Chuxin Award Top 10 AIGC Figures of the Year, Vaca Award Top Chinese AI Visual Creative Author, and Extraordinary Award Annual AI CREATOR 100 Creator**, and participated in the construction of innovation ecosystems such as Shenzhen AIGC Super Creation Laboratory and Langyuan AI Super Creation Ecological Matrix.

He has long focused on AIGC art creation, intelligent content production processes, and AI creative tool research and development, and as a creator representative of multiple mainstream AI creation platforms, continues to promote the application of AI technology in film, visual art, and content production fields.

*The project team would like to thank **@Shanyin** for open-sourcing and contributing professional creative tools, which have provided important technical support for this project in intelligent script generation, director storyboard planning, and AI content production process optimization. With the help of these excellent open-source capability components, this project can further improve the multi-Agent collaborative creative system and accelerate the exploration and practice of AI-native content production applications.*

---

## VIII. Project Operation Manual

### 8.1 Creating a New Work

![page_01](../samples/opr/001.png)

Enter the scenic spot name in the input field. The system has built-in all 359 national 5A scenic spots provided by the Ministry of Culture and Tourism. For example, when you input "Hua Shan", the following scenic spots will appear:

![page_01](../samples/opr/002.png)

At this point:

- Select video duration: 1 minute, 3 minutes, 5 minutes
- Select target audience: General, Children
- Select video tone: Warm, Fantasy, Suspense
- Select art style: Chinese Ink, Children's Picture Book, Modern Illustration
- Select voice: Male, Female
- Select speech rate: 0.8, 1.0, 1.2
- Enable Shanyin Super Screenwriter Master Skill for script generation and Shanyin Super Director Master Skill for storyboard generation.
- If no related stories are found for the scenic spot, users can also prepare their own stories.

Click [Start Generation], and WanderInk begins working.

During video production, the [Generation Queue] will display:

![page_01](../samples/opr/003.png)

### 8.2 S0 Legend: Story Generation

When this step is completed, the generated story content and information sources will be displayed below.

![page_01](../samples/opr/004.png)

### 8.3 S1 Script Generation

When this step is completed, the script is generated and character settings are finalized. The following information will be displayed, showing four characters: Bai Suzhen, Xu Xian, Fahai, and Xiaoqing.

![page_01](../samples/opr/005.png)

### 8.4 S2 Storyboard Generation

When this step is completed, the storyboard text is generated. The following 9 pages of storyboard information will be displayed:

![page_01](../samples/opr/006.png)

![page_01](../samples/opr/007.png)

![page_01](../samples/opr/008.png)

![page_01](../samples/opr/009.png)

![page_01](../samples/opr/010.png)

### 8.5 S3 Character Generation

![page_01](../samples/opr/011.png)

When this step is completed, three-view information will be displayed:

![page_01](../samples/opr/012.png)

Click [View Details] to see three-view information for each character:

Bai Suzhen:

![page_01](../samples/opr/013.png)

Xu Xian:

![page_01](../samples/opr/014.png)

Fahai:

![page_01](../samples/opr/015.png)

Xiaoqing:

![page_01](../samples/opr/016.png)

### 8.6 S4 Comic Page Generation

When this step is completed, the system will display the following comic page information:

![page_01](../samples/png/003/page_01.png)
![page_02](../samples/png/003/page_02.png)
![page_03](../samples/png/003/page_03.png)
![page_04](../samples/png/003/page_04.png)
![page_05](../samples/png/003/page_05.png)
![page_06](../samples/png/003/page_06.png)
![page_07](../samples/png/003/page_07.png)
![page_08](../samples/png/003/page_08.png)
![page_09](../samples/png/003/page_09.png)

### 8.7 S5 Dubbing Generation

When this step is completed, both images and audio are ready:

![page_01](../samples/opr/017.png)

![page_01](../samples/opr/018.png)

![page_01](../samples/opr/019.png)

![page_01](../samples/opr/020.png)

![page_01](../samples/opr/021.png)

### 8.8 S6 Video Composition

When this step is completed, the audio comic video is finished:

![page_01](../samples/opr/022.png)

The system also provides video download, image PDF download, and image package download functions.

---

## IX. Project Completeness

- **Functionally Complete**: All S0–S6 seven steps are fully implemented; frontend supports creating, previewing, editing, redrawing, re-dubbing, downloading MP4 video/PDF/image package;
- **Frontend-Backend Complete**: Backend FastAPI + frontend React + Vite + Tailwind, supporting multi-user, queue, and share links;
- **Stable Operation**: 300+ unit test cases covering core paths; real end-to-end has produced final products for Leifeng Pagoda, Yellow Crane Tower, etc.;
- **Documented**: Including PRD, product plan, deployment manual, user manual, decision records (decisions 0001–0006), full repository research report;
- **Demo-ready**: After inputting scenic spot name, frontend displays real-time progress and time consumption for each step, with final playable MP4.

---

## X. Differences from SparkScroll (1st Spark Hackathon)

This project upgrades the philosophy from [SparkScroll](https://github.com/zhanghui-china/SparkScroll) with professional enhancements:

| Dimension | 1st SparkScroll | This WanderInk |
|---|---|---|
| Output Format | Pure comic images | Audio comic short video (visuals + narration + BGM) |
| Script Source | LLM + prompt direct generation | Film industry "Screenwriter Master / Director Master" skills |
| Creative Process | Fully automated black box | Step-by-step intervenable, redrawable, re-dubbable, reviewable |
| Frontend Role | Backend gateway focused | Frontend as important display entry, supporting multi-user collaboration |
| Character Consistency | Initial solution | Three-view reference images + fixed art style + consistency checkpoint |
| Platform Adaptation | Concept verification | DGX Spark local LLM + image + TTS + music full-stack closed loop |
| Team | Changed members except team leader | Introduced professionals with film skill, frontend/backend, ComfyUI engineering experience |

In one sentence: **The team leader remains the same, but the product format, technical depth, and team configuration have all undergone qualitative changes.**

---

## XI. Evaluation Criteria Comparison Table

| Evaluation Dimension | Weight | Project Corresponding Content |
|---|---|---|
| Practicality, Industry Value & Technical Innovation | 25% | Solves the pain point of IP development for small and medium scenic spots ("long cycle, high cost"); end-to-end audio comic solution has industry pioneering nature; fully utilizes DGX Spark unified memory for single-machine closed loop |
| Agent Integration & Model Optimization Technical Depth | 25% | Multi-Agent collaboration (Story/Script/Director/Character/Image/Voice/Music/Composer); Hermes "Screenwriter Master/Director Master" skill injection; three-view consistency solution; TTS truncation detection and silent fallback |
| Project Completeness | 20% | S0–S6 functionally complete; FastAPI + React frontend-backend complete; 300+ test cases; PRD/deployment manual/user manual/decision records complete; live demo ready |
| Platform Adaptability | 15% | DGX Spark 128GB unified memory time-slice loading; vllm local LLM; ComfyUI image pipeline; Qwen3-TTS speech; Stepfun ACE-STEP music; Stepfun step-3.7-flash text model |
| Demo Effect | 10% | Web real-time progress + three-view/page-by-page preview + final MP4 playback; Demo script 3-minute complete closed loop |
| Competition Essay | 5% | "Ten Days' Talk" development journey, decision records, bug fixes and recovery processes fully documented in `web/docs/decisions/` and README update notes |

---

## XII. Project Team and Updates

### 12.1 Project Team

| Member | Responsibility |
|---|---|
| [Zhang Xiaobai](https://github.com/zhanghui-china) | Team Leader, Project Planning, Environment Deployment, Project Testing, Documentation |
| [Nancy](https://github.com/nancysxy000) | Member, Documentation, Scenic Spot Story Generation, DEMO Video Production |
| [Qing Ta](https://github.com/DoubleCore) | Member, Skill Development, Hermes Integration, Screenwriter/Director Skill Iteration |
| [Ban Du Wu Zi](https://github.com/Bandukids) | Member, ComfyUI Service Deployment & Development, Image/Audio Pipeline |
| [Hun Tun](https://github.com/nativeas) | Member, Web Frontend/Backend Development, Frontend Interaction & Multi-user Design |

### 12.2 Project Updates

[2026.7.21] **Zhang Xiaobai** wrote WanderInk Experience Manual, see: https://zhuanlan.zhihu.com/p/2062795404199073593. **Nancy** created the DEMO explanation video, see: https://www.bilibili.com/video/BV1ymK865Et1. **Ban Du Wu Zi** created the team photo.

[2026.7.20] **Nancy** completed project demonstration PPT preparation.

[2026.7.19] **Zhang Xiaobai**, **Ban Du Wu Zi** completed deployment documentation, project introduction documentation, etc.

[2026.7.18] Project members conducted a series of test verifications on the project. After **Hun Tun** fixed Web-end bugs, Web-end code was frozen.

[2026.7.15] Project members conducted intensive testing on the WanderInk product. During testing, the team found that the Spark device suddenly became unreachable remotely. **Zhang Xiaobai** discovered that Spark had automatically shut down. With **Qing Ta**'s support, **Hun Tun** changed LLM script and storyboard generation to call Hermes skills.

[2026.7.14] **Zhang Xiaobai** assigned tasks to team members. **Ban Du Wu Zi** and **Hun Tun** focused on BugFix and code optimization, while others focused on testing the project code, aiming to release a version of documentation and code by July 18. Text models began using the sponsor-provided step-3.7-flash model. All developers submitted code to this repository.

[2026.7.13] **Zhang Xiaobai** discovered that image editing model calls produced all-black results. After **Ban Du Wu Zi** checked, it was found that adding sage attention acceleration during startup caused this issue, which was resolved by reverting to flash-attn acceleration. Zhang Xiaobai repackaged the ComfyUI HTTP service and tested it successfully.

[2026.7.12] The WanderInk team participated in the morning hackathon online training camp and started text live broadcasting in the group. **Ban Du Wu Zi** configured and started the Ollama local model on Spark.

[2026.7.10] **Hun Tun** submitted the project frontend prototype.

[2026.7.7] **Nancy** submitted code and documentation for LLM-generated scenic spot stories. Due to team member **LZH**'s withdrawal, the WanderInk team recruited new member **Hun Tun** (from Wuxi).

[2026.7.4] **Zhang Xiaobai** installed Hermes on the DGX Spark device, see: https://zhuanlan.zhihu.com/p/2056830749530142643

[2026.7.3] **Zhang Xiaobai** attempted to package **Ban Du Wu Zi**'s ComfyUI service as an HTTP service.

[2026.7.2] The WanderInk team held the second video conference (LZH was unable to attend). The project direction of **audio comic** was basically determined.

[2026.7.1] **Ban Du Wu Zi** completed writing the ComfyUI installation, deployment, and usage documentation. Zhang Xiaobai completed the download and deployment attempt of Stepfun's Step-3.7-Flash-GGUF model, see: https://zhuanlan.zhihu.com/p/2055024035302471223

[2026.6.30] **Qing Ta** researched Shanyin's Screenwriter Master and Director Master skills, attempted to iterate prompts, and installed Claude Code on Spark with successful results. **Nancy** was experimenting with ComfyUI's Lora models. **Ban Du Wu Zi** conducted ComfyUI environment verification for single-image, double-image, and triple-image editing, tuned TTS speech generation, and suggested **Nancy** find some new model Loras.

[2026.6.29] **Zhang Xiaobai** attempted to download and deploy Stepfun's Step-3.7-Flash-NVFP4 model. The next day, he announced that both docker and conda methods failed to start due to insufficient memory.

[2026.6.28] **Ban Du Wu Zi** deployed the ComfyUI environment on Spark. He also spent a long time compiling flash-attention from source, generated music using ACE-STEP XL Turbo, and began testing and verifying ComfyUI image generation. **Qing Ta** started researching Eazo (https://creator.eazo.ai/apps)

[2026.6.27] **Zhang Xiaobai** purchased an intranet penetration cloud service, providing ssh and http channel methods for team members to share his Spark device. Zhang Xiaobai created this repository.

[2026.6.26] The WanderInk team recruited new member **Qing Ta** and held the first video conference (Qing Ta was unable to attend). Brainstorming session. Team members joined a Feishu enterprise organization and a Feishu group with OpenClaw robot (**LZH** was unable to join).

[2026.6.25] WanderInk team name confirmed, recruiting new member **Ban Du Wu Zi** (from Wuxi).

[2026.6.24] WanderInk team members recruited, **Zhang Xiaobai** (Zhang Hui, from Nanjing), **Nancy** (Su Xiaoye, from Chengdu), **LZH** (from Hangzhou) began discussing project direction.

---

## XIII. Easter Egg — WanderInk Generated Works Showcase

### 13.1 Nanxun Ancient Town: Four Elephants, Eight Bulls, Seventy-two Dogs

![page_01](../samples/png/001/page_01.png)

![page_02](../samples/png/001/page_02.png)

![page_03](../samples/png/001/page_03.png)

![page_04](../samples/png/001/page_04.png)

![page_05](../samples/png/001/page_05.png)

![page_06](../samples/png/001/page_06.png)
![page_07](../samples/png/001/page_07.png)
![page_08](../samples/png/001/page_08.png)

![page_09](../samples/png/001/page_09.png)
![page_10](../samples/png/001/page_10.png)

![page_11](../samples/png/001/page_11.png)
![page_12](../samples/png/001/page_12.png)
![page_13](../samples/png/001/page_13.png)
![page_14](../samples/png/001/page_14.png)
![page_15](../samples/png/001/page_15.png)
![page_16](../samples/png/001/page_16.png)
![page_17](../samples/png/001/page_17.png)
![page_18](../samples/png/001/page_18.png)
![page_19](../samples/png/001/page_19.png)
![page_20](../samples/png/001/page_20.png)

![page_21](../samples/png/001/page_21.png)
![page_22](../samples/png/001/page_22.png)

### 13.2 Lijiang: Wooden City Without Walls

![page_01](../samples/png/002/page_01.png)
![page_02](../samples/png/002/page_02.png)
![page_03](../samples/png/002/page_03.png)
![page_04](../samples/png/002/page_04.png)
![page_05](../samples/png/002/page_05.png)
![page_06](../samples/png/002/page_06.png)
![page_07](../samples/png/002/page_07.png)
![page_08](../samples/png/002/page_08.png)
![page_09](../samples/png/002/page_09.png)
![page_10](../samples/png/002/page_10.png)
![page_11](../samples/png/002/page_11.png)
![page_12](../samples/png/002/page_12.png)
![page_13](../samples/png/002/page_13.png)
![page_14](../samples/png/002/page_14.png)
![page_15](../samples/png/002/page_15.png)
![page_16](../samples/png/002/page_16.png)
![page_17](../samples/png/002/page_17.png)
![page_18](../samples/png/002/page_18.png)
![page_19](../samples/png/002/page_19.png)
![page_20](../samples/png/002/page_20.png)
![page_21](../samples/png/002/page_21.png)
![page_22](../samples/png/002/page_22.png)

---

## XIV. Conclusion

WanderInk is not just an "AI-generated video" toy, but a **professional, intervenable, and deployable** multimodal creative system for scenic spot cultural IP production. It fully leverages NVIDIA DGX Spark's unified memory advantages, integrating Stepfun large models, Hermes professional skills, ComfyUI image pipelines, and FFmpeg synthesis capabilities, representing a complete practice of "AI creative industrialization".

> Scenic spot name in, audio comic out. WanderInk, making every landscape have a story to tell.