from shanhai.providers.llm import LLMClient
from shanhai.safety import find_sensitive
from shanhai.schema import Project, Script

WORD_TARGETS = {1: 210, 3: 650, 5: 1100}
NARRATOR_KEYS = ("旁白", "叙事", "narrator", "解说")  # 这些是解说声音,不是画面角色


def is_narrator(name: str, role: str = "") -> bool:
    text = f"{name}{role}".lower()
    return any(k in text for k in NARRATOR_KEYS)


SYSTEM = """你是儿童文学与文旅内容编剧。把给定的景区传说改编为结构化剧本。
规则:
- 保留传说核心情节,不魔改结局
- 受众为"儿童"时,自动规避暴力、恐怖、血腥细节,用温和意象替代
- 旁白承担主要叙事,对白精炼;所有旁白+对白总字数命中目标字数 ±20%
- characters 只列出画面中出现的可视人物角色;旁白/叙事者是解说声音、不是画面角色,绝不列入 characters;主要角色不超过 4 个,appearance 用可视觉化的外貌关键词
- characters 必须按重要度降序排列,主角/核心角色在最前(下游只为前 4 个角色生成三视图参考,顺序错会让主角失去一致性锚点)

叙事框架(引人入胜的故事结构):
- 冷开场钩子:第一幕第一场用悬念、冲突或强画面抓住观众,避免平铺直叙的背景交代式开头;可勾连景区意象(如"这座塔下,压着一段千年往事…")
- 起承转合:整体遵循起(setup)→承(发展)→转(转折/高潮)→合(收束)四段式;张力逐幕递增,高潮置于后段
- 情感落点:结尾给出清晰的情感收束(温情/怅惘/释然),呼应主题
- 避免虚构或引入近现代真实政治人物、政治事件等敏感内容;传说本身涉及此类背景时,
  聚焦民俗/情感/文化侧面改编,避开政治敏感表述"""


def _script_text(script: Script) -> str:
    parts = [script.title, script.theme]
    for act in script.acts:
        for scene in act.scenes:
            parts.append(scene.description)
            parts.append(scene.narration)
            parts.extend(d.line for d in scene.dialogues)
    for c in script.characters:
        parts.append(f"{c.name}{c.role}{c.personality}{c.appearance}")
    return "".join(parts)


# hermes-agent 的"编剧大师"skill:system 前置 /screenwriter-master 显式触发,尾缀"别反问"
# 压住它的多轮反问式工作流,单轮直出 JSON(实测有效)。仅在 S1 后端确为 hermes-agent 时由
# 调用方传 use_skill=True;单次 ~16.5 万 token / ~400s,故 retries 降到 1 封顶最坏两次尝试。
SKILL_PREFIX = "/screenwriter-master\n\n"
SKILL_SUFFIX = "\n\n【一次性给全信息,请勿反问,直接产出成品剧本】"


def run(project: Project, llm: LLMClient, use_skill: bool = False) -> Project:
    if project.legend is None:
        raise ValueError("先完成 S0 并选定传说")
    words = WORD_TARGETS[project.params.duration_min]
    user = (f"传说:《{project.legend.title}》\n梗概:{project.legend.summary}\n"
            f"目标总字数:{words}\n受众:{project.params.audience}\n基调:{project.params.tone}")
    if use_skill:
        script = llm.structured(SKILL_PREFIX + SYSTEM + SKILL_SUFFIX, user, Script, retries=1)
    else:
        script = llm.structured(SYSTEM, user, Script)
    hits = find_sensitive(_script_text(script))
    if hits:
        raise ValueError(f"生成剧本涉及敏感内容({'、'.join(hits)}),已阻止生成")
    project.script = script
    # 防御:即便 LLM 仍把旁白/叙事者塞进 characters,也剔除,避免占三视图名额、被误画
    project.script.characters = [c for c in project.script.characters
                                 if not is_narrator(c.name, c.role)]
    project.status["s1"] = "done"
    return project
