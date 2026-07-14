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
- characters 只列该页画面中真正出现的可视人物,不含旁白/叙事者(旁白是解说声音不是画面角色)
- emotion 只能从这些标签里选:宁静/欢快/紧张/悲伤/神秘/恢弘/温馨
- index 从 1 开始连续编号
- 页尾悬念提示:每页 caption 顺势在页尾留一点悬念或期待感(承上启下),避免每页都平淡收尾;但不得牺牲"连起来能独立讲通故事"这一硬约束"""


class _Cells(BaseModel):
    cells: list[StoryboardCell]


def run(project: Project, llm: LLMClient) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    lo, hi = PAGE_TARGETS[project.params.duration_min]
    system = SYSTEM + (PANEL_RULES if project.params.multi_panel else "")
    user = (f"页数要求:{lo}~{hi} 页。\n剧本 JSON:\n"
            + project.script.model_dump_json(indent=1))
    project.storyboard = llm.structured(system, user, _Cells).cells
    if len(project.storyboard) == 0:  # 诚实链:零页不算成功,让管线记 error 而非空书
        raise ValueError("分镜为空,S2 未产出任何页")
    # index 强制重排为 1..n:LLM 可能给出重复/跳号 index,而下游 S4/S5/S6 与并行落盘
    # 均以 page_{index:02d} 为唯一文件名——重复 index 会让并行页互相踩踏临时/产物文件(丢解说)。
    for i, cell in enumerate(project.storyboard, 1):
        cell.index = i
    for cell in project.storyboard:   # 防御:剔除误入出场角色的旁白/叙事者
        cell.characters = [n for n in cell.characters if not is_narrator(n)]
    for cell in project.storyboard:  # 防御:LLM 可能无视上限,强制裁到 MAX_PANELS_PER_PAGE
        cell.panels = cell.panels[:MAX_PANELS_PER_PAGE]
    project.status["s2"] = "done"
    return project
