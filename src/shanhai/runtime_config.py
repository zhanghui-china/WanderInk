# src/shanhai/runtime_config.py
"""运行时配置覆盖:全局默认 + 按环节覆盖,叠加到 .env 基线的 Settings 之上。

三层叠加,后者压前者,只有"已设置(非 None)"的字段才覆盖:
    Settings()  (.env / 进程环境变量,必填基线)
       └─ 叠加 config.json.global        (全局默认覆盖)
            └─ 叠加 config.json.stages[stage]   (该环节覆盖)

持久化于 cwd 根的 config.json(gitignore,含明文密钥),原子写发布。"""
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from shanhai import store
from shanhai.config import Settings

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
    music_base_url: str | None = None
    music_api_key: str | None = None
    music_model: str | None = None


class AppConfig(BaseModel):
    """config.json 的顶层结构:全局默认 + 按环节覆盖。
    populate_by_name=True 让 global_ 既可用别名 global 也可用字段名填充。"""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    global_: ConfigOverride = Field(default_factory=ConfigOverride, alias="global")
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


def resolve_settings(
    stage: str | None = None,
    cfg: AppConfig | None = None,
    base: Settings | None = None,
) -> Settings:
    """把 global + stages[stage] 覆盖叠加到 base(默认 Settings())之上,返回该环节生效的 Settings。
    用 exclude_none(None=继承,避免显式 null 击穿必填字段);model_copy 不重校验 Literal
    (provider 合法性由写入/读取层的 ConfigOverride 校验保证)。base 可注入,便于单测隔离真实 .env。"""
    base = base or Settings()
    cfg = cfg or load_overrides()
    updates = cfg.global_.model_dump(exclude_none=True)
    if stage is not None and stage in cfg.stages:
        updates.update(cfg.stages[stage].model_dump(exclude_none=True))
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

def merge_override(incoming: ConfigOverride, existing: ConfigOverride) -> ConfigOverride:
    """按 PUT 的"部分更新"语义把 incoming 合并进 existing:
    - 只有 incoming 里**实际发来**的字段(model_fields_set,经 exclude_unset 提取)才参与合并,
      未发来的字段一律保留 existing 值——故前端不渲染的字段(如共享 base_url/api_key)不会被静默抹掉。
    - 密钥字段:值=SENTINEL 或 MASK→保持已存值(掩码回填不会被当成真密钥);值=""→清除(继承);其它→更新。
    - 非密钥字段:值=""或显式 null→清除(继承);其它→更新。"""
    merged = existing.model_dump()
    for field, val in incoming.model_dump(exclude_unset=True).items():
        if field in SECRET_FIELDS:
            if val in (SENTINEL, MASK):
                continue                       # 保持已存值不变
            merged[field] = None if val == "" else val
        else:
            merged[field] = None if val == "" else val
    return ConfigOverride(**merged)


def _is_empty_override(ov: ConfigOverride) -> bool:
    return all(v is None for v in ov.model_dump().values())


def apply_put(existing: AppConfig, incoming: AppConfig) -> AppConfig:
    """把一次 PUT(incoming)按部分更新语义合并进 existing:
    - global 逐字段合并;
    - stages:未在 incoming 出现的环节保留(裸 API 部分 PUT 不会误删其它环节),出现的环节逐字段合并;
      合并后全空的环节条目删除(用户清空某环节全部字段即可删除该环节覆盖)。"""
    merged_stages = dict(existing.stages)
    for st, ov in incoming.stages.items():
        merged = merge_override(ov, existing.stages.get(st, ConfigOverride()))
        if _is_empty_override(merged):
            merged_stages.pop(st, None)
        else:
            merged_stages[st] = merged
    return AppConfig(global_=merge_override(incoming.global_, existing.global_), stages=merged_stages)


def override_view(ov: ConfigOverride) -> dict:
    """把一层覆盖脱敏为 GET 视图:密钥字段 已设→MASK / 未设→None;非密钥字段 原值或 None。"""
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


def config_view(readonly: bool) -> dict:
    """GET /api/config 的完整响应体(也用作 PUT 成功后的回显)。所有密钥字段脱敏。"""
    cfg = load_overrides()
    return {
        "readonly": readonly,
        "stage_clients": {st: list(clients) for st, clients in STAGE_CLIENTS.items()},
        "defaults": defaults_view(),
        "global": override_view(cfg.global_),
        "stages": {st: override_view(ov) for st, ov in cfg.stages.items()},
    }
