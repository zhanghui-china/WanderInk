# tests/test_runtime_config.py
import os

import pytest

from shanhai import runtime_config
from shanhai.cli import _clients
from shanhai.config import Settings
from shanhai.schema import Project
from pydantic import ValidationError

from shanhai.providers._http import is_local_endpoint
from shanhai.runtime_config import (MASK, SECRET_FIELDS, SENTINEL, STAGE_CLIENTS,
                                     AppConfig, ConfigOverride, UserOverride, apply_put,
                                     defaults_view, load_overrides,
                                     merge_override, override_view, use_master_skill,
                                     resolve_settings, save_overrides,
                                     update_overrides)


@pytest.fixture(autouse=True)
def _clean_shanhai_env(monkeypatch):
    """隔离测试环境:清掉所有 SHANHAI_ 前缀的进程环境变量(同 test_config.py)。"""
    for k in list(os.environ):
        if k.startswith("SHANHAI_"):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _tmp_config_path(tmp_path, monkeypatch):
    """把配置路径指到 tmp_path,隔离测试对真实 config.json 的读写。
    _config_path() 延迟读 SHANHAI_CONFIG_PATH,故设环境变量即可(需在上面 _clean_shanhai_env 之后生效)。"""
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(tmp_path / "config.json"))


def _base() -> Settings:
    return Settings(_env_file=None, base_url="https://p.example.com/v1", api_key="sk-1")


# ---------- _config_path 延迟求值 ----------

def test_config_path_lazy_reflects_env(tmp_path, monkeypatch):
    """_config_path() 每次读 SHANHAI_CONFIG_PATH:import 后才设的环境变量(如 load_env 注入 .env)也生效,
    不再被 import 期冻结的常量静默忽略。"""
    target = tmp_path / "custom" / "cfg.json"
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(target))
    assert runtime_config._config_path() == target


def test_load_overrides_uses_lazy_config_path(tmp_path, monkeypatch):
    """load_overrides 读的是运行时 _config_path() 指向的文件,写在该路径的覆盖能被读回。"""
    target = tmp_path / "cfg.json"
    monkeypatch.setenv("SHANHAI_CONFIG_PATH", str(target))
    save_overrides(AppConfig(global_=ConfigOverride(llm_model="m-lazy")))
    assert target.exists()                               # 写到了 env 指定路径
    assert load_overrides().global_.llm_model == "m-lazy"


# ---------- resolve_settings 合并优先级 ----------

def test_resolve_only_base_when_no_overrides():
    base = _base()
    s = resolve_settings("s0", AppConfig(), base=base)
    assert s.llm_model == base.llm_model
    assert s.base_url == base.base_url


def test_resolve_global_overrides_base():
    cfg = AppConfig(global_=ConfigOverride(llm_model="global-model"))
    s = resolve_settings("s0", cfg, base=_base())
    assert s.llm_model == "global-model"


def test_resolve_stage_overrides_global_and_unset_fields_inherit():
    cfg = AppConfig(
        global_=ConfigOverride(llm_model="global-model", image_model="global-image"),
        stages={"s3": ConfigOverride(llm_model="stage-model")},
    )
    s = resolve_settings("s3", cfg, base=_base())
    assert s.llm_model == "stage-model"      # stage 压 global
    assert s.image_model == "global-image"   # s3 未设 image_model,继承 global


def test_resolve_stage_without_override_falls_back_to_global():
    cfg = AppConfig(global_=ConfigOverride(llm_model="global-model"))
    s = resolve_settings("s1", cfg, base=_base())  # s1 不在 cfg.stages 中
    assert s.llm_model == "global-model"


# ---------- users 层(按作品归属者覆盖 LLM)----------

def test_resolve_user_overrides_global():
    cfg = AppConfig(global_=ConfigOverride(llm_model="global-model"),
                    users={"zhanghui": UserOverride(llm_model="local-model")})
    assert resolve_settings("s1", cfg, base=_base(), owner="zhanghui").llm_model == "local-model"


