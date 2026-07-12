from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shanhai import export, ffmpeg, typeset
from shanhai.schema import Legend, Project, StoryboardCell

TITLE_MS = 2500
CREDITS_MS = 3000
S6_CONCURRENCY = 3  # 逐页 clip 编码并发上限,与 S4/S5 同量级


def _content_cells(project: Project, workdir: Path) -> list[StoryboardCell]:
    """入选内容页:确认且图/音齐备、产物文件确实存在的页。跳过的页打印原因。"""
    cells: list[StoryboardCell] = []
    for cell in project.storyboard:
        if cell.status != "confirmed" or not (cell.image and cell.audio):
            print(f"跳过第 {cell.index} 页(status={cell.status})")
            continue
        if not (workdir / cell.image).exists() or not (workdir / cell.audio).exists():
            print(f"跳过第 {cell.index} 页(产物缺失)")
            continue
        cells.append(cell)
    return cells


def _credits_lines(legend: Legend | None) -> list[str]:
    """片尾来源标注(PRD F0②/§9.4):原创演绎显式标注,不冠"传说来源";始终至少一行来源。"""
    source_type = legend.source_type if legend else None
    sources = legend.sources if legend else []
    if source_type == "原创演绎":
        lines = ["本故事为原创演绎"] + [f"素材来源:{s}" for s in sources]
    elif sources:
        lines = [f"传说来源:{s}" for s in sources]
    elif source_type:
        lines = [f"改编自{source_type}"]
    else:
        lines = ["来源:未标注"]
    return lines + ["本片为 AI 生成内容"]


def _encode_page_clip(cell: StoryboardCell, zoom_in: bool, workdir: Path,
                      clips_dir: Path, overlays_dir: Path) -> tuple[Path, float]:
    """编码单页 clip,返回 (clip 路径, 时长秒)。线程安全:各页只写各自的 NN.mp4/overlay,
    互不冲突。编码异常直接向上抛(与既有语义一致:S6 编码失败即 pipeline error,不吞)。"""
    img, aud = workdir / cell.image, workdir / cell.audio
    clip = clips_dir / f"{cell.index:02d}.mp4"
    overlay = overlays_dir / f"page_{cell.index:02d}.png"
    typeset.overlay_layer(cell.caption, overlay)
    # page_clip_cmd 内部补 0.5s 尾缓冲,此处传原始解说时长;奇偶页交替推近/拉远
    ffmpeg.sh(ffmpeg.page_clip_cmd(img, overlay, aud, cell.duration_ms, clip, zoom_in=zoom_in))
    return clip, ffmpeg.clip_duration_s(cell.duration_ms, has_audio=True)


def run(project: Project, workdir: Path) -> Project:
    out_dir = workdir / "output"
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir = out_dir / "overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)

    # 先筛入选内容页:0 页则拒绝产出(仅片头+片尾的)空片,让管线记 error 而非伪造成片
    content_cells = _content_cells(project, workdir)
    if not content_cells:
        raise ValueError("无可用成图页面,拒绝产出空片")

    title_png = out_dir / "title.png"
    legend_title = project.legend.title if project.legend else ""
    typeset.title_card(project.scenic_spot, legend_title, title_png)
    credits_png = out_dir / "credits.png"
    typeset.credits_card(_credits_lines(project.legend), credits_png)

    # clips 与 durations 一一对应:durations 供 xfade 累积 offset 计算
    clips: list[Path] = []
    durations: list[float] = []
    head = clips_dir / "00_title.mp4"
    ffmpeg.sh(ffmpeg.still_clip_cmd(title_png, None, TITLE_MS, head))
    clips.append(head)
    durations.append(ffmpeg.clip_duration_s(TITLE_MS, has_audio=False))

    # PERF2:逐页 clip 编码并行(仿 S4/S5)。各页写各自 NN.mp4/overlay,互不冲突;
    # 用索引回填而非 as_completed 完成序,确保 clips/durations 顺序与页序严格一致。
    page_clips: list[Path] = [None] * len(content_cells)  # type: ignore[list-item]
    page_durations: list[float] = [None] * len(content_cells)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=S6_CONCURRENCY) as ex:
        futures = [ex.submit(_encode_page_clip, cell, i % 2 == 0, workdir,
                             clips_dir, overlays_dir)
                   for i, cell in enumerate(content_cells)]
        for i, f in enumerate(futures):
            page_clips[i], page_durations[i] = f.result()  # 索引回填 + 传播编码异常
    clips.extend(page_clips)
    durations.extend(page_durations)

    tail = clips_dir / "99_credits.mp4"
    ffmpeg.sh(ffmpeg.still_clip_cmd(credits_png, None, CREDITS_MS, tail))
    clips.append(tail)
    durations.append(ffmpeg.clip_duration_s(CREDITS_MS, has_audio=False))

    merged = out_dir / "merged.mp4"
    ffmpeg.sh(ffmpeg.xfade_concat_cmd(clips, durations, merged))
    final = out_dir / "final.mp4"
    bgm = Path(project.bgm) if project.bgm else None
    ffmpeg.sh(ffmpeg.finalize_cmd(merged, bgm, final))
    project.output["mp4"] = str(final)
    project.status["s6"] = "done"
    try:
        export.build_exports(project, workdir)
    except Exception as e:  # noqa: BLE001 导出失败不拖垮 s6,mp4 已产出即算成功
        print(f"图文导出(zip/pdf)失败:{e}")
    return project
