"""把「山音超级编剧/导演大师」的 skill 正文组装成一段 system 前缀。

Designed by @山音(MIT)。正文不在本仓库里,用 scripts/fetch-skills.py 从上游公开仓库取到
assets/skills/(见 .gitignore 与该脚本的说明)。

## 为什么不需要 agent 框架

两个 skill 都是「主文件 + references 按需读」的结构:Claude Skills 的运行时让模型用文件工具
自己决定读哪几个。我们没有文件工具——但**那个"按需"的决定,我们能从作品参数里直接算出来**:
篇幅由 duration_min 定、类型由 tone 定。所以把选中的几份拼成一段前缀即可,不必让模型去读文件,
也就不需要 agent 循环。

实测印证:hermes 加载 skill 后 prompt_tokens 增加约 20k,而"必读 + 选中的一份"正是这个量级
(编剧约 2.3~2.9 万字、导演约 3.5 万字)。整包塞进去(编剧 13.6 万字节、导演 7.4 万字)既超量
又没必要——四份 format 里一次只用得上一份。

## 已知取舍:压扁了它的多步工作流

两个 skill 的 SKILL.md 都写着「严格按步骤顺序执行」「每一步完成后暂停,等待用户确认」
「绝不一次性生成所有内容」。我们的管线是批量生成、没有人在环中,所以沿用接 hermes 时的做法:
用尾缀(见 s1_script.SKILL_SUFFIX / s2_storyboard.DIRECTOR_SUFFIX)把它压成单轮直出 JSON。
拿到的是压扁版而非 skill 设计的完整形态——这与今天走 hermes 的效果一致,故可直接对照;
真要跑完整五步是另一个课题(要编排、要处理耗时与取消粒度),不在本模块范围。
"""
from functools import lru_cache
from pathlib import Path

from shanhai.schema import Project

SKILLS_DIR = Path(__file__).resolve().parents[2] / "assets" / "skills"

# 每次创作都要读的部分(SKILL.md 里的"参考资料索引"表明确标了"每次创作都读")
_ALWAYS = {
    "s1": ["SKILL.md", "references/core-methodology.md"],
    "s2": ["SKILL.md", "references/core-methodology.md",
           "references/shot-design.md", "references/storyboard-format.md"],
}
_SKILL_OF = {"s1": "screenwriting-master", "s2": "director-master"}

# 走 hermes 时用的斜杠命令(它加载自己那份同名 skill)。
# ⚠️ 必须与 hermes 上**当前已装**的名字逐字相同,写错**不报错**:hermes 把不认识的斜杠命令
# 当普通文本吞掉,照常用普通 LLM 回答。2026-08-08 实测发现 s1 原本写的 screenwriter-master
# 早已不匹配(真名 screenwriting-master),编剧大师长期空转。核对办法:
# `python3 scripts/check-hermes-skills.py`(改这里也要改那个脚本的 SLASH_COMMANDS)。
SLASH = {"s1": "/screenwriting-master\n\n", "s2": "/director-master\n\n"}

# 编剧大师的 format 四选一。我们的作品恒为 1/3/5 分钟:
# format-ultrashort = 概念超短片(1~3 分钟),format-short = 叙事短片(5~10 分钟)。
# feature/series 是长片与剧集,我们永远用不到,别拼进去白占上下文。
_FORMAT_BY_MINUTES = {1: "format-ultrashort", 3: "format-ultrashort", 5: "format-short"}

# 导演大师的 genre 按基调选。tone 的取值域见 schema.GenerationParams(温情/奇幻/悬疑),
# 与 genre 文件的覆盖面对应:
#   A-mood  = 日常写实/情绪美学/诗意冥想/悲剧/喜剧/浪漫
#   B-genre = 惊悚/心理惊悚/恐怖/犯罪/悬疑推理/科幻/奇幻魔幻
#   D-theme = 爱情/家庭亲情/青春成长/传记
# 只选一份:多选一份就多几千字,而实测单份已经把 prompt 拉到 hermes 同量级。
_GENRE_BY_TONE = {
    "温情": "genre-A-mood",
    "奇幻": "genre-B-genre",
    "悬疑": "genre-B-genre",
}
_GENRE_FALLBACK = "genre-A-mood"


@lru_cache(maxsize=32)
def _read(skill: str, rel: str) -> str:
    """读一份 skill 文件;缺文件返回空串(降级,不抛)。缓存住——正文几万字,每页重跑都读盘不值当。"""
    p = SKILLS_DIR / skill / rel
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parts_for(stage: str, project: Project) -> list[str]:
    """该环节要拼哪几份。选择完全由作品参数决定,不依赖模型。"""
    parts = list(_ALWAYS[stage])
    if stage == "s1":
        fmt = _FORMAT_BY_MINUTES.get(project.params.duration_min, "format-ultrashort")
        parts.append(f"references/{fmt}.md")
    else:
        genre = _GENRE_BY_TONE.get(project.params.tone, _GENRE_FALLBACK)
        parts.append(f"references/{genre}.md")
    return parts


def build_skill_prompt(stage: str, project: Project) -> str:
    """组装该环节的 skill 前缀。**任何一份必读文件缺失就返回空串**(调用方据此降级为普通生成)。

    为什么缺一份就整体放弃、而不是拼一半:半份 skill 产出什么无人知晓,而"看起来在用大师
    其实是残缺版"比"明确降级为普通"更难排查。降级路径本身是既有行为(见 use_master_skill)。
    """
    skill = _SKILL_OF[stage]
    parts = _parts_for(stage, project)
    texts = [_read(skill, rel) for rel in parts]
    if not all(texts):
        missing = [rel for rel, t in zip(parts, texts) if not t]
        print(f"⚠️ {stage.upper()} 的 skill 正文缺件,降级为普通生成:{skill}/{missing};"
              f"用 `uv run python scripts/fetch-skills.py` 取回")
        return ""
    body = "\n\n".join(texts)
    return (f"以下是你要遵循的创作技能说明(Designed by @山音)。请完全按其中的方法与规范工作。\n\n"
            f"{body}\n\n---\n\n")


def available() -> bool:
    """两个 skill 的正文是否都齐(给启动自检/配置界面用)。"""
    return all(_read(_SKILL_OF[st], rel) for st in _ALWAYS for rel in _ALWAYS[st])
