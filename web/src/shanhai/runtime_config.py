# src/shanhai/runtime_config.py
"""运行时配置覆盖:全局默认 + 按用户覆盖 + 按环节覆盖,叠加到 .env 基线的 Settings 之上。

四层叠加,后者压前者,只有"已设置(非 None)"的字段才覆盖:
    Settings()  (.env / 进程环境变量,必填基线)
       └─ 叠加 config.json.global          (全局默认覆盖)
            └─ 叠加 config.json.users[owner]    (该作品归属者的个人覆盖,仅 LLM)
                 └─ 叠加 config.json.stages[stage] (该环节覆盖,优先级最高)

users 层刻意排在 stages 之下:管理员为某个环节钉死的配置(如"S4 必须走本机 shim")不该被
个人偏好盖掉。它也只开放 LLM 字段,理由见 UserOverride 的 docstring。

持久化于 cwd 根的 config.json(gitignore,含明文密钥),原子写发布。"""
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from shanhai import store
from shanhai.config import Settings
from shanhai.schema import Project

# 各环节用到的 client 组:前端据此只渲染相关字段组,校验 PUT 的 stage 名。
# S6 纯 ffmpeg 无端点,故不在此表。
STAGE_CLIENTS: dict[str, tuple[str, ...]] = {
    "s0": ("llm",),
    "s1": ("llm",),
    "s2": ("llm",),
    "s3": ("llm", "image"),
    "s4": ("image",),
    "s5": ("tts", "music"),
}

# 密钥字段:GET 脱敏、PUT 哨兵语义均据此集合区分密钥与普通字段。
SECRET_FIELDS = {"api_key", "llm_api_key", "image_api_key", "tts_api_key", "music_api_key"}

# GET 脱敏掩码 / PUT 哨兵:表示密钥"已配置但不回显" / "保持已存值不变"。二者都不会被写成真实密钥。
MASK = "••••••"
SENTINEL = "__UNCHANGED__"

# 覆盖存储路径:放 cwd 根(不在 projects/ 下,不被 /files 挂载暴露)。
# 延迟求值(每次读 os.getenv)而非 import 期冻结:api.py 的 load_env() 晚于本模块 import,
# 只写在 .env 里的 SHANHAI_CONFIG_PATH 若在 import 期读取会被静默忽略(进程环境变量则生效,行为不一致)。
def _config_path() -> Path:
    return Path(os.getenv("SHANHAI_CONFIG_PATH", "config.json"))

# 原子写发布锁:与 store.save 同构(唯一临时名 + os.replace)。
_WRITE_LOCK = threading.Lock()


class ConfigOverride(BaseModel):
    """单层覆盖:LLM/图像/TTS 全部字段,均 Optional(None=继承下层)。
    extra="forbid" 拒绝 readonly 等越权字段(写入层与读取层共用此校验)。"""
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    api_key: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_provider: Literal["openai", "ollama"] | None = None
    llm_timeout: float | None = None
    image_base_url: str | None = None
    image_api_key: str | None = None
    image_model: str | None = None
    image_api_mode: str | None = None
    image_size: str | None = None
    image_lora_model: str | None = None
    tts_base_url: str | None = None
    tts_api_key: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_voices: str | None = None
    tts_voice_en: str | None = None
    music_base_url: str | None = None
    music_api_key: str | None = None
    music_model: str | None = None


class UserOverride(BaseModel):
    """按用户覆盖:**只开放 LLM 五个字段**,图像/配音/配乐刻意不给。

    不是嫌麻烦——image_base_url 一旦被改成非 loopback,两处按 hostname 的判定会同时静默失效:
    providers/_http.py 的 local_backend_guard(全局单并发锁直接不生效)与本模块的
    image_concurrency(S3/S4 扇出从 1 变 2)。方向叠加恶化,且无日志无告警(见
    docs/deploy-gateway.md §0)。把"用户不能改 image 端点"做成 extra="forbid" 的结构约束,
    塞任何非 llm_* 字段直接 422,而不是留一条要靠人记住的纪律。

    TTS/音乐同样不开:它们与 image 共用那把本地锁,且音色列表(/api/meta)与音色样本注册
    走的是请求上下文而非作品 owner,开放后会出现"表单列的是 A 的音色、生成时用 B 的端点"
    这类静默失配。保持全站统一最简单也最不容易错。"""
    model_config = ConfigDict(extra="forbid")

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_provider: Literal["openai", "ollama"] | None = None
    llm_timeout: float | None = None


