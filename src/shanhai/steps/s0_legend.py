"""S0 传说检索。骨架局限:无联网检索,靠 LLM 知识 + 强制来源标注;M2 接检索 API。"""
from pydantic import BaseModel

from shanhai.providers.llm import LLMClient
from shanhai.schema import Legend, Project

SYSTEM = """你是文旅内容研究员。给定景区名称,列出 2~5 个与之相关的历史传说。
规则:
- 每个传说标注来源类型:正史/地方志/民间传说/文学作品,不得把传说包装成史实
- sources 给出可核查的出处(书名、方志名或链接);无法给出可靠出处的不要列
- 确实没有可靠传说时返回空列表,不要编造"""


class _Candidates(BaseModel):
    candidates: list[Legend]


def run(project: Project, llm: LLMClient) -> Project:
    result = llm.structured(SYSTEM, f"景区名称:{project.scenic_spot}", _Candidates)
    project.legend_candidates = result.candidates
    project.status["s0"] = "done"
    return project


def from_text(project: Project, llm: LLMClient, story_text: str) -> Project:
    summary = llm.chat("把用户提供的故事压缩成 200 字以内的中文梗概,只输出梗概。", story_text)
    project.legend = Legend(title=f"{project.scenic_spot}·自备故事", summary=summary,
                            source_type="原创演绎", sources=["用户自备文本"])
    project.status["s0"] = "done"
    return project
