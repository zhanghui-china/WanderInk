# tests/test_config.py
from shanhai.config import Settings

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