class AppConfig(BaseModel):
    """config.json 的顶层结构:全局默认 + 按用户覆盖 + 按环节覆盖。
    populate_by_name=True 让 global_ 既可用别名 global 也可用字段名填充。
    users 的键是登录名,不校验是否为真实账号——校验会让配置读写耦合上账号存储,
    而账号删除后留一条无人使用的覆盖是无害的。"""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    global_: ConfigOverride = Field(default_factory=ConfigOverride, alias="global")
    users: dict[str, UserOverride] = Field(default_factory=dict)
    stages: dict[str, ConfigOverride] = Field(default_factory=dict)


def load_overrides() -> AppConfig:
    """读 config.json 反序列化为 AppConfig。文件缺失 → 空 AppConfig();
    其它读取失败(非 UTF-8/权限/是目录等)或不合 schema → 打印告警并返回空(不 brick 生成)。"""
    path = _config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AppConfig()
    except (OSError, UnicodeDecodeError) as e:  # 权限/是目录/非 UTF-8 等:回退空,避免 brick 全部入口
        print(f"[runtime_config] 读取 {path} 失败,回退空配置:{e}")
        return AppConfig()
    try:
        return AppConfig.model_validate_json(text)
    except Exception as e:  # 损坏或不合 schema:告警后回退空配置,生成流程不受影响
        print(f"[runtime_config] 解析 {path} 失败,回退空配置:{e}")
        return AppConfig()


def save_overrides(cfg: AppConfig) -> None:
    """原子写发布 config.json(整份替换)。by_alias=True 确保 global_ 序列化回别名 global。"""
    with _WRITE_LOCK:
        store.atomic_write_text(_config_path(), cfg.model_dump_json(indent=2, by_alias=True))


def update_overrides(mutate: Callable[[AppConfig], AppConfig]) -> AppConfig:
    """在写锁内原子地 读→变换→写,避免并发 PUT 的读-改-写(read-merge-write)丢更新——
    否则两个并发 PUT 会互相覆盖,甚至把刚设置的密钥静默回退成旧值。返回落盘后的新配置。"""
    with _WRITE_LOCK:
        new = mutate(load_overrides())
        store.atomic_write_text(_config_path(), new.model_dump_json(indent=2, by_alias=True))
        return new


def _apply_layer(updates: dict, layer: dict) -> None:
    """叠加一层覆盖。若这一层换了 llm_base_url 却没写 llm_provider,provider 不再从下层
    继承,而是回到 openai。

    llm_provider 描述的是"怎么跟这个端点说话",它属于**端点**、不属于用户。逐字段合并会把
    A 端点的协议配到 B 端点上——2026-08-08 线上正是如此:stages.s0/s1/s2 指向 hermes-agent
    (纯 OpenAI 兼容)却没写 provider,某用户在自己那层选了 ollama,于是拿 Ollama 原生协议去
    打 hermes,POST /api/chat 得到 404,而界面上两处配置各看各的、完全看不出冲突。

    为什么回落到 openai 而不是保持继承:"没声明协议的端点按 OpenAI 兼容处理"既是本项目的
    历史假设(ollama 分支是后加的),也正是现存所有 stage 覆盖隐含的那个。
    ⚠️ 必须**显式写入** openai 而不是只 pop 掉:光 pop 会回落到 base(.env 的
    SHANHAI_LLM_PROVIDER),那仍然是"别的端点的协议",同一个 bug 换一层再犯一次。"""
    if "llm_base_url" in layer and "llm_provider" not in layer:
        updates["llm_provider"] = "openai"
    updates.update(layer)


def resolve_settings(
    stage: str | None = None,
    cfg: AppConfig | None = None,
    base: Settings | None = None,
    owner: str = "",
) -> Settings:
    """把 global + users[owner] + stages[stage] 覆盖依次叠加到 base(默认 Settings())之上,
    返回该环节生效的 Settings。用 exclude_none(None=继承,避免显式 null 击穿必填字段);
    model_copy 不重校验 Literal(provider 合法性由写入/读取层的模型校验保证)。
    base 可注入,便于单测隔离真实 .env。

    owner 传作品归属者(Project.owner),**不是当前操作者**:admin 帮别人重跑某一步时仍用
    作品主人的模型,否则同一部作品的 S1 与 S4 会走两套模型、文风画风对不上。
    owner="" 是默认值,故历史无主项目与 CLI(无登录态)自然跳过 users 层回落到 global——
    与写侧 _may_edit 的"owner 为空视为无主"是同一种处理。

    ⚠️ 叠加不是纯粹的逐字段覆盖:llm_provider 跟着 llm_base_url 走,理由见 _apply_layer。"""
    base = base or Settings()
    cfg = cfg or load_overrides()
    updates: dict = {}
    _apply_layer(updates, cfg.global_.model_dump(exclude_none=True))
    if owner and owner in cfg.users:
        _apply_layer(updates, cfg.users[owner].model_dump(exclude_none=True))
    if stage is not None and stage in cfg.stages:
        _apply_layer(updates, cfg.stages[stage].model_dump(exclude_none=True))
    return base.model_copy(update=updates) if updates else base


