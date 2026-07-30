import json
import httpx, pytest, respx
from shanhai.providers.llm import LLMClient
from shanhai.schema import CharacterCard, Project, Script
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


def test_s2_system_keeps_suspense_but_bans_rhetorical_questions():
    """悬念提示保留,但必须同时带上句式禁令。

    原先这条只断言"含悬念提示",锁的是**每页都要悬念**那版设计;实测下来模型满足
    "每页都要悬念"最省力的写法就是反问句,一部 22 页的作品里 18 页 caption 以问号结尾。
    修法不是删掉悬念,而是限定频次(只在幕/场转折处)+ 明禁句式,所以这条断言也要跟着
    锁住"禁令还在"——只断言"含悬念"会让禁令被误删时依旧全绿。"""
    system = s2_storyboard.SYSTEM
    assert "悬念" in system, "Missing suspense hint"
    assert "问号" in system and "反问" in system, "Missing ban on rhetorical/question endings"
    # Ensure the hard constraint about story continuity is preserved
    assert "连起来" in system or "独立讲通" in system, "Missing hard constraint about story continuity"


def test_s2_system_bans_character_names_in_visual_desc():
    """角色中文名(如"小虎")原样进入 visual_desc,会一路流到文生图 prompt 被画成真老虎。
    源头上让模型用外貌/身份指代,S4 的强制替换只是兜底。"""
    system = s2_storyboard.SYSTEM
    assert "不要写角色名" in system, "Missing ban on character names in visual_desc"


@respx.mock
def test_s2_single_page_mode_omits_panel_rules_in_system_prompt():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": [],
         "caption": "初遇。", "emotion": "宁静"}]}
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    sent = json.loads(route.calls[0].request.content)
    assert "分格" not in sent["messages"][0]["content"]


@respx.mock
def test_s2_multi_panel_includes_panel_rules_in_system_prompt():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": [],
         "caption": "初遇。", "emotion": "宁静", "panels": []}]}
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.params.multi_panel = True
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    sent = json.loads(route.calls[0].request.content)
    system_msg = sent["messages"][0]["content"]
    assert "分格" in system_msg and "insert" in system_msg


def test_s2_use_skill_prepends_slash_and_caps_retries():
    # use_skill=True:system 前置 /director-master 触发导演大师 skill,retries 降到 1 封成本
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.structured.return_value = s2_storyboard._Cells.model_validate(CELLS)
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, llm, use_skill=True)
    sys_arg = llm.structured.call_args.args[0]
    assert sys_arg.startswith("/director-master")
    assert "请勿反问" in sys_arg
    assert llm.structured.call_args.kwargs.get("retries") == 1


def test_s2_without_skill_no_slash_default_retries():
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.structured.return_value = s2_storyboard._Cells.model_validate(CELLS)
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, llm)
    sys_arg = llm.structured.call_args.args[0]
    assert not sys_arg.startswith("/director-master")
    assert "retries" not in llm.structured.call_args.kwargs


@respx.mock
def test_s2_multi_panel_clamps_panels_to_hard_cap():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": ["白素贞"],
         "caption": "初遇。", "emotion": "宁静",
         "panels": [{"visual_desc": f"格{i}", "shot_type": "medium", "characters": ["白素贞"]}
                    for i in range(6)]}]}  # LLM 违规给了 6 格,应被裁到 4
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.params.multi_panel = True
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert len(p.storyboard[0].panels) == s2_storyboard.MAX_PANELS_PER_PAGE


@respx.mock
def test_s2_drops_panels_when_multi_panel_off():
    """未勾选分格时,模型自发填的 panels 必须被清掉。

    PANEL_RULES 开关只控制自然语言指令,而 llm.structured 无条件把含 panels 字段
    (带 shot_type 枚举和中文说明)的完整 JSON Schema 喂给模型——模型完全可能好心填上,
    填了就会让 S4 走分格路径,与用户预期相反。"""
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": ["白素贞"],
         "caption": "初遇。", "emotion": "宁静",
         "panels": [{"visual_desc": "格1", "shot_type": "wide", "characters": []},
                    {"visual_desc": "格2", "shot_type": "closeup", "characters": []}]}]}
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.params.multi_panel = False          # 用户没开分格
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert p.storyboard[0].panels == []


# ---------- 出场名单必须用角色表里的名字 ----------

def _cast_cells(names_per_page: list[list[str]]) -> dict:
    return {"cells": [
        {"index": i, "scene_ref": "1-1", "visual_desc": f"画面{i}", "characters": names,
         "caption": f"第{i}页的解说词。", "emotion": "宁静"}
        for i, names in enumerate(names_per_page, 1)]}


def _card(name: str) -> CharacterCard:
    return CharacterCard(name=name, role="r", personality="p", appearance="a")


def _run_with(cells: dict, cast: list[str]) -> Project:
    p = Project(project_id="x", scenic_spot="可可托海")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[_card(n) for n in cast])
    return s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))


@respx.mock
def test_s2_rejects_storyboard_that_names_nobody_from_the_cast():
    """线上真实形态(可可托海 2ee76074):cast 是「牧羊少年/老牧民」,而 cells 填的是
    「粗布羊毛衣的少年」「白发老者」。s4_pages 按名字查表一个都匹配不上 → 出场角色为空 →
    整部作品所有页走 text2img、无任何参考图,而 missing_refs 算的是"出场角色里谁缺三视图",
    出场为空自然"没人缺" —— 锚点和告警一起失效,用户毫无察觉。
    44 部历史作品里 43 部都至少命中一个主角名,全零命中在正常情况下不会发生,故直接判失败。"""
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(
            _cast_cells([["粗布羊毛衣的少年"], ["白发老者"]]), ensure_ascii=False)}}]}))
    with pytest.raises(ValueError, match="出场角色"):
        _run_with(_cast_cells([]), ["牧羊少年", "老牧民"])


@respx.mock
def test_s2_allows_extra_walk_on_names_when_cast_still_matches():
    """S2 额外列群众演员(百姓/村民/弓箭手)是历史上一直有的,主角名字对得上就不影响锚点,
    不能因此判失败——早期多部作品都是这个形态且成片正常。"""
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(
            _cast_cells([["牧羊少年"], ["村民", "牧羊少年"]]), ensure_ascii=False)}}]}))
    p = _run_with(_cast_cells([]), ["牧羊少年", "老牧民"])
    assert p.status["s2"] == "done"
    assert p.storyboard[1].characters == ["村民", "牧羊少年"]   # 群众演员原样保留


@respx.mock
def test_s2_allows_pages_with_no_characters_at_all():
    """纯景物页(characters 为空)合法,不该被新校验误伤。"""
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(
            _cast_cells([[], []]), ensure_ascii=False)}}]}))
    assert _run_with(_cast_cells([]), ["牧羊少年"]).status["s2"] == "done"


def test_s2_system_scopes_the_no_name_rule_to_visual_desc():
    """那条"不要写角色名"必须写清只管 visual_desc:模型把它泛化到 characters 字段,
    就是上面那个锚点全失效的成因。"""
    system = s2_storyboard.SYSTEM
    assert "characters" in system
    assert "原样" in system or "角色表" in system
