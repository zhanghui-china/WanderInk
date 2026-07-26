from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shanhai import export, ffmpeg, subtitles, typeset
from shanhai.schema import Legend, LocalizedTrack, Project, StoryboardCell
from shanhai.steps.s5_audio import DEFAULT_LANG, track_of

TITLE_MS = 2500
CREDITS_MS = 3000
S6_CONCURRENCY = 3  # 逐页 clip 编码并发上限,与 S4/S5 同量级

# 语种码 -> ffmpeg 字幕轨的 ISO 639-2 语言标签
SUB_LANG_TAGS = {"zh": "zho", "en": "eng"}

# 片尾来源标注的各语种模板。加一门语言在这里加一组即可。
CREDITS_TEXT = {
    "zh": {"original": "本故事为原创演绎", "material": "素材来源:{s}",
           "legend": "传说来源:{s}", "adapted": "改编自{t}",
           "unknown": "来源:未标注", "ai": "本片为 AI 生成内容"},
    "en": {"original": "An original dramatization", "material": "Material source: {s}",
           "legend": "Legend source: {s}", "adapted": "Adapted from {t}",
           "unknown": "Source: not specified", "ai": "AI-generated content"},
}


def _content_cells(project: Project, workdir: Path,
                   lang: str = DEFAULT_LANG) -> list[StoryboardCell]:
    """入选内容页:确认且图/音齐备、产物文件确实存在的页。跳过的页打印原因。
    音频按语种取——英文版看 tracks["en"].audio,主语言看 cell.audio。"""
    cells: list[StoryboardCell] = []
    for cell in project.storyboard:
        track = track_of(cell, lang)
        if cell.status != "confirmed" or not (cell.image and track.audio):
            print(f"跳过第 {cell.index} 页({lang},status={cell.status})")
            continue
        if not (workdir / cell.image).exists() or not (workdir / track.audio).exists():
            print(f"跳过第 {cell.index} 页({lang},产物缺失)")
            continue
        cells.append(cell)
    return cells


def _credits_lines(legend: Legend | None, lang: str = DEFAULT_LANG) -> list[str]:
    """片尾来源标注(PRD F0②/§9.4):原创演绎显式标注,不冠"传说来源";始终至少一行来源。"""
    t = CREDITS_TEXT.get(lang, CREDITS_TEXT[DEFAULT_LANG])
    source_type = legend.source_type if legend else None
    sources = legend.sources if legend else []
    if source_type == "原创演绎":
        lines = [t["original"]] + [t["material"].format(s=s) for s in sources]
    elif sources:
        lines = [t["legend"].format(s=s) for s in sources]
    elif source_type:
        lines = [t["adapted"].format(t=source_type)]
    else:
        lines = [t["unknown"]]
    return lines + [t["ai"]]


def _encode_page_clip(cell: StoryboardCell, track: StoryboardCell | LocalizedTrack,
                      zoom_in: bool, workdir: Path, clips_dir: Path,
                      overlay: Path, suffix: str) -> tuple[Path, float]:
    """编码单页 clip,返回 (clip 路径, 时长秒)。线程安全:各页只写各自的 NN.mp4,
    互不冲突(overlay 是全片共用的只读文件)。编码异常直接向上抛(与既有语义一致:
    S6 编码失败即 pipeline error,不吞)。"""
    img, aud = workdir / cell.image, workdir / track.audio
    clip = clips_dir / f"{cell.index:02d}{suffix}.mp4"
    # page_clip_cmd 内部补 0.5s 尾缓冲,此处传原始解说时长;奇偶页交替推近/拉远
    ffmpeg.sh(ffmpeg.page_clip_cmd(img, overlay, aud, track.duration_ms, clip, zoom_in=zoom_in))
    return clip, ffmpeg.clip_duration_s(track.duration_ms, has_audio=True)


def _subtitle_langs(project: Project) -> list[str]:
    """成片要内嵌的字幕语种:主语言恒有,附加语种只要有任意一页译文就算数。"""
    langs = [DEFAULT_LANG]
    extra = {lg for cell in project.storyboard for lg, tr in cell.tracks.items()
             if tr.caption.strip()}
    return langs + sorted(extra)


def _write_subtitles(project: Project, cells: list[StoryboardCell], durations: list[float],
                     out_dir: Path) -> list[tuple[Path, str]]:
    """为每个可用语种写一份 SRT,返回 [(路径, ISO639-2 标签)]。
    时间轴按 clip 起点算——cells[i] 对应 clips[i+1](clips[0] 是片头卡)。"""
    starts = subtitles.clip_start_times(durations)
    written: list[tuple[Path, str]] = []
    for lang in _subtitle_langs(project):
        cues: list[subtitles.Cue] = []
        for i, cell in enumerate(cells):
            track = track_of(cell, lang)
            if not track.caption.strip():
                continue
            start = starts[i + 1]
            # 字幕跟着这一页的解说走,不占用 page_clip 尾部那 0.5s 缓冲。
            # 该语种没配过音时(duration_ms=0)退而用本页画面时长,免得字幕瞬闪。
            span = (track.duration_ms or round(durations[i + 1] * 1000)) / 1000
            cues.append((start, start + span, track.caption))
        if not cues:
            continue
        path = out_dir / f"final.{lang}.srt"
        subtitles.build_srt(cues, path)
        written.append((path, SUB_LANG_TAGS.get(lang, lang)))
    return written


