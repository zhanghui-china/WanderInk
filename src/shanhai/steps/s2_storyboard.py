from pydantic import BaseModel

from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, StoryboardCell
from shanhai.steps.s1_script import is_narrator

PAGE_TARGETS = {1: (8, 10), 3: (20, 24), 5: (32, 40)}
EMOTIONS = {"宁静", "欢快", "紧张", "悲伤", "神秘", "恢弘", "温馨"}

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
    user = (f"页数要求:{lo}~{hi} 页。\n剧本 JSON:\n"
            + project.script.model_dump_json(indent=1))
    project.storyboard = llm.structured(SYSTEM, user, _Cells).cells
    for cell in project.storyboard:   # 防御:剔除误入出场角色的旁白/叙事者
        cell.characters = [n for n in cell.characters if not is_narrator(n)]
    project.status["s2"] = "done"
    return project
