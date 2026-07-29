from pydantic import BaseModel

from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, StoryboardCell
from shanhai.steps.s1_script import is_narrator

PAGE_TARGETS = {1: (8, 10), 3: (20, 24), 5: (32, 40)}
EMOTIONS = {"宁静", "欢快", "紧张", "悲伤", "神秘", "恢弘", "温馨"}

MAX_PANELS_PER_PAGE = 4  # 每页分格上限,防成本失控

PANEL_RULES = """
分格模式:每页可以拆成 1~4 个格子(panels 字段),按剧情节奏决定格数——
平静场景可以只给 1 格(等价于铺满整页),高潮或转折场景给 3~4 格,不必每页都用满格数。
每个格子填写:
- visual_desc:该格自己的构图/景别/氛围(不是整页笼统描述)
- shot_type:wide(远景)/medium(中景)/closeup(特写)/insert(嵌入式特写叠加格,
  漫画里常见的裁成异形叠在其它格子上面的手法)
- characters:该格实际出现的角色,可以是页面角色的子集
每页最多一个 insert 格,只在情绪转折或关键台词处使用,不是每页都要有。
panels 数量最多 4 个,超出会被截断,请自行控制在这个范围内。"""

SYSTEM = """你是连环画分镜师。把剧本切分为一页一格的连环画分镜。
规则:
- caption 是该页的解说文案(旁白或对白),不超过 80 字
- 所有 caption 连起来必须能独立讲通整个故事——听众不看画面也能听懂,这是硬性要求
- visual_desc 描述构图、景别、光线、氛围,供绘图使用,不写文字内容
- visual_desc 只描述**一个瞬间**的静止画面,不要写"先…然后…""从…走到…"这类过程,
  也不要让同一个角色在同一页画面里出现多次(下游是一张图,写成过程会被画成分身)
- characters 只列该页画面中真正出现的可视人物,不含旁白/叙事者(旁白是解说声音不是画面角色)
- emotion 只能从这些标签里选:宁静/欢快/紧张/悲伤/神秘/恢弘/温馨
- index 从 1 开始连续编号
- 悬念只留在幕与幕、场与场的转折处,不是每页都要;大多数页正常收尾即可,但不得牺牲"连起来能独立讲通故事"这一硬约束
- caption 一律不得以问号结尾,不得用反问句或向听众提问来制造悬念;悬念要用陈述句的未尽之意来写(停在一个动作、一个未解释的意象、一句没说完的话)"""


class _Cells(BaseModel):
    cells: list[StoryboardCell]


# hermes-agent 的"导演大师"skill(与编剧大师联动,从剧本出发拆分镜):system 前置
# /director-master 显式触发,尾缀"别反问+只输出 JSON"压住它原生的多轮工作流/xlsx 分镜表输出,
# 单轮直出符合 _Cells schema 的 JSON(实测有效)。仅在 S2 后端确为 hermes-agent 时由调用方
# 传 use_skill=True;单次约 3.5 万 token/300s,故 retries 降到 1 封顶最坏两次尝试。
DIRECTOR_PREFIX = "/director-master\n\n"
DIRECTOR_SUFFIX = "\n\n【一次性给全信息,请勿反问,直接产出成品;严格只输出 JSON,不要输出 xlsx/表格/其它格式】"


def run(project: Project, llm: LLMClient, use_skill: bool = False) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    lo, hi = PAGE_TARGETS[project.params.duration_min]
    system = SYSTEM + (PANEL_RULES if project.params.multi_panel else "")
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
    for cell in project.storyboard:
        if not project.params.multi_panel:
            # 用户没开分格就一律清空。上面的 PANEL_RULES 开关只控制"怎么用分格"这段自然语言
            # 指令,而 llm.structured 无条件把含 panels 字段(带 shot_type 枚举和中文说明)的
            # 完整 JSON Schema 喂给模型——模型完全可能好心自己填,填了就会让 S4 走分格路径。
            cell.panels = []
        else:  # 防御:LLM 可能无视上限,强制裁到 MAX_PANELS_PER_PAGE
            cell.panels = cell.panels[:MAX_PANELS_PER_PAGE]
    project.status["s2"] = "done"
    return project
