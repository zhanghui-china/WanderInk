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


# ---------- 分格补齐(用户勾了分格排版就该每页都分格) ----------

def _panel_cells(panel_counts: list[int]) -> dict:
    """按 panel_counts 造页:0 表示该页 LLM 没给分格(旧行为下就静默变整页单图)。"""
    return {"cells": [
        {"index": i, "scene_ref": "1-1", "visual_desc": f"画面{i}", "characters": ["白素贞"],
         "caption": f"第{i}页的解说词。", "emotion": "宁静",
         "panels": [{"visual_desc": f"{i}-格{j}", "shot_type": "medium", "characters": []}
                    for j in range(1, n + 1)]}
        for i, n in enumerate(panel_counts, 1)]}


def _resp(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})


def _panel_project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.params.multi_panel = True
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    return p


@respx.mock
def test_s2_backfills_pages_the_model_left_unpaneled():
    """线上 f50b97f4「10 页只有 4 页分格」的回归:勾了分格就是每页都要分格,
    首轮漏掉的页必须再要一次,不能只截断不补足。"""
    backfill = {"items": [{"index": i, "panels": [
        {"visual_desc": f"补{i}-格{j}", "shot_type": "wide", "characters": []}
        for j in (1, 2, 3)]} for i in (1, 3, 4)]}
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[_resp(_panel_cells([0, 2, 0, 1])), _resp(backfill)])
    p = s2_storyboard.run(_panel_project(), LLMClient(BASE, "sk", "m"))
    assert len(route.calls) == 2                        # 首轮 + 补齐
    assert [len(c.panels) for c in p.storyboard] == [3, 2, 3, 3]
    assert p.storyboard[1].panels[0].visual_desc == "2-格1"   # 已达标的页不被覆盖
    assert p.status["panels"] == "4/4"
    assert p.status["s2"] == "done"
    # 只补 panels 不改原文
    assert [c.caption for c in p.storyboard] == [f"第{i}页的解说词。" for i in range(1, 5)]


@respx.mock
def test_s2_skips_backfill_when_every_page_already_paneled():
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[_resp(_panel_cells([2, 3, 4]))])
    p = s2_storyboard.run(_panel_project(), LLMClient(BASE, "sk", "m"))
    assert len(route.calls) == 1        # 不做无用请求
    assert p.status["panels"] == "3/3"


@respx.mock
def test_s2_backfill_treats_single_panel_as_unpaneled():
    """1 格会被 paneling.LAYOUTS[1] 排成铺满整页,和单图页长得一样,必须算作没分格。"""
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[_resp(_panel_cells([1])), _resp({"items": []})])
    p = s2_storyboard.run(_panel_project(), LLMClient(BASE, "sk", "m"))
    assert len(route.calls) == 2
    assert p.status["panels"] == "0/1"   # 补齐没成功,如实记 0


@respx.mock
def test_s2_backfill_failure_does_not_discard_the_storyboard():
    """补齐这一步抛异常会被 api._save_error 回滚成盘上旧快照——整份分镜连同这一轮
    跑好的一切全部白跑。补齐失败只能退化成"这几页还是单图",不能毁掉 S2。"""
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[_resp(_panel_cells([0, 2])),
                     _resp({"cells": []}), _resp({"cells": []}), _resp({"cells": []})])
    p = s2_storyboard.run(_panel_project(), LLMClient(BASE, "sk", "m"))
    assert len(p.storyboard) == 2                 # 分镜完好
    assert p.status["s2"] == "done"
    assert p.status["panels"] == "1/2"            # 缺口如实可见
    assert len(route.calls) >= 2


@respx.mock
def test_s2_multi_panel_system_prompt_drops_the_one_panel_per_page_line():
    """「一页一格」与「每页必须 2~4 格」不能同时出现在一份 system prompt 里——
    这个自相矛盾正是模型大多数页给 0 格的直接原因。"""
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[_resp(_panel_cells([2]))])
    s2_storyboard.run(_panel_project(), LLMClient(BASE, "sk", "m"))
    system = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "一页一格" not in system
    assert "可以只给 1 格" not in system and "不必每页都用满格数" not in system
    assert "不允许只给 1 格" in system     # 许可式措辞换成禁止式


