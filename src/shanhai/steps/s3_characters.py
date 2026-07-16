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

FEATURE_SYSTEM = (
    "把角色信息浓缩为一段可直接用于图像生成 prompt 的中文外貌描述片段。"
    "先判断角色是人类还是非人类(动物、神兽、精怪、器物等):"
    "若是人类,包含性别年龄、发型发色、服饰与颜色、标志性道具;"
    "若是非人类,必须先明确写出其物种或形体(如「一只丹顶鹤」「一头麒麟」),"
    "再描述体型、体表覆盖物(羽毛/鳞片/毛发等)与颜色、标志性特征或道具,"
    "不要套用人类的发型/服饰措辞。只输出这一段描述。"
)


def run(project: Project, llm: LLMClient, image: ImageClient,
        workdir: Path, image_size: str) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    style = STYLE_PRESETS[project.style_preset]
    char_dir = workdir / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(project.script.characters):
        if c.locked and c.turnaround_image and (workdir / c.turnaround_image).exists():
            continue                                  # 已定稿角色不重绘(续跑幂等)
        c.feature_prompt = llm.chat(
            FEATURE_SYSTEM, f"姓名:{c.name}\n身份:{c.role}\n性格:{c.personality}\n外貌:{c.appearance}")
        # 仅前 MAX_TURNAROUND 个角色绘三视图;依赖 S1 已按重要度降序排列 characters,
        # 故前几个即主角。S1 违约(主角排后)则其一致性锚点缺失,见 s1_script SYSTEM 约束。
        if i < MAX_TURNAROUND:
            try:
                out = char_dir / f"{c.name}.png"
                out.write_bytes(image.generate(
                    TURNAROUND_TMPL.format(style=style, feature=c.feature_prompt), size=image_size))
                c.turnaround_image = str(out.relative_to(workdir))
                c.locked = True
            except Exception as e:  # noqa: BLE001 单角色三视图失败不拖垮整轮(同 S4 单页失败模式);
                # 清掉可能残留的旧三视图并解锁,不保留旧图冒充成功(否则重跑时旧图会掩盖本次失败);
                # 该角色退化为仅文字特征约束,与 MAX_TURNAROUND 之外的次要角色同等对待
                c.turnaround_image = ""
                c.locked = False
                print(f"角色「{c.name}」三视图生成失败,退化为纯文字特征:{e}")
    # 诚实状态:所有需绘三视图的角色(前 MAX_TURNAROUND 个)都成功产出并锁定才算 done;
    # 任一失败(未锁定、无三视图)则 partial。MAX_TURNAROUND 之外的次要角色本不绘三视图,不参与判定。
    project.status["s3"] = "done" if all(
        c.locked or i >= MAX_TURNAROUND
        for i, c in enumerate(project.script.characters)) else "partial"
    return project