def test_resolve_stage_overrides_user():
    """四层优先级的关键一条:环节层压用户层。管理员为某环节钉死的配置(如"S4 必须走本机
    shim")不该被个人偏好盖掉——这也是 image 单并发保护的最后一道人为闸门。"""
    cfg = AppConfig(
        global_=ConfigOverride(llm_model="global-model"),
        users={"zhanghui": UserOverride(llm_model="user-model")},
        stages={"s1": ConfigOverride(llm_model="stage-model")},
    )
    assert resolve_settings("s1", cfg, base=_base(), owner="zhanghui").llm_model == "stage-model"
    # 没有环节覆盖的环节仍走用户层
    assert resolve_settings("s2", cfg, base=_base(), owner="zhanghui").llm_model == "user-model"


def test_provider_does_not_bleed_onto_another_endpoint():
    """2026-08-08 线上故障的回归,形状照抄当时的 config.json。

    stages.s1 指向 hermes-agent(纯 OpenAI 兼容)却没写 llm_provider,用户在自己那层选了
    ollama。逐字段合并会让 provider 穿透上来,于是拿 Ollama 原生协议去打 hermes:
    POST http://127.0.0.1:8642/api/chat → 404,而两处配置各看各的、界面上看不出冲突。
    换了端点就不该继承下层的协议——这条是 _apply_layer 的执法点。"""
    cfg = AppConfig(
        users={"huntun": UserOverride(llm_base_url="http://127.0.0.1:11434/v1",
                                      llm_provider="ollama")},
        stages={"s1": ConfigOverride(llm_base_url="http://127.0.0.1:8642/v1",
                                     llm_model="hermes-agent")},
    )
    s1 = resolve_settings("s1", cfg, base=_base(), owner="huntun")
    assert s1.llm_provider == "openai"                        # 不是 ollama
    assert s1.llm_endpoint[0] == "http://127.0.0.1:8642/v1"   # 端点仍是环节层的
    # 没有环节覆盖的环节照常用本人的 ollama,不能误伤
    s3 = resolve_settings("s3", cfg, base=_base(), owner="huntun")
    assert (s3.llm_provider, s3.llm_endpoint[0]) == ("ollama", "http://127.0.0.1:11434/v1")


def test_provider_kept_when_layer_does_not_change_endpoint():
    """只改模型名、没换端点的覆盖不该动 provider——协议属于端点,端点没变就别管。"""
    cfg = AppConfig(
        users={"huntun": UserOverride(llm_base_url="http://127.0.0.1:11434/v1",
                                      llm_provider="ollama")},
        stages={"s1": ConfigOverride(llm_model="qwen3.5-tiny")},
    )
    s = resolve_settings("s1", cfg, base=_base(), owner="huntun")
    assert (s.llm_provider, s.llm_model) == ("ollama", "qwen3.5-tiny")


def test_explicit_provider_always_wins():
    """换端点**并且**写明了协议时,写明的那个说了算(否则本机 Ollama 就没法按环节配了)。"""
    cfg = AppConfig(
        stages={"s1": ConfigOverride(llm_base_url="http://127.0.0.1:11434/v1",
                                     llm_provider="ollama")},
    )
    assert resolve_settings("s1", cfg, base=_base()).llm_provider == "ollama"


def test_provider_does_not_bleed_from_env_layer():
    """.env 里的 provider 同样穿不过来。

    锁的是 _apply_layer 里"显式写 openai"而不是"只 pop"的那个决定:只 pop 会回落到 base
    (即 .env 的 SHANHAI_LLM_PROVIDER),那仍旧是别的端点的协议,同一个 bug 换一层再犯。"""
    base = Settings(_env_file=None, base_url="https://p.example.com/v1", api_key="sk-1",
                    llm_provider="ollama")
    cfg = AppConfig(global_=ConfigOverride(llm_base_url="http://127.0.0.1:8642/v1"))
    assert resolve_settings("s1", cfg, base=base).llm_provider == "openai"
    # 全局层没换端点时,.env 的选择照常生效
    assert resolve_settings("s1", AppConfig(), base=base).llm_provider == "ollama"