def test_s2_no_backfill_when_multi_panel_off():
    """单图模式一次额外请求都不该发(也是 use_skill 那两个断言 call_args 的测试的前提)。"""
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.structured.return_value = s2_storyboard._Cells.model_validate(CELLS)
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, llm)
    assert llm.structured.call_count == 1
    assert "panels" not in p.status


# ---------- 相邻页解说词去重 ----------

def _sb(captions: list[str]) -> dict:
    return {"cells": [
        {"index": i, "scene_ref": "1-1", "visual_desc": f"画面{i}", "characters": [],
         "caption": cap, "emotion": "宁静"} for i, cap in enumerate(captions, 1)]}


@respx.mock
def _run_captions(captions: list[str]) -> list[str]:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(_sb(captions), ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="北京城")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    return [c.caption for c in p.storyboard]


def test_s2_drops_recap_clause_that_opens_the_next_page():
    """线上真实案例(「碧血海外录」d48ce1e4 页 6→7):S2 把一段剧本 narration 切成多页时,
    习惯把上一页的尾句照抄到下一页当承接。剧本里「他遍历北方」只有一次，是切分时抄出来的。
    旁白是连着播的，听众会听到同一句连说两遍。"""
    out = _run_captions([
        "因其父之名与一身绝学，袁承志被江湖群豪推为七省盟主。他遍历北方，洞察天下大势。",
        "他遍历北方，见流民四起，田地荒芜，深知唯有扫除腥膻，方能再造乾坤。",
    ])
    assert out[0].endswith("他遍历北方，洞察天下大势。")          # 上一页原样不动
    assert out[1] == "见流民四起，田地荒芜，深知唯有扫除腥膻，方能再造乾坤。"


def test_s2_keeps_mid_caption_echo_as_deliberate_callback():
    """句中重复多数是收尾页复现前文金句，是有意的回环呼应，不是啰嗦。
    机器分不清"呼应"和"重复"，只记日志、一字不改。"""
    out = _run_captions([
        "她终于明白，琴声归处不在皇宫。你只管做你自己。",
        "千年流转，往事散尽。你只管做你自己——此后千年，琴声仍在。",
    ])
    assert out[1] == "千年流转，往事散尽。你只管做你自己——此后千年，琴声仍在。"


def test_s2_does_not_trim_when_remainder_would_be_too_short():
    """删完只剩几个字的话，这一页的旁白短得不成句、配音节奏也会怪，宁可留着重复。"""
    out = _run_captions([
        "少年抚剑长吟，想起父亲遗书，决定连夜下山去。",
        "决定连夜下山去，走了。",
    ])
    assert out[1] == "决定连夜下山去，走了。"      # 剩余不足，保持原样


def test_s2_ignores_short_shared_clause():
    """三字以内的共同小句多是虚词/主语(「他望着」「于是」)，删了会把句子弄断。"""
    out = _run_captions([
        "他望着远处的山影，久久不语，心里翻涌着旧事。",
        "他望着，忽然想起父亲临别时说过的那句话，眼里有了光。",
    ])
    assert out[1].startswith("他望着，")


def test_s2_only_dedupes_adjacent_pages():
    """只管相邻页:隔页撞句听不出来，而且常是有意的结构呼应。"""
    out = _run_captions([
        "他遍历北方，洞察天下大势，心中已有主张。",
        "闯军大营，篝火熊熊，李岩引他入帐相见。",
        "他遍历北方，见流民四起，田地荒芜，深知唯有扫除腥膻。",
    ])
    assert out[2].startswith("他遍历北方，")


def test_s2_system_bans_recap_as_transition():
    system = s2_storyboard.SYSTEM
    assert "复述" in system or "重复" in system
