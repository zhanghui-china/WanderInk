import re

from pydantic import BaseModel, Field

from shanhai import paneling
from shanhai.providers.llm import LLMClient, LLMError
from shanhai.schema import Panel, Project, StoryboardCell
from shanhai.steps.s1_script import is_narrator

PAGE_TARGETS = {1: (8, 10), 3: (20, 24), 5: (32, 40)}
EMOTIONS = {"宁静", "欢快", "紧张", "悲伤", "神秘", "恢弘", "温馨"}

MAX_PANELS_PER_PAGE = 4  # 每页分格上限,防成本失控
# 每页分格下限。用户勾了「分格排版」就是要每页都分格,这是硬约束而不是建议:
# 线上「f50b97f4」10 页里只有 4 页分格,根因就是旧 PANEL_RULES 写着「平静场景可以只给
# 1 格、不必每页都用满格数」,而基础 SYSTEM 又说「一页一格」——模型按 prompt 办事,
# 大多数页给 0 格,代码只截断不补足,于是静默退回单图。
# 下限取 2 而不是 1:paneling.LAYOUTS[1] 是整页满铺,1 格与单图页产出完全同形,
# 用户肉眼分不出来,等于没分格。
# ⚠️ 这个下限卡的是 **paneling.regular_slots(独立版位数)**,不是 len(panels):
# insert 格叠在别的格子上面、不占独立版位,所以 [medium, insert] 虽然有 2 格,排出来
# 仍是整页满铺加一个角标(线上 f50b97f4 第 10 页,用户数分格页时数不到它)。
MIN_PANELS_PER_PAGE = 2

PANEL_RULES = f"""
分格模式:每页**必须**拆成 {MIN_PANELS_PER_PAGE}~{MAX_PANELS_PER_PAGE} 个格子(panels 字段),
按剧情节奏决定格数——平缓场景 2 格,高潮或转折场景 3~4 格。
不允许只给 1 格,也不允许留空:1 格会被排成铺满整页,和不分格的单图页长得一模一样,
等于用户勾的分格排版没生效。
每个格子填写:
- visual_desc:该格自己的构图/景别/氛围(不是整页笼统描述)
- shot_type:wide(远景)/medium(中景)/closeup(特写)/insert(嵌入式特写叠加格,
  漫画里常见的裁成异形叠在其它格子上面的手法)
- characters:该格实际出现的角色,可以是页面角色的子集
每页最多一个 insert 格,只在情绪转折或关键台词处使用,不是每页都要有。
insert 格是叠在别的格子上面的,**不占独立版位**:要用 insert 就得配至少 2 个非 insert 的格子
(即整页至少 3 格),否则这一页排出来仍是整页一张图,等于没分格。
panels 数量最多 {MAX_PANELS_PER_PAGE} 个,超出会被截断,请自行控制在这个范围内。"""

# 开头这句随分格开关切换:分格模式下**绝不能**出现「一页一格」——它与 PANEL_RULES
# 的「每页必须 2~4 格」直接打架,而两句都在同一个 system prompt 里。
HEAD_SINGLE = "你是连环画分镜师。把剧本切分为一页一格的连环画分镜。"
HEAD_PANEL = "你是连环画分镜师。把剧本切分为一页一幅的连环画分镜,每页再分成若干格子。"

# 角色名规则。**必须是单一真源**:S2 首轮与分格补齐两处 prompt 都要带上它。
# 补齐那次曾经漏掉,实测 LLM 立刻在格子的 visual_desc 里写出"巴特尔身穿深色蒙古袍",
# 而这段文字是直接进绘图 prompt 的——名字会被画成字面意思。
NAME_RULES = """- visual_desc 里用外貌或身份指代人物(如"灰衣少年"),不要写角色名(名字会进绘图 prompt 被画成字面意思)
- 上一条**只管 visual_desc**:characters 字段必须原样填角色表里的名字(如"牧羊少年"),
  不能改成外貌描述——那是下游按名字取角色设定图的唯一依据,改了就取不到"""

