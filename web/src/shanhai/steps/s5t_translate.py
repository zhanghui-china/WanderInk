"""S5T 译文环节:把已定稿的中文分镜解说逐页译成目标语种,写入 cell.tracks[lang].caption。

为什么是"翻译"而不是"用目标语种重新创作":两语内容必须严格对应,才能共用同一套分镜与
画面(生图是整条流水线最贵的一步)。这也让 S0–S4 全程保持中文、一行都不用改。

幂等:已有非空译文的页跳过,与 S3/S4/S5 的断点续跑语义一致;想重译单页把该页译文清空即可。
"""
from pydantic import BaseModel, Field

from shanhai.providers.llm import LLMClient
from shanhai.schema import TRACK_CAPTION_MAX, LocalizedTrack, Project

# 目标语种码 -> (人类可读名, 给模型看的语言要求)。加一门语言只需在这里加一行。
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("英文", "natural, idiomatic English"),
}

BATCH = 8   # 每次请求翻译的页数上限:太大容易让模型丢页/串页,太小则请求次数多

SYSTEM = f"""你是一名文化旅游领域的资深译者,为景区连环画的解说词做翻译。

要求:
- 忠实传达原意与语气,不增删情节,不发挥。
- 面向外国游客,译文要自然流畅、口语化、适合朗读,不要翻译腔。
- 人名、地名、朝代等专有名词用通行译法;首次出现且外国读者可能陌生时,可加一个极简短的
  同位语说明(如 "the Tang dynasty"),但不要变成注释或长句。
- 每条译文控制在 {TRACK_CAPTION_MAX} 个字符以内,这是硬上限。
- 逐条对应输入的 index,不合并、不拆分、不遗漏、不改变 index。"""


class _Item(BaseModel):
    index: int
    text: str


class _Batch(BaseModel):
    items: list[_Item] = Field(default_factory=list)


def _pending(project: Project, lang: str) -> list[tuple[int, str]]:
    """待翻译页:有中文原文、且该语种还没有译文的页。"""
    out: list[tuple[int, str]] = []
    for cell in project.storyboard:
        if not cell.caption.strip():
            continue
        track = cell.tracks.get(lang)
        if track is not None and track.caption.strip():
            continue
        out.append((cell.index, cell.caption))
    return out


def run(project: Project, llm: LLMClient, lang: str = "en") -> Project:
    if lang not in LANGUAGES:
        raise ValueError(f"不支持的语种:{lang}(可选:{','.join(LANGUAGES)})")
    if not project.storyboard:
        raise ValueError("先完成 S2")
    name, style = LANGUAGES[lang]
    pending = _pending(project, lang)
    by_index = {c.index: c for c in project.storyboard}

    for start in range(0, len(pending), BATCH):
        chunk = pending[start:start + BATCH]
        payload = "\n".join(f"{i}. {text}" for i, text in chunk)
        user = (f"把下列中文解说词翻译成{name}({style})。"
                f"逐条对应,index 原样保留:\n\n{payload}")
        batch = llm.structured(SYSTEM, user, _Batch)
        for item in batch.items:
            cell = by_index.get(item.index)
            if cell is None or not item.text.strip():
                continue   # 模型偶发多吐/空吐,忽略而不是让整轮失败
            track = cell.tracks.setdefault(lang, LocalizedTrack())
            # 硬截断兜底:prompt 已要求上限,但模型不保证守约,超了会撞 schema 校验。
            # 上限引用 schema 的常量,不写字面量——同一个数字散在提示词/截断/schema 三处
            # 必然漂移(改 schema 忘了改这里,截断就成了永远不触发的死代码)。
            track.caption = item.text.strip()[:TRACK_CAPTION_MAX]

    translated = sum(1 for c in project.storyboard
                     if (t := c.tracks.get(lang)) is not None and t.caption.strip())
    total = sum(1 for c in project.storyboard if c.caption.strip())
    project.status[f"s5t_{lang}"] = "done" if translated == total else "partial"
    return project
