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


@respx.mock
def test_s0_filters_sensitive_candidates():
    cands = {"candidates": [
        {"title": "白蛇传", "summary": "白娘子与许仙…", "source_type": "民间传说",
         "sources": ["《警世通言》"]},
        {"title": "周恩来与梅园新村和平谈判", "summary": "1946年周恩来率团驻南京梅园新村…",
         "source_type": "正史", "sources": ["《周恩来传》"]},
    ]}
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cands, ensure_ascii=False)}}]}))
    p = s0_legend.run(Project(project_id="x", scenic_spot="梅园新村"), LLMClient(BASE, "sk", "m"))
    titles = [c.title for c in p.legend_candidates]
    assert "白蛇传" in titles
    assert "周恩来与梅园新村和平谈判" not in titles   # 命中敏感人物,被过滤


def test_s0_from_text_rejects_sensitive_story():
    p = Project(project_id="x", scenic_spot="梅园新村")
    try:
        s0_legend.from_text(p, LLMClient(BASE, "sk", "m"),
                            "1946年,周恩来率中共代表团在梅园新村与国民党谈判……")
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "敏感" in str(e)
    assert p.legend is None      # 阻断在调 LLM 之前,project 未被写入
