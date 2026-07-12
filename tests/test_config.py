# tests/test_config.py
import os

import pytest
from pydantic import ValidationError

from shanhai.config import Settings, load_env


@pytest.fixture(autouse=True)
def _clean_shanhai_env(monkeypatch):
    """隔离测试环境:清掉所有 SHANHAI_ 前缀的进程环境变量。
    collection 期导入 shanhai.api 会触发其模块级 load_env(),把真实项目 .env 载入
    os.environ;若不清理,会污染下面依赖 Settings(_env_file=None) + monkeypatch 精确控制
    输入的默认值断言。"""
    for k in list(os.environ):
        if k.startswith("SHANHAI_"):
            monkeypatch.delenv(k, raising=False)

def test_defaults_and_fallback(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    s = Settings(_env_file=None)
    assert s.image_api_mode == "chat_api"
    assert s.image_endpoint == ("https://p.example.com/v1", "sk-1")

def test_modality_override(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    monkeypatch.setenv("SHANHAI_IMAGE_BASE_URL", "https://img.example.com/v1")
    s = Settings(_env_file=None)
    assert s.image_endpoint == ("https://img.example.com/v1", "sk-1")
    assert s.tts_endpoint == ("https://p.example.com/v1", "sk-1")

def test_music_endpoint_falls_back_to_base_url(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    s = Settings(_env_file=None)
    assert s.music_endpoint == ("https://p.example.com/v1", "sk-1")

def test_music_endpoint_override(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    monkeypatch.setenv("SHANHAI_MUSIC_BASE_URL", "https://music.example.com/v1")
    s = Settings(_env_file=None)
    assert s.music_endpoint == ("https://music.example.com/v1", "sk-1")
    assert s.image_endpoint == ("https://p.example.com/v1", "sk-1")   # 不受影响
    assert s.tts_endpoint == ("https://p.example.com/v1", "sk-1")     # 不受影响

def test_strict_consistency_defaults_false(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    assert Settings(_env_file=None).strict_consistency is False

def test_tts_voices_list_falls_back_to_tts_voice(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    s = Settings(_env_file=None)
    assert s.tts_voices_list == [s.tts_voice]

def test_tts_voices_list_parses_comma_separated(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    monkeypatch.setenv("SHANHAI_TTS_VOICES", "alloy, shimmer,echo")
    s = Settings(_env_file=None)
    assert s.tts_voices_list == ["alloy", "shimmer", "echo"]

def test_load_env_sets_key_not_in_process_env(tmp_path):
    # .env 里的键在进程环境中不存在时,应被载入 os.environ(H7/P6:否则只写 .env 不生效)
    key = "SHANHAI_TEST_NEW_KEY"
    os.environ.pop(key, None)
    envfile = tmp_path / ".env"
    envfile.write_text(f"{key}=from_dotenv\n")
    try:
        load_env(envfile)
        assert os.environ[key] == "from_dotenv"
    finally:
        os.environ.pop(key, None)

def test_llm_provider_rejects_typo(monkeypatch):
    # FP9:非法 llm_provider(如拼写错误)须在构造 Settings 时即报错,而非运行时误落到 OpenAI 客户端。
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    monkeypatch.setenv("SHANHAI_LLM_PROVIDER", "olama")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

def test_llm_provider_accepts_known_values(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    for value in ("openai", "ollama"):
        monkeypatch.setenv("SHANHAI_LLM_PROVIDER", value)
        assert Settings(_env_file=None).llm_provider == value

def test_load_env_does_not_override_process_env(monkeypatch, tmp_path):
    # 进程环境变量(如 systemd EnvironmentFile 注入)优先于 .env,不能被覆盖
    key = "SHANHAI_TEST_EXISTING_KEY"
    monkeypatch.setenv(key, "from_process")
    envfile = tmp_path / ".env"
    envfile.write_text(f"{key}=from_dotenv\n")
    load_env(envfile)
    assert os.environ[key] == "from_process"
