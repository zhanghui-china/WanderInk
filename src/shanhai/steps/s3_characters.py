from pathlib import Path

from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project
from shanhai.styles import STYLE_PRESETS

MAX_TURNAROUND = 4

TURNAROUND_TMPL = (
    "{style}。角色三视图设定图:同一角色的正面、侧面、背面全身像并排排列,"
    "纯白背景,画面中不要出现任何文字。角色:{feature}"
)

FEATURE_SYSTEM = ("把角色信息浓缩为一段可直接用于图像生成 prompt 的中文外貌描述片段,"
                  "包含:性别年龄、发型发色、服饰与颜色、标志性道具。只输出这一段描述。")


def run(project: Project, llm: LLMClient, image: ImageClient,
        workdir: Path, image_size: str) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    style = STYLE_PRESETS[project.style_preset]
    char_dir = workdir / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(project.script.characters):
        c.feature_prompt = llm.chat(
            FEATURE_SYSTEM, f"姓名:{c.name}\n身份:{c.role}\n性格:{c.personality}\n外貌:{c.appearance}")
        if i < MAX_TURNAROUND:
            out = char_dir / f"{c.name}.png"
            out.write_bytes(image.generate(
                TURNAROUND_TMPL.format(style=style, feature=c.feature_prompt), size=image_size))
            c.turnaround_image = str(out.relative_to(workdir))
            c.locked = True
    project.status["s3"] = "done"
    return project
