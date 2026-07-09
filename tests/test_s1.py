import json
import httpx, respx, pytest
from shanhai.providers.llm import LLMClient
from shanhai.schema import Legend, Project
from shanhai.steps import s1_script

BASE = "https://p.example.com/v1"

SCRIPT = {"title": "白蛇传", "theme": "人妖之恋", "acts": [{"scenes": [
    {"description": "断桥初遇", "characters": ["白素贞", "许仙"],
     "narration": "西湖烟雨中……", "dialogues": [{"character": "白素贞", "line": "公子留步。"}]}]}],
    "characters": [{"name": "白素贞", "role": "蛇仙", "personality": "温婉坚韧",
                    "appearance": "白衣女子"}]}

def _project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["x"])
    return p

@respx.mock
def test_s1_fills_script():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(SCRIPT, ensure_ascii=False)}}]}))
    p = s1_script.run(_project(), LLMClient(BASE, "sk", "m"))
    assert p.script.characters[0].name == "白素贞"
    assert p.status["s1"] == "done"

def test_s1_requires_legend():
    with pytest.raises(ValueError):
        s1_script.run(Project(project_id="x", scenic_spot="雷峰塔"), LLMClient(BASE, "sk", "m"))

def test_s1_system_contains_narrative_framework():
    """Assert that SYSTEM prompt contains key narrative framework keywords."""
    system = s1_script.SYSTEM
    # Check for cold open hook keywords
    assert "冷开场" in system or "钩子" in system, "Missing cold open hook instruction"
    # Check for four-part structure (起承转合)
    assert "起承转合" in system, "Missing 起承转合 structure instruction"
    # Check for climax/tension
    assert "高潮" in system, "Missing climax instruction"
    # Check for emotional beat
    assert "情感" in system, "Missing emotional beat instruction"
