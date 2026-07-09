# src/shanhai/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SHANHAI_", extra="ignore")

    base_url: str
    api_key: str
    llm_model: str = "claude-sonnet-5"
    image_model: str = "gemini-2.5-flash-image"
    image_api_mode: str = "chat_api"
    image_size: str = "1536x1024"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    image_base_url: str | None = None
    image_api_key: str | None = None
    tts_base_url: str | None = None
    tts_api_key: str | None = None

    @property
    def image_endpoint(self) -> tuple[str, str]:
        return (self.image_base_url or self.base_url, self.image_api_key or self.api_key)

    @property
    def tts_endpoint(self) -> tuple[str, str]:
        return (self.tts_base_url or self.base_url, self.tts_api_key or self.api_key)
