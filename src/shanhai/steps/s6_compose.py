from pathlib import Path

from shanhai import ffmpeg, typeset
from shanhai.schema import Project

TITLE_MS = 2500
CREDITS_MS = 3000


def run(project: Project, workdir: Path) -> Project:
    out_dir = workdir / "output"
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    title_png = out_dir / "title.png"
    legend_title = project.legend.title if project.legend else ""
    typeset.title_card(project.scenic_spot, legend_title, title_png)
    sources = project.legend.sources if project.legend else []
    credits_png = out_dir / "credits.png"
    typeset.credits_card([f"传说来源:{s}" for s in sources] + ["本片为 AI 生成内容"], credits_png)

    clips: list[Path] = []
    head = clips_dir / "00_title.mp4"
    ffmpeg.sh(ffmpeg.page_clip_cmd(title_png, None, TITLE_MS, head))
    clips.append(head)
    for cell in project.storyboard:
        if cell.status != "confirmed" or not (cell.image and cell.audio):
            print(f"跳过第 {cell.index} 页(status={cell.status})")
            continue
        clip = clips_dir / f"{cell.index:02d}.mp4"
        # page_clip_cmd 内部补 0.5s 尾缓冲,此处传原始解说时长
        ffmpeg.sh(ffmpeg.page_clip_cmd(workdir / cell.image, workdir / cell.audio,
                                       cell.duration_ms, clip))
        clips.append(clip)
    tail = clips_dir / "99_credits.mp4"
    ffmpeg.sh(ffmpeg.page_clip_cmd(credits_png, None, CREDITS_MS, tail))
    clips.append(tail)

    merged = out_dir / "merged.mp4"
    ffmpeg.sh(ffmpeg.concat_cmd(clips, out_dir / "concat.txt", merged))
    final = out_dir / "final.mp4"
    bgm = Path(project.bgm) if project.bgm else None
    ffmpeg.sh(ffmpeg.finalize_cmd(merged, bgm, final))
    project.output["mp4"] = str(final)
    project.status["s6"] = "done"
    return project