def run(project: Project, workdir: Path, lang: str = DEFAULT_LANG) -> Project:
    is_main = lang == DEFAULT_LANG
    suffix = "" if is_main else f".{lang}"
    out_dir = workdir / "output"
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir = out_dir / "overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)

    # 先筛入选内容页:0 页则拒绝产出(仅片头+片尾的)空片,让管线记 error 而非伪造成片
    content_cells = _content_cells(project, workdir, lang)
    if not content_cells:
        raise ValueError(f"无可用成图页面({lang}),拒绝产出空片")

    title_png = out_dir / f"title{suffix}.png"
    legend_title = project.legend.title if project.legend else ""
    typeset.title_card(project.scenic_spot, legend_title, title_png)
    credits_png = out_dir / f"credits{suffix}.png"
    typeset.credits_card(_credits_lines(project.legend, lang), credits_png)
    # 字幕改走 MP4 软字幕轨,画面上不再烧文字——传空 caption 让 overlay 只剩右上角水印。
    # 全片共用一张(内容与页码无关),不必逐页生成。
    overlay = overlays_dir / "watermark.png"
    typeset.overlay_layer("", overlay)

    # clips 与 durations 一一对应:durations 供 xfade 累积 offset 计算
    clips: list[Path] = []
    durations: list[float] = []
    head = clips_dir / f"00_title{suffix}.mp4"
    ffmpeg.sh(ffmpeg.still_clip_cmd(title_png, None, TITLE_MS, head))
    clips.append(head)
    durations.append(ffmpeg.clip_duration_s(TITLE_MS, has_audio=False))

    # PERF2:逐页 clip 编码并行(仿 S4/S5)。各页写各自 NN.mp4,互不冲突;
    # 用索引回填而非 as_completed 完成序,确保 clips/durations 顺序与页序严格一致。
    page_clips: list[Path] = [None] * len(content_cells)  # type: ignore[list-item]
    page_durations: list[float] = [None] * len(content_cells)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=S6_CONCURRENCY) as ex:
        futures = [ex.submit(_encode_page_clip, cell, track_of(cell, lang), i % 2 == 0,
                             workdir, clips_dir, overlay, suffix)
                   for i, cell in enumerate(content_cells)]
        for i, f in enumerate(futures):
            page_clips[i], page_durations[i] = f.result()  # 索引回填 + 传播编码异常
    clips.extend(page_clips)
    durations.extend(page_durations)

    tail = clips_dir / f"99_credits{suffix}.mp4"
    ffmpeg.sh(ffmpeg.still_clip_cmd(credits_png, None, CREDITS_MS, tail))
    clips.append(tail)
    durations.append(ffmpeg.clip_duration_s(CREDITS_MS, has_audio=False))

    merged = out_dir / f"merged{suffix}.mp4"
    ffmpeg.sh(ffmpeg.xfade_concat_cmd(clips, durations, merged))
    final = out_dir / f"final{suffix}.mp4"
    bgm = Path(project.bgm) if project.bgm else None
    subs = _write_subtitles(project, content_cells, durations, out_dir)
    if subs:
        # 先做 BGM/响度,再单独一趟 copy 封字幕轨(见 ffmpeg.mux_subtitles_cmd 的取舍说明)
        staged = out_dir / f"final{suffix}.nosub.mp4"
        ffmpeg.sh(ffmpeg.finalize_cmd(merged, bgm, staged))
        ffmpeg.sh(ffmpeg.mux_subtitles_cmd(staged, subs, final))
        staged.unlink(missing_ok=True)
    else:
        ffmpeg.sh(ffmpeg.finalize_cmd(merged, bgm, final))
    project.output["mp4" if is_main else f"mp4_{lang}"] = str(final)
    # 诚实状态:content_cells 只挑 confirmed 且图/音齐备的页,少于总页数说明有页因上游
    # (通常是 S4)失败被跳过——不能无条件标 done,否则这一格看着"完成"会盖过 S4 的 partial。
    done = len(content_cells) == len(project.storyboard)
    project.status["s6" if is_main else f"s6_{lang}"] = "done" if done else "partial"
    if is_main:
        # zip/pdf 只出主语言:纸质连环画没有"软字幕",文字必须烧进画面,而英文烧录要先解决
        # 词级断行/两行截断/布局溢出那一组问题(见计划),本期不做。
        try:
            export.build_exports(project, workdir)
        except Exception as e:  # noqa: BLE001 导出失败不拖垮 s6,mp4 已产出即算成功
            print(f"图文导出(zip/pdf)失败:{e}")
    return project
