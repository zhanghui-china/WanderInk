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
    # 用户上传的角色参考图(相对 workdir 的路径),有则 S3 走单图编辑而非文生图。
    # 有默认值 → 老 project.json 零迁移。
    # 已知限制:重跑 S1 会重建 characters,该字段随之丢失、图片文件成孤儿。但 _STEP_NAMES
    # 不含 s1(网页上根本触发不到),只有 CLI 全量重跑会碰到,不值得为此写迁移/清理逻辑。
    reference_image: str = ""
    locked: bool = False


class Script(BaseModel):
    title: str
    theme: str
    acts: list[Act]
    characters: list[CharacterCard]


class Panel(BaseModel):
    """漫画格子的单个构图描述(仅 params.multi_panel=True 时使用)。"""
    visual_desc: str
    shot_type: Literal["wide", "medium", "closeup", "insert"] = "medium"
    characters: list[str] = Field(default_factory=list)
    image: str = ""  # S4 填入,该格自己的生成图相对路径


class LocalizedTrack(BaseModel):
    """非主语言(中文)的一页译文与配音。主语言仍走 StoryboardCell 上的原字段
    (caption/audio/duration_ms/silent),这样既有项目文件、既有代码路径与既有测试全部零改动,
    多语种只是旁挂上来的一层。"""
    model_config = ConfigDict(validate_assignment=True)

    # 英文表达同义内容的字符数约为中文的 2~2.5 倍,主语言那条 80 的上限不够用。
    caption: str = Field(default="", max_length=240)
    audio: str = ""
    duration_ms: int = 0
    silent: bool = False


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
    image_gen_ms: int = 0  # 最近一次成功生成该页图片所花的时间(重绘直接覆盖,不累计)
    # silent=True 表示该页音频是静音兜底(非真人解说);用于状态诚实化与重跑重合成。
    silent: bool = False
    status: Literal["draft", "confirmed", "failed"] = "draft"
    panels: list[Panel] = Field(default_factory=list)  # 空 = 单图模式(现状不变)
    # 语种码 -> 该语种的译文与配音(如 "en");中文不进这里,仍用上面的原字段。
    tracks: dict[str, LocalizedTrack] = Field(default_factory=dict)


class GenerationParams(BaseModel):
    duration_min: Literal[1, 3, 5] = 3
    audience: Literal["儿童", "大众"] = "大众"
    tone: Literal["温情", "奇幻", "悬疑"] = "温情"
    voice: str = ""
    voice_en: str = ""   # 英文轨音色;留空则回落到配置层的 tts_voice_en
    speed: float = 1.0
    multi_panel: bool = False
    # 默认开:与改造前"能生成就配"的隐式行为一致(BGM 链路一直是完整的,只是 music-shim
    # 的工作流模板路径写错、每次秒返 500 被静默降级吞掉,所以从未成功过)。
    # 有默认值 → 老 project.json 零迁移。
    bgm: bool = True
    use_hermes_agent: bool = True
    master_skill: bool = False   # S1 用"编剧大师"+S2 用"导演大师"深度创作(需对应环节为 hermes-agent 后端,更慢更贵)


class Project(BaseModel):
    project_id: str
    scenic_spot: str
    owner: str = ""   # 建作品时的登录用户名;历史项目(改造前所建)留空,前端显示"未知"
    created_at: str = ""   # ISO 8601 UTC;建作品时写入,历史项目(改造前所建)留空,列表按 project.json mtime 兜底排序
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
