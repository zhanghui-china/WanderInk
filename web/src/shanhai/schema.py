from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["正史", "地方志", "民间传说", "文学作品", "原创演绎"]

# 单页解说文案的**硬熔断**长度(不是给模型的目标,S2/S5t 的提示词里另有更保守的目标值)。
#
# CAPTION_MAX 从 80 放宽到 120。80 不是随手定的:它精确对应 typeset 烧录字幕的两行容量
#(Noto 40px、可用宽 1680px → 每行 42 字 → 两行 84)。但线上实测 664 条 caption 中位数
# 才 39 字、90 分位 55,模型只是偶尔写飞一句——而写飞一句会让**整个 S2 结构化输出判失败、
# 整批分镜全废**(「石坊温热」就是这么挂的:cells.23.caption 超长 → 22 页分镜一起没了,
# 且 llm.structured 的 3 次重试全部撞同一堵墙)。120 给离群值留余量。
# ⚠️ 与 typeset.overlay_image 的行数上限强耦合:120 字要三行(三行容量 126 字),那边的
# [:2] 已同步改成 [:3]。再往上调必须先确认烧录容量跟得上,否则导出的 PDF/ZIP 会**静默截断**。
#
# TRACK_CAPTION_MAX(附加语种)必须跟着一起放宽:英文同义内容约为中文 2~2.5 倍,
# 一条 120 字的中文译成英文约 280 字,只放宽中文那头会让 S5t 撞上旧的 240、
# 把整批翻译判失败——与 S2 那次同一形态。s5t_translate 的提示词与硬截断都引用这个常量,
# 不再各写一个字面量(那正是这类数字迟早漂移的成因)。
CAPTION_MAX = 120
TRACK_CAPTION_MAX = 300


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
    # 最近一次成功生成三视图所花的时间(重绘直接覆盖,不累计),与 turnaround_image 同进同退。
    # 有默认值 → 老 project.json 零迁移(老作品显示不出读数,重绘后才有)。
    turnaround_gen_ms: int = 0
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

    caption: str = Field(default="", max_length=TRACK_CAPTION_MAX)
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
    # 熔断值与理由见文件顶部的 CAPTION_MAX。提示词里仍写"不超过 80 字"
    #(s2_storyboard.SYSTEM):那是给模型的**目标**,这里是熔断,两个数字不同是有意的。
    caption: str = Field(max_length=CAPTION_MAX)
    emotion: str
    image: str = ""
    audio: str = ""
    duration_ms: int = 0
    image_gen_ms: int = 0  # 最近一次成功生成该页图片所花的时间(重绘直接覆盖,不累计)
    # 最近一次成功生成该页图片实际走的路径:"chat" / "edit" / "text2img" / "mixed"。
    # "mixed" 只出现在分格页:各格的参考图是按 panel.characters 逐格算的,一页里完全可能
    # "有人物的格走 edit、空镜格走 text2img",此时只记其中一格会说反话。
    image_route: str = ""
    # 本次生成请求指定的 LoRA 短名。注意:空串不等于"没用 LoRA"!ComfyUI 工作流模板里 LoRA
    # 节点是焊死存在的,shanhai 不指定时 DGX 上的 image-shim 会回落它自己的默认权重
    #(Real_Ani-Qwen_000001250.safetensors),而那个默认值 shanhai 这边无从得知。所以空串
    # 只表示"未指定,由后端决定",不是"无"——照这条字面意思做界面会做出一个说谎的界面。
    image_lora: str = ""
    # silent=True 表示该页音频是静音兜底(非真人解说);用于状态诚实化与重跑重合成。
    silent: bool = False
    status: Literal["draft", "confirmed", "failed"] = "draft"
    # 生成这一页时,出场角色里**没有三视图可用**的那些名字(S4 每轮重算并覆盖)。
    # 这些角色只有文字特征约束、没有视觉锚点,一致性无从保证——而在此之前这件事完全静默:
    # S4 唯一的护栏是"所有角色里至少有一个有三视图",三个角色活一个就通过,界面上什么都看不到。
    # 实测 DGX 上的 8f41283a 有 7 页在第一主角三视图产出前 18~33 分钟就画完了,全程无任何提示。
    missing_refs: list[str] = Field(default_factory=list)
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
    # 把解说词烧进画面(而非只封 MP4 软字幕轨)。默认开:浏览器根本不解析 MP4 内的
    # mov_text,下载的成片拿到微信/抖音这类地方软轨也等于不存在——用户看到的就是
    # "没有字幕"。关掉则回到纯软字幕轨。仅中文片生效(英文烧录待解决词级断行)。
    # 有默认值 → 老 project.json 零迁移。
    burn_subtitles: bool = True
    use_hermes_agent: bool = True
    master_skill: bool = False   # S1 用"编剧大师"+S2 用"导演大师"深度创作(需对应环节为 hermes-agent 后端,更慢更贵)


class Project(BaseModel):
    project_id: str
    scenic_spot: str
    owner: str = ""   # 建作品时的登录用户名;历史项目(改造前所建)留空,前端显示"未知"
    created_at: str = ""   # ISO 8601 UTC;建作品时写入,历史项目(改造前所建)留空,列表按 project.json mtime 兜底排序
    params: GenerationParams = Field(default_factory=GenerationParams)
    status: dict[str, str] = Field(default_factory=dict)
    story: str | None = None   # 用户自备故事原文;None = 走自动检索传说
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