def test_resolve_ignores_user_layer_without_owner():
    """owner=""(历史无主项目、CLI 无登录态)跳过 users 层回落到 global——这条同时是
    "本功能不影响存量项目"的回归证据。"""
    cfg = AppConfig(global_=ConfigOverride(llm_model="global-model"),
                    users={"zhanghui": UserOverride(llm_model="user-model")})
    assert resolve_settings("s1", cfg, base=_base()).llm_model == "global-model"
    assert resolve_settings("s1", cfg, base=_base(), owner="").llm_model == "global-model"


def test_resolve_unknown_owner_falls_back_to_global():
    cfg = AppConfig(global_=ConfigOverride(llm_model="global-model"),
                    users={"zhanghui": UserOverride(llm_model="user-model")})
    assert resolve_settings("s1", cfg, base=_base(), owner="someone-else").llm_model == "global-model"


def test_user_override_rejects_non_llm_fields():
    """**image 单并发保护的执法点。** image_base_url 一旦能按人配,改成非 loopback 会让
    _http.local_backend_guard(锁失效)与 image_concurrency(扇出 1→2)同时静默失效。
    用 extra="forbid" 让它成为结构约束,而不是一条要靠人记住的纪律。"""
    for field in ("image_base_url", "image_model", "tts_base_url", "music_base_url", "base_url"):
        with pytest.raises(ValidationError):
            UserOverride(**{field: "http://192.168.1.9:8099/v1"})


def test_user_layer_never_touches_image_settings():
    """配了用户层之后,图像侧必须逐字不变——单并发的两处判定都只看 image 端点。"""
    cfg = AppConfig(global_=ConfigOverride(image_base_url="http://127.0.0.1:8091/v1"),
                    users={"zhanghui": UserOverride(llm_base_url="http://127.0.0.1:11434/v1")})
    s = resolve_settings("s4", cfg, base=_base(), owner="zhanghui")
    assert s.image_endpoint[0] == "http://127.0.0.1:8091/v1"
    assert runtime_config.image_concurrency(s) == 1          # 仍串行
    assert is_local_endpoint(s.image_endpoint[0])            # 仍受全局单并发锁保护


# ---------- llm_endpoint 回退 ----------

def test_llm_base_url_only_affects_llm_endpoint():
    s = Settings(_env_file=None, base_url="https://p.example.com/v1", api_key="sk-1",
                 llm_base_url="https://llm.example.com/v1")
    assert s.llm_endpoint == ("https://llm.example.com/v1", "sk-1")
    assert s.image_endpoint == ("https://p.example.com/v1", "sk-1")   # 不受影响
    assert s.tts_endpoint == ("https://p.example.com/v1", "sk-1")     # 不受影响


def test_llm_endpoint_falls_back_to_base_url_when_unset():
    s = _base()
    assert s.llm_endpoint == ("https://p.example.com/v1", "sk-1")


# ---------- 原子读写 round-trip ----------

def test_save_load_round_trip():
    cfg = AppConfig(
        global_=ConfigOverride(llm_model="m", api_key="sk-x"),
        stages={"s5": ConfigOverride(tts_base_url="https://tts.local/v1")},
    )
    save_overrides(cfg)
    loaded = load_overrides()
    assert loaded.global_.llm_model == "m"
    assert loaded.global_.api_key == "sk-x"
    assert loaded.stages["s5"].tts_base_url == "https://tts.local/v1"


def test_save_leaves_no_leftover_tmp_files(tmp_path):
    save_overrides(AppConfig(global_=ConfigOverride(llm_model="m")))
    assert list(tmp_path.iterdir()) == [tmp_path / "config.json"]   # 临时文件已被 os.replace 清掉


def test_load_missing_file_returns_empty_config():
    assert load_overrides() == AppConfig()