REMOTE_IMAGE_CONCURRENCY = 2  # tu-zi 实测扛不住 s4_pages.CONCURRENCY(3)路并发:2026-07-16 复现
# 3 路并发时 images/edits 端点约 2/3 请求以 500/RemoteProtocolError(服务端断连)失败,
# 单独串行重放同样的请求则全部成功——是上游并发容量问题,不是 prompt/参考图/审核问题。


def image_concurrency(s: Settings) -> int:
    """本地 shim(127.0.0.1/localhost)背后是团队共用的单张 GPU,并发请求只会排队/互相拖慢
    甚至冲突,强制串行;远程云端 API(如 tu-zi)可以并发出图,但要封顶(见上)。
    api._pipeline/_run_step 与 cli.step/run 共用,保证 s3/s4 并发数一致跟随后端(本地串行、远程并发)。"""
    base_url, _ = s.image_endpoint
    host = urlparse(base_url).hostname or ""
    return 1 if host in ("127.0.0.1", "localhost") else REMOTE_IMAGE_CONCURRENCY


# ---------- PUT 合并 + GET 脱敏视图(HTTP 层与 CLI 共用同一契约) ----------

_Override = TypeVar("_Override", ConfigOverride, UserOverride)


def merge_override(incoming: _Override, existing: _Override) -> _Override:
    """按 PUT 的"部分更新"语义把 incoming 合并进 existing:
    - 只有 incoming 里**实际发来**的字段(model_fields_set,经 exclude_unset 提取)才参与合并,
      未发来的字段一律保留 existing 值——故前端不渲染的字段(如共享 base_url/api_key)不会被静默抹掉。
    - 密钥字段:值=SENTINEL 或 MASK→保持已存值(掩码回填不会被当成真密钥);值=""→清除(继承);其它→更新。
    - 非密钥字段:值=""或显式 null→清除(继承);其它→更新。

    对 ConfigOverride 与 UserOverride 通用:逻辑只依赖字段名与 SECRET_FIELDS,不依赖具体模型,
    故用 type(existing) 构造回同一类型,而不是抄第二份合并逻辑(两份迟早漂)。"""
    merged = existing.model_dump()
    for field, val in incoming.model_dump(exclude_unset=True).items():
        if field in SECRET_FIELDS:
            if val in (SENTINEL, MASK):
                continue                       # 保持已存值不变
            merged[field] = None if val == "" else val
        else:
            merged[field] = None if val == "" else val
    return type(existing)(**merged)


def _is_empty_override(ov: ConfigOverride | UserOverride) -> bool:
    return all(v is None for v in ov.model_dump().values())


def _merge_layer(incoming: dict[str, _Override], existing: dict[str, _Override],
                 empty: Callable[[], _Override]) -> dict[str, _Override]:
    """users / stages 这类"键 → 覆盖"字典的部分更新:未在 incoming 出现的键保留(裸 API 部分
    PUT 不会误删其它条目),出现的键逐字段合并,合并后全空的条目删除(清空全部字段即删除该覆盖)。"""
    merged = dict(existing)
    for key, ov in incoming.items():
        m = merge_override(ov, existing.get(key, empty()))
        if _is_empty_override(m):
            merged.pop(key, None)
        else:
            merged[key] = m
    return merged


def apply_put(existing: AppConfig, incoming: AppConfig) -> AppConfig:
    """把一次 PUT(incoming)按部分更新语义合并进 existing:global 逐字段合并,
    users 与 stages 走同一套 _merge_layer(保留未提及的键、逐字段合并、空条目剪枝)。"""
    return AppConfig(
        global_=merge_override(incoming.global_, existing.global_),
        users=_merge_layer(incoming.users, existing.users, UserOverride),
        stages=_merge_layer(incoming.stages, existing.stages, ConfigOverride),
    )


def override_view(ov: ConfigOverride | UserOverride) -> dict:
    """把一层覆盖脱敏为 GET 视图:密钥字段 已设→MASK / 未设→None;非密钥字段 原值或 None。
    对两种覆盖模型通用:只按字段名查 SECRET_FIELDS,不依赖具体模型。"""
    return {
        k: (MASK if v is not None else None) if k in SECRET_FIELDS else v
        for k, v in ov.model_dump().items()
    }


def defaults_view() -> dict:
    """.env 基线视图,供前端做 placeholder 继承提示。
    非密钥字段=实际值;密钥字段=bool(是否已配置)。字段集与 ConfigOverride 对齐。"""
    base = Settings()
    return {
        field: (bool(getattr(base, field, None)) if field in SECRET_FIELDS
                else getattr(base, field, None))
        for field in ConfigOverride.model_fields
    }


