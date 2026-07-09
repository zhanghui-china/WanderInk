import json
import httpx, respx
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, Script
from shanhai.steps import s2_storyboard

BASE = "https://p.example.com/v1"

CELLS = {"cells": [
    {"index": i, "scene_ref": "1-1", "visual_desc": f"画面{i}", "characters": ["白素贞"],
     "caption": f"第{i}页的解说词。", "emotion": "宁静"} for i in range(1, 9)]}

@respx.mock
def test_s2_fills_storyboard():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(CELLS, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert len(p.storyboard) == 8
    assert p.storyboard[0].status == "draft"
    assert p.status["s2"] == "done"
