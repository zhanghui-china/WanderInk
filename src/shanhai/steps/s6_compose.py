from pathlib import Path

from shanhai import export, ffmpeg, typeset
from shanhai.schema import Legend, Project

TITLE_MS = 2500
CREDITS_MS = 3000


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


def run(project: Project, workdir: Path) -> Project:
    out_dir = workdir / "output"
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir = out_dir / "overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)

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
    page_i = 0
    for cell in project.storyboard:
        if cell.status != "confirmed" or not (cell.image and cell.audio):
            print(f"跳过第 {cell.index} 页(status={cell.status})")
            continue
        img, aud = workdir / cell.image, workdir / cell.audio
        if not img.exists() or not aud.exists():
            print(f"跳过第 {cell.index} 页(产物缺失)")
            continue
        clip = clips_dir / f"{cell.index:02d}.mp4"
        overlay = overlays_dir / f"page_{cell.index:02d}.png"
        typeset.overlay_layer(cell.caption, overlay)
        # page_clip_cmd 内部补 0.5s 尾缓冲,此处传原始解说时长;奇偶页交替推近/拉远
        ffmpeg.sh(ffmpeg.page_clip_cmd(img, overlay, aud, cell.duration_ms, clip,
                                       zoom_in=page_i % 2 == 0))
        clips.append(clip)
        durations.append(ffmpeg.clip_duration_s(cell.duration_ms, has_audio=True))
        page_i += 1
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
