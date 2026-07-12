# src/shanhai/config.py
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


def load_env(path: str | Path = ".env") -> None:
    """把 path 指向的 .env 加载进 os.environ,不覆盖已存在的进程环境变量
    (systemd EnvironmentFile 等注入的进程环境优先于 .env)。
    api.py 在模块级用 os.getenv 读取 HOST/PORT/CORS_ORIGINS/READONLY 之前需调用一次,
    否则单靠 .env 写的配置读不到——这些读取发生在 import 期,早于任何 Settings() 构造。"""
    load_dotenv(path, override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SHANHAI_", extra="ignore")

    base_url: str
    api_key: str
    llm_model: str = "claude-sonnet-5"
    llm_provider: str = "openai"  # "openai" | "ollama"(原生 API,think:false + schema 约束,快 10×)
    llm_timeout: float = 300  # 秒;本地思考型模型建议调大(SHANHAI_LLM_TIMEOUT)
    image_model: str = "gemini-2.5-flash-image"
    image_api_mode: str = "chat_api"
    image_size: str = "1536x1024"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    tts_voices: str = ""  # 逗号分隔的可选音色列表;空则回退 [tts_voice]
    image_base_url: str | None = None
    image_api_key: str | None = None
    tts_base_url: str | None = None
    tts_api_key: str | None = None
    strict_consistency: bool = False  # True 时 S4 无三视图直接失败(堵 M0 绕过);默认告警继续
    # readonly 不在此声明:公网只读靠 api.py 模块级 os.getenv(SHANHAI_READONLY)读取一次
    # (见 api.py _READONLY),避免与本类形成双真相源——Settings 在每次请求里都会重新构造一份。

    @property
    def tts_voices_list(self) -> list[str]:
        voices = [v.strip() for v in self.tts_voices.split(",") if v.strip()]
        return voices or [self.tts_voice]

    @property
    def image_endpoint(self) -> tuple[str, str]:
        return (self.image_base_url or self.base_url, self.image_api_key or self.api_key)

    @property
    def tts_endpoint(self) -> tuple[str, str]:
        return (self.tts_base_url or self.base_url, self.tts_api_key or self.api_key)
