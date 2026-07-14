from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["正史", "地方志", "民间传说", "文学作品", "原创演绎"]


class Legend(BaseModel):
    title: str
    summary: str
    source_type: SourceType
    sources: list[str]


class Dialogue(BaseModel):
    character: str
    line: str


class Scene(BaseModel):
    description: str
    characters: list[str]
    narration: str
    dialogues: list[Dialogue] = Field(default_factory=list)


class Act(BaseModel):
    scenes: list[Scene]


class CharacterCard(BaseModel):
    name: str
    role: str
    personality: str
    appearance: str
    feature_prompt: str = ""
    turnaround_image: str = ""
    locked: bool = False


class Script(BaseModel):
    title: str
    theme: str
    acts: list[Act]
    characters: list[CharacterCard]


class Panel(BaseModel):
    """分格漫画的单个格子(仅 params.multi_panel=True 时使用)。"""
    visual_desc: str
    shot_type: Literal["wide", "medium", "closeup", "insert"] = "medium"
    characters: list[str] = Field(default_factory=list)
    image: str = ""  # S4 填入,该格自己的生成图相对路径


class StoryboardCell(BaseModel):
    # validate_assignment:属性赋值也校验,堵住编辑端点绕过 caption max_length 写入
    # 永久不可加载的 project.json(pydantic ValidationError 是 ValueError 子类,端点直接转 400)。
    model_config = ConfigDict(validate_assignment=True)

    index: int
    scene_ref: str
    visual_desc: str
    characters: list[str]
    caption: str = Field(max_length=80)
    emotion: str
    image: str = ""
    audio: str = ""
    duration_ms: int = 0
    # silent=True 表示该页音频是静音兜底(非真人解说);用于状态诚实化与重跑重合成。
    silent: bool = False
    status: Literal["draft", "confirmed", "failed"] = "draft"
    panels: list[Panel] = Field(default_factory=list)  # 空 = 单图模式(现状不变)


class GenerationParams(BaseModel):
    duration_min: Literal[1, 3, 5] = 3
    audience: Literal["儿童", "大众"] = "大众"
    tone: Literal["温情", "奇幻", "悬疑"] = "温情"
    voice: str = ""
    speed: float = 1.0
    multi_panel: bool = False


class Project(BaseModel):
    project_id: str
    scenic_spot: str
    owner: str = ""   # 建作品时的登录用户名;历史项目(改造前所建)留空,前端显示"未知"
    params: GenerationParams = Field(default_factory=GenerationParams)
    status: dict[str, str] = Field(default_factory=dict)
    legend_candidates: list[Legend] = Field(default_factory=list)
    legend: Legend | None = None
    script: Script | None = None
    style_preset: str = "guofeng_ink"
    storyboard: list[StoryboardCell] = Field(default_factory=list)
    bgm: str = ""
    output: dict[str, str] = Field(default_factory=dict)

    def content_summary(self) -> dict[str, int]:
        """成片内容盘点:出图页、真人解说页、静音兜底页数。供状态诚实化与前端展示。"""
        return {
            "total": len(self.storyboard),
            "imaged": sum(1 for c in self.storyboard if c.status == "confirmed" and c.image),
            "narrated": sum(1 for c in self.storyboard if c.audio and not c.silent),
            "silent": sum(1 for c in self.storyboard if c.audio and c.silent),
        }

    def is_deliverable(self) -> bool:
        """真正的成片至少要有一页出图;全程失败的空片不算可交付。"""
        return self.content_summary()["imaged"] >= 1