SYSTEM = f"""
规则:
- caption 是该页的解说文案(旁白或对白),不超过 80 字
- 所有 caption 连起来必须能独立讲通整个故事——听众不看画面也能听懂,这是硬性要求
- 但"独立讲通"指的是信息不缺,**不是**把上一页的句子再说一遍:严禁复述上一页已经讲过的
  内容来给本页开头(如上页结尾"他遍历北方",本页开头又写"他遍历北方,…")。
  旁白是连着播的,重复的话听众会听见说了两遍。承接靠剧情往前推,不靠重复
- visual_desc 描述构图、景别、光线、氛围,供绘图使用,不写文字内容
- visual_desc 只描述**一个瞬间**的静止画面,不要写"先…然后…""从…走到…"这类过程,
  也不要让同一个角色在同一页画面里出现多次(下游是一张图,写成过程会被画成分身)
{NAME_RULES}
- characters 只列该页画面中真正出现的可视人物,不含旁白/叙事者(旁白是解说声音不是画面角色)
- emotion 只能从这些标签里选:宁静/欢快/紧张/悲伤/神秘/恢弘/温馨
- index 从 1 开始连续编号
- 悬念只留在幕与幕、场与场的转折处,不是每页都要;大多数页正常收尾即可,但不得牺牲"连起来能独立讲通故事"这一硬约束
- caption 一律不得以问号结尾,不得用反问句或向听众提问来制造悬念;悬念要用陈述句的未尽之意来写(停在一个动作、一个未解释的意象、一句没说完的话)"""


class _Cells(BaseModel):
    cells: list[StoryboardCell]


class _PanelsOf(BaseModel):
    index: int
    panels: list[Panel] = Field(default_factory=list)


class _PanelBatch(BaseModel):
    items: list[_PanelsOf] = Field(default_factory=list)


BACKFILL_SYSTEM = f"""你是连环画分镜师。下面每一条是一页已经定稿的分镜,但缺少分格方案。
为**每一页**补出 {MIN_PANELS_PER_PAGE}~{MAX_PANELS_PER_PAGE} 个格子。
{PANEL_RULES}
格级字段沿用同一套规则:
{NAME_RULES}
逐条对应输入的 index,不合并、不拆分、不遗漏、不改变 index;
只补 panels,不要改动原有的 caption / visual_desc / characters。"""


# hermes-agent 的"导演大师"skill(与编剧大师联动,从剧本出发拆分镜):system 前置
# /director-master 显式触发,尾缀"别反问+只输出 JSON"压住它原生的多轮工作流/xlsx 分镜表输出,
# 单轮直出符合 _Cells schema 的 JSON(实测有效)。仅在 S2 后端确为 hermes-agent 时由调用方
# 传 use_skill=True;单次约 3.5 万 token/300s,故 retries 降到 1 封顶最坏两次尝试。
DIRECTOR_PREFIX = "/director-master\n\n"
DIRECTOR_SUFFIX = "\n\n【一次性给全信息,请勿反问,直接产出成品;严格只输出 JSON,不要输出 xlsx/表格/其它格式】"


# 小句分隔符。**刻意写成 Unicode 转义而不是字面量**:全角逗号「，」(U+FF0C)与 ASCII 逗号
# 长得几乎一样,手写字面量时我把两个 ASCII 逗号当成了"半角+全角"一对,于是这个字符类里根本
# 没有全角逗号——线上 caption 全是全角标点,切句只在「。」处生效,去重一条都没命中,
# 而单测里我用的又恰好是 ASCII 逗号,测试照样全绿。转义写法让"到底包含哪些字符"可核对。
_CLAUSE_CHARS = "，。；！？、：,;!?: "   # 全角七个 + 半角同款 + 空格
MIN_DUP_CLAUSE = 4     # 共同小句短于这个长度不动:三字以内多是虚词/主语(「他望着」「于是」),删了句子就断
# 裁完至少要剩这么多字才算还成句,不够则宁可留着重复。定在 6 是拿真实数据校准的:
# 「碧血海外录」页 4 裁完剩「纵身跃下苍龙岭。」8 字,是完全成立的一句旁白,阈值定 12 会把它挡掉;
# 而「走了。」这种 3 字残句必须挡住。配音那头有 MIN_MS 兜底,短句不会一闪而过。
MIN_KEPT_CHARS = 6


_CLAUSE_SEP = re.compile(f"[{re.escape(_CLAUSE_CHARS)}]")


def _clauses(text: str) -> list[str]:
    return [c for c in (x.strip() for x in _CLAUSE_SEP.split(text)) if c]


