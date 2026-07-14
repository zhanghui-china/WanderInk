import respx, httpx, json
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project
from shanhai.steps import s0_legend

BASE = "https://p.example.com/v1"

CANDS = {"candidates": [{"title": "白蛇传", "summary": "白娘子与许仙…" ,
                          "source_type": "民间传说", "sources": ["《警世通言》"]}]}

@respx.mock
def test_s0_fills_candidates():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(CANDS, ensure_ascii=False)}}]}))
    p = s0_legend.run(Project(project_id="x", scenic_spot="雷峰塔"), LLMClient(BASE, "sk", "m"))
    assert p.legend_candidates[0].title == "白蛇传"
    assert p.status["s0"] == "done"

@respx.mock
def test_s0_empty_candidates_no_fabrication():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps({"candidates": []})}}]}))
    p = s0_legend.run(Project(project_id="x", scenic_spot="无名小地"), LLMClient(BASE, "sk", "m"))
    assert p.legend_candidates == []          # 无可靠传说返回空,不编造
    assert p.status["s0"] == "done"
