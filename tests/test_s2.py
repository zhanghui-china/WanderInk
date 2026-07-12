import json
import httpx, pytest, respx
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

@respx.mock
def test_s2_filters_narrator_from_cell_characters():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥",
         "characters": ["白素贞", "旁白"], "caption": "初遇。", "emotion": "宁静"}]}
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert p.storyboard[0].characters == ["白素贞"]   # 旁白从出场角色剔除


@respx.mock
def test_s2_raises_on_empty_storyboard():
    # M1:LLM 产出零页时须报 error,不能让空书当作 done 溜过管线
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps({"cells": []}, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    with pytest.raises(ValueError, match="分镜为空"):
        s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert "s2" not in p.status                # 未标记 done


@respx.mock
def test_s2_renumbers_duplicate_indices():
    # PERF1 数据竞争根因:LLM 给重复/跳号 index 也须重排为唯一 1..n(下游 S4/S5/S6 与并行
    # 落盘以 page_{index} 为唯一文件名,重复 index 会让并行页互相踩踏临时/产物文件)。
    cells = {"cells": [
        {"index": 5, "scene_ref": "1-1", "visual_desc": "a", "characters": [],
         "caption": "第一页。", "emotion": "宁静"},
        {"index": 5, "scene_ref": "1-2", "visual_desc": "b", "characters": [],
         "caption": "第二页。", "emotion": "宁静"},
        {"index": 2, "scene_ref": "1-3", "visual_desc": "c", "characters": [],
         "caption": "第三页。", "emotion": "宁静"}]}
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert [c.index for c in p.storyboard] == [1, 2, 3]   # 重复/跳号 → 唯一连续 1..n


def test_s2_system_contains_page_end_suspense_hint():
    """Assert that SYSTEM prompt contains hint about page-end suspense."""
    system = s2_storyboard.SYSTEM
    # Check for page-end suspense hint keywords
    assert "悬念" in system or "期待" in system or "页尾" in system, "Missing page-end suspense hint"
    # Ensure the hard constraint about story continuity is preserved
    assert "连起来" in system or "独立讲通" in system, "Missing hard constraint about story continuity"