def _dedupe_adjacent_captions(project: Project) -> None:
    """删掉"下一页开头照抄上一页某个小句"那种承接式重复。

    S2 把一段较长的剧本 narration 切成多页时,惯用手法是把上一页的尾句再写一遍当引子——
    线上「碧血海外录」d48ce1e4 页 6 结尾"他遍历北方,洞察天下大势",页 7 开头又是
    "他遍历北方,见流民四起…"。剧本里这句只有一次,是切分时抄出来的。旁白连着播,
    听众就听见同一句说了两遍(用户原话:"前面如果跟后面重复,就不好听了")。
    根因是上面那条"连起来必须能独立讲通"的副作用:最省力的自成一体就是抄上页尾句。
    提示词已加了明禁,这里是代码兜底——提示词永远只是和模型拔河。

    **只裁下一页的首句,且只在逐字相同时裁。** 句中重复不动:实测那类多数出现在收尾页
    复现前文金句(「你只管做你自己」「画上了穹顶」),是有意的回环呼应、是好文笔,
    机器分不清"呼应"和"啰嗦",改了反而把写作手法拆了——只打日志,人工想改再改。
    也只看**相邻**页:隔页撞句听不出来,且常是有意的结构呼应。"""
    cells = project.storyboard
    for prev, cur in zip(cells, cells[1:]):
        prev_clauses = _clauses(prev.caption)
        cur_clauses = _clauses(cur.caption)
        if not prev_clauses or not cur_clauses:
            continue
        head = cur_clauses[0]
        if head in prev_clauses and len(head) >= MIN_DUP_CLAUSE:
            # 连同它后面那个分隔符一起去掉,剩下的就是本页真正往前推的内容。
            # lstrip 的字符集与切句的分隔符共用 _CLAUSE_CHARS 一个真源:分两处写的话,
            # 我第一版就是在这里漏了全角逗号,裁完剩下「,见流民四起…」带个头逗号。
            rest = cur.caption.partition(head)[2].lstrip(_CLAUSE_CHARS)
            if len(rest) >= MIN_KEPT_CHARS:
                print(f"第 {cur.index} 页开头复述了上一页的「{head}」,已删除")
                cur.caption = rest
                continue
            print(f"⚠️ 第 {cur.index} 页开头复述了上一页的「{head}」,但删掉后不足 "
                  f"{MIN_KEPT_CHARS} 字,保留原文")
        dup = [c for c in cur_clauses[1:] if c in prev_clauses and len(c) >= MIN_DUP_CLAUSE]
        if dup:
            print(f"⚠️ 第 {cur.index} 页与上一页有相同的句子 {dup}(在句中,可能是有意的呼应,未改动)")


def _check_cast_names(project: Project) -> None:
    """出场名单必须真的用角色表里的名字,否则整部作品静默失去角色一致性。

    S4 是按名字查表拿角色设定图的(s4_pages: `cards[n] for n in cell.characters if n in cards`)。
    模型若把 characters 写成外貌描述(「粗布羊毛衣的少年」而不是「牧羊少年」),匹配数为零 →
    每页都走 text2img、一张参考图都不传;而 missing_refs 算的是"出场角色里谁缺三视图",
    出场为空自然"没人缺" —— **锚点和告警一起失效**,界面上一切正常。线上「可可托海」
    2ee76074 就是这样 9 页全无锚点,用户毫无察觉;44 部作品里仅此一例。

    全零命中判失败(与上面"分镜为空不算成功"同一条诚实原则:宁可让管线记 error 重跑 S2,
    也不产出一部注定没有一致性的作品);部分失配只告警——S2 额外列群众演员(百姓/村民/
    弓箭手)是历史上一直有的形态,主角名字对得上,锚点照常工作,拦了才是误伤。"""
    if project.script is None:
        return
    cast = {c.name for c in project.script.characters}
    used = {n for cell in project.storyboard for n in cell.characters}
    if not cast or not used:      # 没有角色表、或全是纯景物页:无从校验,也无锚点可谈
        return
    if not (used & cast):
        raise ValueError(
            f"分镜的出场角色没有一个用了角色表里的名字(角色表:{sorted(cast)};"
            f"分镜里写的:{sorted(used)})——下游取不到角色设定图,整部作品会失去角色一致性")
    unknown = used - cast
    if unknown:
        print(f"⚠️ 分镜里有 {sorted(unknown)} 不在角色表中(通常是群众演员),"
              f"这些人物无设定图锚点")


