import concurrent.futures as cf
from collections.abc import Callable
from pathlib import Path

from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.schema import CharacterCard, Project
from shanhai.styles import STYLE_PRESETS

MAX_TURNAROUND = 4
# 突破 MAX_TURNAROUND 是用户的决定(传了参考图的角色都该出图),但不能无上界——
# 一次传几十个角色的参考图会把一轮 S3 撑成几十次生图请求。12 是"4 个默认主角 +
# 8 个额外传图角色"的量级预留,纯拍脑袋但足够覆盖常见剧本规模。
MAX_TURNAROUND_TOTAL = 12
CONCURRENCY = 3  # 各角色并行上限,默认与 S4 同量级;并发度实际由调用方按后端传入(本地串行=1、远程并行)

TURNAROUND_TMPL = (
    "{style}。角色三视图设定图:同一角色的正面、侧面、背面全身像并排排列,"
    "纯白背景,画面中不要出现任何文字。角色:{feature}"
)

# 有参考图时走单图编辑(而非文生图),故不再提 {style}——"参考图"三个字在文生图
# prompt 里毫无所指只会污染语义,这版是 DGX 实测更稳的措辞。副作用:该角色实际
# 生图请求会带 references,shim 据此切到 edit 工作流,LoRA 节点在这条工作流才生效
# (文生图工作流没有 LoRA 节点)——同一项目内有/无参考图的角色可能因此画风分裂,
# 目前无解,只能靠这条注释提醒排查时留意。
TURNAROUND_REF_TMPL = (
    "以参考图中的角色为准,保持其面部、发型、服饰与配色一致,重新绘制该角色的三视图"
    "设定图:左侧正面、中间侧面、右侧背面,三个全身像并排排列,全身入镜不要裁切,"
    "纯白背景,画面中不要出现任何文字。角色补充特征:{feature}"
)

FEATURE_SYSTEM = (
    "把角色信息浓缩为一段可直接用于图像生成 prompt 的中文外貌描述片段。"
    "先判断角色是人类还是非人类(动物、神兽、精怪、器物等):"
    "若是人类,包含性别年龄、发型发色、服饰与颜色、标志性道具;"
    "若是非人类,必须先明确写出其物种或形体(如「一只丹顶鹤」「一头麒麟」),"
    "再描述体型、体表覆盖物(羽毛/鳞片/毛发等)与颜色、标志性特征或道具,"
    "不要套用人类的发型/服饰措辞。"
    "人类还是非人类只依据身份与外貌判断,不得从角色名推断物种或形体。"
    "只输出这一段描述。"
)


def _resolve_ref(c: CharacterCard, workdir: Path) -> Path | None:
    """参考图路径落地校验:字段为空或指向的文件已被外部删除,一律静默退回
    「无参考图」,不让 S3 因此炸掉。"""
    ref = (workdir / c.reference_image) if c.reference_image else None
    if ref is not None and not ref.exists():
        ref = None
    return ref


def _already_done(c: CharacterCard, workdir: Path) -> bool:
    """该角色的三视图已定稿、文件也还在——本轮会被幂等跳过,一次生图请求都不会发。
    判据必须与 _process_character 的提前返回**逐字一致**,否则预算会算错(见 _draw_flags)。"""
    return bool(c.locked and c.turnaround_image and (workdir / c.turnaround_image).exists())


def _draw_flags(characters: list[CharacterCard], workdir: Path) -> list[bool]:
    """决定每个角色本轮该不该画三视图,供生成循环与 status 判定共用同一份判据
    (两处各算一遍必然迟早口径漂移)。规则:前 MAX_TURNAROUND 个默认画;
    传了(有效)参考图的角色不论 index 也画;候选总数硬顶 MAX_TURNAROUND_TOTAL。

    ⚠️ 预算只扣「本轮真的会发生图请求」的角色。此前的版本按候选数扣,而已定稿的角色
    在 _process_character 里会直接 return、一次请求都不发,却照样占掉一个名额——结果是
    前 4 个主角早已定稿的项目里,用户给第 5~16 个角色传了参考图,名额被那 4 个白占,
    排在后面的几个**永远画不出来**,而 status 还是 done、界面上毫无异常,重跑多少次都
    一样(_draw_flags 是纯函数)。硬顶的本意是"限制一轮的生图请求数",这里对齐它的本意。"""
    flags = []
    budget = MAX_TURNAROUND_TOTAL
    for i, c in enumerate(characters):
        candidate = i < MAX_TURNAROUND or _resolve_ref(c, workdir) is not None
        if not candidate:
            flags.append(False)
            continue
        if _already_done(c, workdir):
            flags.append(True)      # 仍算"该画",这样 status 判定认它;但不扣预算
            continue
        draw = budget > 0
        budget -= 1 if draw else 0
        flags.append(draw)
    return flags