def test_load_corrupted_json_returns_empty_config_without_raising(tmp_path):
    (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")
    assert load_overrides() == AppConfig()   # 不抛异常,回退空配置


# ---------- update_overrides:读-改-写在写锁内原子完成(防并发 PUT 丢更新) ----------

def test_update_overrides_reads_current_and_persists():
    save_overrides(AppConfig(global_=ConfigOverride(llm_model="old")))
    result = update_overrides(
        lambda cur: AppConfig(global_=ConfigOverride(llm_model=cur.global_.llm_model + "-new")))
    assert result.global_.llm_model == "old-new"          # mutate 收到当前落盘态
    assert load_overrides().global_.llm_model == "old-new"  # 且已持久化


def test_update_overrides_mutate_error_leaves_file_untouched():
    save_overrides(AppConfig(global_=ConfigOverride(llm_model="keep")))
    with pytest.raises(ValueError):
        update_overrides(lambda _cur: (_ for _ in ()).throw(ValueError("boom")))
    assert load_overrides().global_.llm_model == "keep"   # 异常时不写,旧文件完好


# ---------- merge_override / apply_put:PUT 部分更新语义 ----------

def test_merge_override_unsent_field_preserved():
    existing = ConfigOverride(base_url="https://root", api_key="sk-root", llm_model="m1")
    merged = merge_override(ConfigOverride(llm_model="m2"), existing)  # 只发 llm_model
    assert merged.llm_model == "m2"
    assert merged.base_url == "https://root"   # 未发送 → 保留(不被抹掉)
    assert merged.api_key == "sk-root"


def test_merge_override_secret_sentinel_mask_empty():
    existing = ConfigOverride(llm_api_key="sk-old")
    assert merge_override(ConfigOverride(llm_api_key=SENTINEL), existing).llm_api_key == "sk-old"
    assert merge_override(ConfigOverride(llm_api_key=MASK), existing).llm_api_key == "sk-old"  # 掩码回填不写真值
    assert merge_override(ConfigOverride(llm_api_key=""), existing).llm_api_key is None         # 清除
    assert merge_override(ConfigOverride(llm_api_key="sk-new"), existing).llm_api_key == "sk-new"


def test_merge_override_nonsecret_empty_clears():
    existing = ConfigOverride(llm_model="m1")
    assert merge_override(ConfigOverride(llm_model=""), existing).llm_model is None  # 非密钥空串=清除继承


def test_apply_put_preserves_and_prunes_stages():
    existing = AppConfig(stages={
        "s2": ConfigOverride(llm_model="a"),
        "s5": ConfigOverride(tts_model="b")})
    incoming = AppConfig(stages={"s2": ConfigOverride(llm_model="")})  # 清空 s2 的唯一字段
    result = apply_put(existing, incoming)
    assert "s2" not in result.stages                 # 清空 → 删除该环节
    assert result.stages["s5"].tts_model == "b"      # 未在 incoming 出现 → 保留


# ---------- 集成:环节确实用了覆盖端点 ----------

def test_clients_use_resolved_tts_base_url_override():
    cfg = AppConfig(stages={"s5": ConfigOverride(tts_base_url="https://tts.local/v1")})
    s = resolve_settings("s5", cfg, base=_base())
    _, _, tts, _ = _clients(s)
    assert str(tts._client.base_url).rstrip("/") == "https://tts.local/v1"


# ---------- music provider 接入(STAGE_CLIENTS / SECRET_FIELDS / 合并 / 脱敏) ----------

def test_stage_clients_s5_includes_music():
    assert STAGE_CLIENTS["s5"] == ("tts", "music")


def test_music_api_key_is_secret_field():
    assert "music_api_key" in SECRET_FIELDS


def test_resolve_stage_music_override():
    cfg = AppConfig(stages={"s5": ConfigOverride(music_model="custom-model")})
    s = resolve_settings("s5", cfg, base=_base())
    assert s.music_model == "custom-model"


def test_music_base_url_only_affects_music_endpoint():
    s = Settings(_env_file=None, base_url="https://p.example.com/v1", api_key="sk-1",
                 music_base_url="https://music.example.com/v1")
    assert s.music_endpoint == ("https://music.example.com/v1", "sk-1")
    assert s.tts_endpoint == ("https://p.example.com/v1", "sk-1")     # 不受影响


def test_defaults_view_includes_music_fields():
    d = defaults_view()
    assert "music_model" in d and "music_base_url" in d and "music_api_key" in d
    assert isinstance(d["music_api_key"], bool)   # 密钥字段=bool,不回显明文


def test_override_view_masks_music_api_key():
    ov = ConfigOverride(music_api_key="sk-music")
    assert override_view(ov)["music_api_key"] == MASK


# ---------- 大师 skill 闸门 + 引擎记录 ----------
# 从 tests/test_api.py 迁来(闸门随 _use_master_skill 移到本模块),并补上记录断言:
# 记录的意义全在"退化那一路也要留痕"——原先退化只 print 到服务端 stdout,project.json
# 里不存任何模型/skill 信息,事后无法回答"这部作品到底走没走 skill"。

def _p_with_skill(on: bool) -> Project:
    p = Project(project_id="g1", scenic_spot="花果山")
    p.params.master_skill = on
    return p


HERMES = dict(base_url="http://127.0.0.1:8642/v1", api_key="x", llm_model="hermes-agent")
OTHER = dict(base_url="https://api.stepfun.com/v1", api_key="x", llm_model="step-3.7-flash")


def test_use_master_skill_on_hermes_returns_true_and_records_skill_name():
    p = _p_with_skill(True)
    s = Settings(_env_file=None, **HERMES)
    assert use_master_skill(p, s, "s1") is True
    assert use_master_skill(p, s, "s2") is True
    assert p.status["s1_engine"] == "hermes-agent · 编剧大师"
    assert p.status["s2_engine"] == "hermes-agent · 导演大师"


def test_use_master_skill_off_records_plain_engine():
    p = _p_with_skill(False)
    s = Settings(_env_file=None, **HERMES)
    assert use_master_skill(p, s, "s1") is False   # 开关关 → 恒 False,即便后端是 hermes
    assert p.status["s1_engine"] == "hermes-agent · 普通"


def test_use_master_skill_on_non_hermes_degrades_and_records_reason():
    # 开关开了但该环节后端不是 hermes-agent → 退化普通生成(不把斜杠命令发给别的模型),
    # 且记录里必须写明退化原因,否则这次退化在事后完全不可见。
    p = _p_with_skill(True)
    s = Settings(_env_file=None, **OTHER)
    assert use_master_skill(p, s, "s2") is False
    rec = p.status["s2_engine"]
    assert rec.startswith("step-3.7-flash · 普通")
    assert "已忽略大师开关" in rec and "hermes-agent" in rec


# ---------- 附加语种轨的默认音色 ----------

def _voice_settings(**kw) -> Settings:
    return Settings(_env_file=None, base_url="https://p.example.com/v1", api_key="sk-1",
                    tts_voice="cn-default", tts_voices="cn-default, cn-female",
                    tts_voice_en="en-default", **kw)


def test_track_voice_inherits_custom_clone_voice():
    """自定义音色是用户自己的声音,同一个作品的英文视频也该是他 —— 用户原话
    "音色在每个项目中都是影响到这个项目的所有配音的"。

    判据是"不在配置层的预置音色列表里",而不是 voice 以 clone: 开头:那个前缀是上游 TTS
    返回的约定,后端从未强制过;预置列表是配置的一部分,对新老作品一律有效
    (今天新建的音色样本索引只覆盖此后上传的,线上 6 部老作品的句柄不在里面)。"""
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.voice = "clone:shanhai_voice_abc.wav"
    assert runtime_config.default_track_voice(p, _voice_settings()) == "clone:shanhai_voice_abc.wav"


def test_track_voice_does_not_inherit_preset_chinese_voice():
    """预置中文音色**不能**继承到英文轨:配置里专门有 tts_voice_en 就是因为中文 speaker
    念英文效果差。只有克隆音色才有"这是本人的声音、跨语种也该是他"这个诉求。"""
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.voice = "cn-female"          # 在预置列表里
    assert runtime_config.default_track_voice(p, _voice_settings()) == "en-default"


def test_track_voice_falls_back_when_project_has_no_voice():
    p = Project(project_id="x", scenic_spot="雷峰塔")
    assert runtime_config.default_track_voice(p, _voice_settings()) == "en-default"


def test_track_voice_falls_back_to_main_voice_when_no_english_preset():
    p = Project(project_id="x", scenic_spot="雷峰塔")
    s = Settings(_env_file=None, base_url="https://p.example.com/v1", api_key="sk-1",
                 tts_voice="cn-default", tts_voices="cn-default")
    assert runtime_config.default_track_voice(p, s) == "cn-default"   # tts_voice_en 为空
