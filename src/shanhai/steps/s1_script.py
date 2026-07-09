from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, Script

WORD_TARGETS = {1: 210, 3: 650, 5: 1100}

SYSTEM = """你是儿童文学与文旅内容编剧。把给定的景区传说改编为结构化剧本。
规则:
- 保留传说核心情节,不魔改结局
- 受众为"儿童"时,自动规避暴力、恐怖、血腥细节,用温和意象替代
- 旁白承担主要叙事,对白精炼;所有旁白+对白总字数命中目标字数 ±20%
- characters 列出全部出场角色,主要角色不超过 4 个,appearance 用可视觉化的外貌关键词

叙事框架(引人入胜的故事结构):
- 冷开场钩子:第一幕第一场用悬念、冲突或强画面抓住观众,避免平铺直叙的背景交代式开头;可勾连景区意象(如"这座塔下,压着一段千年往事…")
- 起承转合:整体遵循起(setup)→承(发展)→转(转折/高潮)→合(收束)四段式;张力逐幕递增,高潮置于后段
- 情感落点:结尾给出清晰的情感收束(温情/怅惘/释然),呼应主题"""


def run(project: Project, llm: LLMClient) -> Project:
    if project.legend is None:
        raise ValueError("先完成 S0 并选定传说")
    words = WORD_TARGETS[project.params.duration_min]
    user = (f"传说:《{project.legend.title}》\n梗概:{project.legend.summary}\n"
            f"目标总字数:{words}\n受众:{project.params.audience}\n基调:{project.params.tone}")
    project.script = llm.structured(SYSTEM, user, Script)
    project.status["s1"] = "done"
    return project