def _backfill_panels(project: Project, llm: LLMClient) -> None:
    """给分格数不足 MIN_PANELS_PER_PAGE 的页再要一次分格方案。

    prompt 改硬之后模型仍可能漏页(它对"平缓场景"的判断不受我们控制),而代码里只截断
    不补足的老行为就是"10 页只分格 4 页"的直接原因。形状照抄 s5t_translate.run:
    算出待补子集 → 一次结构化请求 → 按 index 查表合并,合并端对模型的多吐/空吐容错。

    ⚠️ 异常必须吞在这里:S2 抛异常会让 api._save_error 回滚到盘上的旧快照,
    整份分镜(以及这一轮已经跑好的一切)全部白跑。补齐失败的代价不该是这个,
    退化成"这几页还是单图"即可,由调用方记进 status 让它可见。
    """
    pending = [c for c in project.storyboard
               if paneling.regular_slots(c.panels) < MIN_PANELS_PER_PAGE]
    if not pending:
        return
    by_index = {c.index: c for c in project.storyboard}
    payload = "\n".join(
        f"{c.index}. 画面:{c.visual_desc} / 解说:{c.caption} / 角色:{','.join(c.characters)}"
        for c in pending)
    try:
        batch = llm.structured(BACKFILL_SYSTEM, f"待补分格的页:\n\n{payload}", _PanelBatch)
    except LLMError as e:
        print(f"⚠️ 分格补齐失败,{len(pending)} 页仍为整页单图:{e}")
        return
    for item in batch.items:
        cell = by_index.get(item.index)
        panels = [p for p in item.panels if p.visual_desc.strip()]
        if cell is None or paneling.regular_slots(panels) < MIN_PANELS_PER_PAGE:
            continue   # 模型偶发多吐/空吐/仍旧只给 1 格,忽略而不是让整轮失败
        cell.panels = panels[:MAX_PANELS_PER_PAGE]


def run(project: Project, llm: LLMClient, use_skill: bool = False) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    lo, hi = PAGE_TARGETS[project.params.duration_min]
    head = HEAD_PANEL if project.params.multi_panel else HEAD_SINGLE
    system = head + SYSTEM + (PANEL_RULES if project.params.multi_panel else "")
    user = (f"页数要求:{lo}~{hi} 页。\n剧本 JSON:\n"
            + project.script.model_dump_json(indent=1))
    if use_skill:
        result = llm.structured(DIRECTOR_PREFIX + system + DIRECTOR_SUFFIX, user, _Cells, retries=1)
    else:
        result = llm.structured(system, user, _Cells)
    project.storyboard = result.cells
    if len(project.storyboard) == 0:  # 诚实链:零页不算成功,让管线记 error 而非空书
        raise ValueError("分镜为空,S2 未产出任何页")
    # index 强制重排为 1..n:LLM 可能给出重复/跳号 index,而下游 S4/S5/S6 与并行落盘
    # 均以 page_{index:02d} 为唯一文件名——重复 index 会让并行页互相踩踏临时/产物文件(丢解说)。
    for i, cell in enumerate(project.storyboard, 1):
        cell.index = i
    for cell in project.storyboard:   # 防御:剔除误入出场角色的旁白/叙事者
        cell.characters = [n for n in cell.characters if not is_narrator(n)]
    _dedupe_adjacent_captions(project)
    _check_cast_names(project)
    for cell in project.storyboard:
        if not project.params.multi_panel:
            # 用户没开分格就一律清空。上面的 PANEL_RULES 开关只控制"怎么用分格"这段自然语言
            # 指令,而 llm.structured 无条件把含 panels 字段(带 shot_type 枚举和中文说明)的
            # 完整 JSON Schema 喂给模型——模型完全可能好心自己填,填了就会让 S4 走分格路径。
            cell.panels = []
        else:  # 防御:LLM 可能无视上限,强制裁到 MAX_PANELS_PER_PAGE
            cell.panels = cell.panels[:MAX_PANELS_PER_PAGE]
    if project.params.multi_panel:
        _backfill_panels(project, llm)
        # 诚实链:补齐尽力而为,实际分格了几页要记下来。
        # 细节**不能**塞进 status["s2"]:前端 ProgressSteps 对环节键是严格相等判断
        # 'done'/'partial',写成 "done: 4/10" 会让界面把 S2 当成没跑完。
        # 独立键是既有做法(s5_audio 的 status["bgm"])。
        paneled = sum(1 for c in project.storyboard
                      if paneling.regular_slots(c.panels) >= MIN_PANELS_PER_PAGE)
        project.status["panels"] = f"{paneled}/{len(project.storyboard)}"
    project.status["s2"] = "done"
    return project
