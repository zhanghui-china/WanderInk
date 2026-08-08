import json
import httpx, respx, pytest
from shanhai.providers.llm import LLMClient
from shanhai.schema import Legend, Project, Script
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


def test_s1_use_skill_prepends_slash_and_caps_retries():
    # use_skill=True:system 前置斜杠命令触发编剧大师 skill,retries 降到 1 封成本。
    # ⚠️ 这条**测不出真正的失败模式**:斜杠命令写错时 hermes 不报错,只是当普通文本吞掉
    # (2026-08-08 实测,名字从 screenwriter-master 被对方改成了 screenwriting-master,
    # 编剧大师因此长期空转)。单测只能锁住"前缀确实被拼上去了";名字是否还有效,只有对着
    # 线上 hermes 的 GET /v1/skills 比对才知道 —— 见 scripts/check-hermes-skills.py。
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.structured.return_value = Script.model_validate(SCRIPT)
    s1_script.run(_project(), llm, use_skill=True)
    sys_arg = llm.structured.call_args.args[0]
    assert sys_arg.startswith("/screenwriting-master")
    assert "请勿反问" in sys_arg
    assert llm.structured.call_args.kwargs.get("retries") == 1


def test_s1_without_skill_no_slash_default_retries():
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.structured.return_value = Script.model_validate(SCRIPT)
    s1_script.run(_project(), llm)                 # use_skill 默认 False
    sys_arg = llm.structured.call_args.args[0]
    assert "/screenwriting-master" not in sys_arg
    assert "retries" not in llm.structured.call_args.kwargs   # 走默认 retries=2

def test_is_narrator():
    assert s1_script.is_narrator("旁白", "叙事者")
    assert s1_script.is_narrator("解说")
    assert s1_script.is_narrator("Narrator")
    assert not s1_script.is_narrator("白素贞", "蛇仙")
    assert not s1_script.is_narrator("许仙", "书生")

@respx.mock
def test_s1_filters_narrator_from_characters():
    script = json.loads(json.dumps(SCRIPT))
    script["characters"].append({"name": "旁白", "role": "叙事者",
                                 "personality": "温和", "appearance": "无固定形象"})
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(script, ensure_ascii=False)}}]}))
    p = s1_script.run(_project(), LLMClient(BASE, "sk", "m"))
    names = [c.name for c in p.script.characters]
    assert "旁白" not in names            # 旁白被剔除,不占三视图名额
    assert "白素贞" in names

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

def test_s1_system_requires_importance_ordering():
    """characters 需按重要度降序,保证前 4 个主角拿到三视图参考。"""
    system = s1_script.SYSTEM
    assert "重要度" in system and "降序" in system


@respx.mock
def test_s1_blocks_sensitive_generated_script():
    script = json.loads(json.dumps(SCRIPT))
    script["theme"] = "周恩来在梅园新村的坚守"     # LLM 生成内容里混入敏感人物,即便传说本身干净
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(script, ensure_ascii=False)}}]}))
    with pytest.raises(ValueError, match="敏感"):
        s1_script.run(_project(), LLMClient(BASE, "sk", "m"))
