from typing import Literal

from pydantic import BaseModel, Field

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


class StoryboardCell(BaseModel):
    index: int
    scene_ref: str
    visual_desc: str
    characters: list[str]
    caption: str = Field(max_length=80)
    emotion: str
    image: str = ""
    audio: str = ""
    duration_ms: int = 0
    status: Literal["draft", "confirmed", "failed"] = "draft"


class GenerationParams(BaseModel):
    duration_min: Literal[1, 3, 5] = 3
    audience: Literal["儿童", "大众"] = "大众"
    tone: Literal["温情", "奇幻", "悬疑"] = "温情"


class Project(BaseModel):
    project_id: str
    scenic_spot: str
    params: GenerationParams = Field(default_factory=GenerationParams)
    status: dict[str, str] = Field(default_factory=dict)
    legend_candidates: list[Legend] = Field(default_factory=list)
    legend: Legend | None = None
    script: Script | None = None
    style_preset: str = "guofeng_ink"
    storyboard: list[StoryboardCell] = Field(default_factory=list)
    bgm: str = ""
    output: dict[str, str] = Field(default_factory=dict)
