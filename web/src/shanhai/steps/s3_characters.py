import concurrent.futures as cf
from pathlib import Path

from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.schema import CharacterCard, Project
from shanhai.styles import STYLE_PRESETS

MAX_TURNAROUND = 4
CONCURRENCY = 3  # 各角色并行上限,默认与 S4 同量级;并发度实际由调用方按后端传入(本地串行=1、远程并行)

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


def _process_character(i: int, c: CharacterCard, llm: LLMClient, image: ImageClient,
                       style: str, workdir: Path, char_dir: Path, image_size: str) -> None:
    """单角色:LLM 特征浓缩 + 三视图生图。线程安全——只写各自的 CharacterCard 与各自的
    characters/<name>.png,不共享可变态。生图失败已在此吞掉并退化;LLM 特征失败向上抛(同串行版语义)。"""
    if c.locked and c.turnaround_image and (workdir / c.turnaround_image).exists():
        return                                        # 已定稿角色不重绘(续跑幂等)
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


def run(project: Project, llm: LLMClient, image: ImageClient,
        workdir: Path, image_size: str, concurrency: int = CONCURRENCY) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    style = STYLE_PRESETS[project.style_preset]
    char_dir = workdir / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    # PERF:各角色并行(仿 S4)。并发度由调用方按图像后端传入(本地单 GPU 串行=1、远程并行)。
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(_process_character, i, c, llm, image, style, workdir, char_dir,
                             image_size)
                   for i, c in enumerate(project.script.characters)]
        for f in cf.as_completed(futures):
            f.result()   # 传播非预期错误(生图失败已在 _process_character 内吞掉并退化)
    # 诚实状态:所有需绘三视图的角色(前 MAX_TURNAROUND 个)都成功产出并锁定才算 done;
    # 任一失败(未锁定、无三视图)则 partial。MAX_TURNAROUND 之外的次要角色本不绘三视图,不参与判定。
    project.status["s3"] = "done" if all(
        c.locked or i >= MAX_TURNAROUND
        for i, c in enumerate(project.script.characters)) else "partial"
    return project