# "大师"skill 只存在于 hermes-agent 后端(/screenwriter-master、/director-master 发给别的
# 模型会被当乱码),故每个环节各自绑定自己的 skill 名,用于开关判定与引擎记录。
MASTER_SKILLS = {"s1": "编剧大师", "s2": "导演大师"}
MASTER_SKILL_BACKEND = "hermes-agent"


def default_track_voice(p: Project, stage_settings: Settings) -> str:
    """附加语种轨(英文)配音的**默认**音色。

    自定义音色是用户自己的声音,同一个作品的英文视频也该是同一个人——用户原话"音色在每个
    项目中都是影响到这个项目的所有配音的"。此前英文轨恒用配置层的预置音色:s5_audio 的解析
    链是 `params.voice_en or 传入默认值`,而 params.voice_en **没有任何入口能设置**
    (换音色端点只收 voice、新建表单也只提交 voice),永远是空串,于是同一部作品中文是本人的
    声音、英文是另一个人。

    判据是"不在配置层预置音色列表里"。两个不选的方案:
    - `voice.startswith("clone:")`:那个前缀是上游 TTS 返回的约定,后端从未强制过,拿它当
      判据是猜(今天已经因为同一个理由否掉过一次)。
    - 音色样本索引命中:那份索引只覆盖此后上传的样本,线上 6 部老作品的句柄不在里面。
    预置列表是配置的一部分,对新老作品一律有效。

    **预置中文音色不继承**:配置里专门有 tts_voice_en,就是因为中文 speaker 念英文效果差;
    只有克隆音色才有"这是本人的声音、跨语种也该是他"这个诉求。

    返回值只是**默认值**:s5_audio 里 `params.voice_en or voice` 的回落链不动,voice_en
    一旦被显式设过就压过这里——那是"克隆音色念英文效果不好"时的逃生口。"""
    voice = p.params.voice
    if voice and voice not in stage_settings.tts_voices_list:
        return voice
    return stage_settings.tts_voice_en or stage_settings.tts_voice


def use_master_skill(p: Project, stage_settings: Settings, stage: str) -> bool:
    """该环节是否调"大师"skill,并把**本次实际用的引擎**记进 p.status[f"{stage}_engine"]。

    判定仍是老规则:作品勾了开关**且**该环节后端确为 hermes-agent;开关开了但后端不是
    hermes 时静默退化为普通生成,不报错(退化比失败对用户更有价值)。

    之所以顺带记录:退化原先只 print 一行到服务端 stdout,而 project.json 里不存任何模型
    或 skill 信息,事后完全无法回答"这部作品到底走没走 skill"——实测正是这个盲区让一批
    质量可疑的旁白无从归因。记录跟着作品走,status 已被 api._serialize 整体透传给前端,
    因此这里写完即可见,不必再动 _serialize(那里漏一行就静默失效,是本仓库的老坑)。

    只写内存里的 p.status,落盘交给调用方原有的 store.save / _locked_save。"""
    skill = MASTER_SKILLS[stage]
    model = stage_settings.llm_model
    if not p.params.master_skill:
        p.status[f"{stage}_engine"] = f"{model} · 普通"
        return False
    if model == MASTER_SKILL_BACKEND:
        p.status[f"{stage}_engine"] = f"{model} · {skill}"
        return True
    p.status[f"{stage}_engine"] = f"{model} · 普通(已忽略大师开关:后端非 {MASTER_SKILL_BACKEND})"
    print(f"⚠️ 大师 skill 需 {MASTER_SKILL_BACKEND} 后端,当前 {stage.upper()} 用 {model},已忽略该开关")
    return False


def config_view(readonly: bool, viewer: str = "", viewer_is_admin: bool = False) -> dict:
    """GET /api/config 的完整响应体(也用作 PUT 成功后的回显)。所有密钥字段脱敏。

    users 层做**行级过滤**:管理员看全部,其他人只看得到自己那一条——别人配了什么模型
    不该互相可见。global/stages 维持对所有登录用户可见(既有行为,且前端要靠它显示继承值)。"""
    cfg = load_overrides()
    users = cfg.users if viewer_is_admin else {u: ov for u, ov in cfg.users.items() if u == viewer}
    return {
        "readonly": readonly,
        "stage_clients": {st: list(clients) for st, clients in STAGE_CLIENTS.items()},
        "defaults": defaults_view(),
        "global": override_view(cfg.global_),
        "users": {u: override_view(ov) for u, ov in users.items()},
        "stages": {st: override_view(ov) for st, ov in cfg.stages.items()},
    }
