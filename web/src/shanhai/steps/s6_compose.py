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


def _subtitle_langs(project: Project, lang: str = DEFAULT_LANG) -> list[str]:
    """成片要内嵌的字幕语种:主语言恒有,附加语种只要有任意一页译文就算数。

    **本轮语种排在最前**:播放器不认 disposition 时一律选第一条轨,英文版若把中文排在
    前面就会弹中文字幕——用户的观感就是"没有英文字幕"(本次反馈的成因之一)。"""
    langs = [DEFAULT_LANG]
    extra = {lg for cell in project.storyboard for lg, tr in cell.tracks.items()
             if tr.caption.strip()}
    langs += sorted(extra)
    return [lang] + [x for x in langs if x != lang] if lang in langs else langs


def _timeline(project: Project, workdir: Path,
              lang: str = DEFAULT_LANG) -> tuple[list[StoryboardCell], list[float]]:
    """某语种成片的"入选页 + 逐段时长(含片头/片尾)",纯函数、不编码。

    时长用的是 `_encode_page_clip` 返回值那**同一个** clip_duration_s,所以这份预测
    与真实编码结果必然一致——字幕时间轴要能脱离编码流程独立算出来,否则就只能沿用
    "本轮编码顺手产生的 durations",而那正是跨语种串轨的病根(见 _write_subtitles)。"""
    cells = _content_cells(project, workdir, lang)
    durations = [ffmpeg.clip_duration_s(TITLE_MS, has_audio=False)]
    durations += [ffmpeg.clip_duration_s(track_of(cell, lang).duration_ms, has_audio=True)
                  for cell in cells]
    durations.append(ffmpeg.clip_duration_s(CREDITS_MS, has_audio=False))
    return cells, durations


def _write_subtitles(project: Project, workdir: Path,
                     out_dir: Path, lang: str = DEFAULT_LANG) -> list[tuple[Path, str]]:
    """为每个可用语种各写一份 SRT 与 VTT,返回 [(srt 路径, ISO639-2 标签)]。
    时间轴按 clip 起点算——cells[i] 对应 clips[i+1](clips[0] 是片头卡)。

    两种格式各有其用、缺一不可:SRT 喂给 ffmpeg 封成 mov_text 内嵌轨(下载后用 VLC/
    景区播放设备看);VTT 给网页——浏览器**不解析 MP4 内的字幕轨**,只认 <track> 外挂 VTT。

    ⚠️ **字幕属于「成片」,不属于「语种」**——这是本次两个 bug 的共同根。一条成片里每页画面
    停留多久,只由**这条成片的语种**(lang)的配音时长决定;塞进它的所有语种轨都必须用
    **同一条**时间轴,只有文本按 sub_lang 取。两个曾经踩过的反面:

    - 原实现让 `starts` 走本轮 lang、`span` 却取 sub_lang 的 `duration_ms`,一条 cue 的
      起点和长度来自两个语种;又因为文件名不带 suffix,英文那轮把 `final.zh.srt` 按英文
      时长原地覆盖,中文字幕偏差随页数累积到二十多秒(用户报的正是这个)。
    - 第一版修复矫枉过正:让每个 sub_lang 各按**自己那条成片**算时间轴。中文字幕是对了,
      但中文成片里的英文轨变成了英文成片的时间轴——末页 cue 起点 90.83s 超出中文成片
      77s 的总长,永远不显示;英文成片里的中文轨则在 67s 就播完。症状镜像,同样是错的。

    所以:时间轴取 `_timeline(lang)` 一次,文件名带 `suffix` 按成片区分。主片 suffix 为空,
    文件名与历史一致(`final.zh.srt`),`api._remux_main_subtitles` 与 `_serialize` 不受影响。
    """
    suffix = "" if lang == DEFAULT_LANG else f".{lang}"
    cells, durations = _timeline(project, workdir, lang)
    starts = subtitles.clip_start_times(durations)
    written: list[tuple[Path, str]] = []
    for sub_lang in _subtitle_langs(project, lang):
        cues: list[subtitles.Cue] = []
        for i, cell in enumerate(cells):
            track = track_of(cell, sub_lang)
            if not track.caption.strip():
                continue   # 该页没有这个语种的译文:跳过它一条 cue,不影响其余页的索引
            # 字幕跟着这一页的**画面**走,扣掉 page_clip 尾部那 0.5s 缓冲。
            # 这一步同时守住页间不变量:相邻页起点间隔恰为 durations[i+1] - BUFFER,
            # 即下一页首条 cue 的 start 精确等于本页末条 cue 的 end,零重叠零空隙。
            # 注意判据必须是画面时长而**不是** track.duration_ms —— 后者是该语种自己配音
            # 的长度,在跨语种轨上与本片画面无关(那正是上面说的第一个反面)。
            span = durations[i + 1] - ffmpeg.BUFFER_MS / 1000
            # 整段解说切成若干条按口播时间推进,不再一次性糊满屏幕
            cues.extend(subtitles.spread(subtitles.split_caption(track.caption, sub_lang),
                                         starts[i + 1], span))
        if not cues:
            continue
        path = out_dir / f"final{suffix}.{sub_lang}.srt"
        subtitles.build_srt(cues, path)
        subtitles.build_vtt(cues, out_dir / f"final{suffix}.{sub_lang}.vtt")
        written.append((path, SUB_LANG_TAGS.get(sub_lang, sub_lang)))
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
    # 字幕不吃这份 durations:那是本轮 lang 的画面时长,每个语种要按自己的时间轴算
    subs = _write_subtitles(project, workdir, out_dir, lang)
    if subs:
        # 先做 BGM/响度,再单独一趟 copy 封字幕轨(见 ffmpeg.mux_subtitles_cmd 的取舍说明)
        staged = out_dir / f"final{suffix}.nosub.mp4"
        ffmpeg.sh(ffmpeg.finalize_cmd(merged, bgm, staged))
        # 本轮语种的那条轨置默认:英文版就该默认显示英文字幕
        ffmpeg.sh(ffmpeg.mux_subtitles_cmd(staged, subs, final,
                                           default_lang=SUB_LANG_TAGS.get(lang, lang)))
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