def turnaround_progress(project: Project, workdir: Path) -> tuple[int, int]:
    """(已出三视图数, 本轮该出的总数),供前端显示 S3 的实时进度。

    分母**不是**角色总数:只有前 MAX_TURNAROUND 个主角、以及传了参考图的角色才会画,
    还有 MAX_TURNAROUND_TOTAL 的硬顶。拿总数当分母会永远停在 4/8 那样卡住不动。
    刻意复用 _draw_flags 而不是在 api 层另算一遍——同一个判断写两份必然漂移,
    这正是 _INVALIDATES 那次的教训。"""
    if project.script is None:
        return 0, 0
    chars = project.script.characters
    flags = _draw_flags(chars, workdir)
    total = sum(flags)
    done = sum(1 for c, f in zip(chars, flags) if f and c.turnaround_image)
    return done, total


def _process_character(c: CharacterCard, llm: LLMClient, image: ImageClient,
                       style: str, workdir: Path, char_dir: Path, image_size: str,
                       should_draw: bool) -> None:
    """单角色:LLM 特征浓缩 + 三视图生图。线程安全——只写各自的 CharacterCard 与各自的
    characters/<name>.png,不共享可变态。生图失败已在此吞掉并退化;LLM 特征失败向上抛(同串行版语义)。"""
    if _already_done(c, workdir):
        return                                        # 已定稿角色不重绘(续跑幂等)
    # 刻意不传姓名:名字对外貌描述零信息量,却会带偏物种判断(「小虎」被写成一只幼虎,
    # 画面里就真出现老虎)。落盘文件名仍用 c.name,那不进 prompt。
    c.feature_prompt = llm.chat(
        FEATURE_SYSTEM, f"身份:{c.role}\n性格:{c.personality}\n外貌:{c.appearance}")
    if should_draw:
        ref = _resolve_ref(c, workdir)
        out = char_dir / f"{c.name}.png"
        try:
            if ref is not None:
                # 参考图编辑路径是新启用的、走另一套带 LoRA 的 ComfyUI 工作流,
                # 新失败模式比文生图更多;用户明确传了文件、注意力就在这个角色上,
                # 静默退成纯文字是最差的结果,故先退一步试文生图兜底,而不是直接降级。
                # S3 本来没有重试循环(不同于 S4 的 MAX_ATTEMPTS),这里是唯一新增的
                # 一次,上限就是"1 次编辑 + 1 次文生图",不要顺手给 S3 加通用重试。
                try:
                    out.write_bytes(image.generate(
                        TURNAROUND_REF_TMPL.format(feature=c.feature_prompt),
                        size=image_size, references=[ref]))
                except Exception as e:  # noqa: BLE001 参考图编辑失败,退回文生图再试一次
                    print(f"角色「{c.name}」参考图三视图编辑失败,已回退文生图重试:{e}")
                    out.write_bytes(image.generate(
                        TURNAROUND_TMPL.format(style=style, feature=c.feature_prompt),
                        size=image_size))
            else:
                out.write_bytes(image.generate(
                    TURNAROUND_TMPL.format(style=style, feature=c.feature_prompt), size=image_size))
            c.turnaround_image = str(out.relative_to(workdir))
            c.locked = True
        except Exception as e:  # noqa: BLE001 单角色三视图失败不拖垮整轮(同 S4 单页失败模式);
            # 清掉可能残留的旧三视图并解锁,不保留旧图冒充成功(否则重跑时旧图会掩盖本次失败);
            # 但 reference_image 必须保留——用户的上传不能因为一次生成失败就丢,留着下次重跑还能再试。
            # 该角色退化为仅文字特征约束,与 MAX_TURNAROUND 之外的次要角色同等对待
            c.turnaround_image = ""
            c.locked = False
            print(f"角色「{c.name}」三视图生成失败(参考图与文生图两条路径均失败),退化为纯文字特征:{e}")


def run(project: Project, llm: LLMClient, image: ImageClient,
        workdir: Path, image_size: str, concurrency: int = CONCURRENCY,
        on_progress: Callable[[], None] | None = None,
        cancel_check: Callable[[], bool] | None = None) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    style = STYLE_PRESETS[project.style_preset]
    char_dir = workdir / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    characters = project.script.characters
    flags = _draw_flags(characters, workdir)
    # PERF:各角色并行(仿 S4)。并发度由调用方按图像后端传入(本地单 GPU 串行=1、远程并行)。
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(_process_character, c, llm, image, style, workdir, char_dir,
                             image_size, flags[i])
                   for i, c in enumerate(characters)]
        for f in cf.as_completed(futures):
            if cancel_check and cancel_check():
                for pending in futures:
                    pending.cancel()  # 已开始的取消不了(Python 线程池物理限制),但能拦掉还没排上的
                break
            f.result()   # 传播非预期错误(生图失败已在 _process_character 内吞掉并退化)
            # 每完成一个角色就落盘一次(同 s4_pages 的做法)。此前 S3 全程一次不存,
            # 角色卡在界面上始终是空的,直到本阶段结束那一刻才整体跳变——而同一刻 S4 就开始了,
            # 观感上就是"三视图还没生成完就开始画漫画页"。用户正是这样报的这个问题。
            if on_progress:
                on_progress()
    # 诚实状态:该画三视图的角色(_draw_flags 判定为 True 的)都成功产出并锁定才算 done;
    # 任一失败(未锁定、无三视图)则 partial。不该画的角色(判据同 _draw_flags)不参与判定。
    project.status["s3"] = "done" if all(
        c.locked or not flags[i]
        for i, c in enumerate(characters)) else "partial"
    return project
