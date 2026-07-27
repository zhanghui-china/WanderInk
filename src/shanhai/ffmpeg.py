import subprocess
from pathlib import Path

FPS = 25
BUFFER_MS = 500  # 每页时长 = 解说音频 + 0.5s(PRD F6)
XFADE_S = 0.5    # 页间交叉溶解时长,落在每页尾部 0.5s 缓冲静音处使解说不重叠
FADE_S = 0.5     # 全片开合:首段从黑淡入、末段淡出到黑(xfade 只做页间过渡,不含此)
ZOOM_MAX = 1.08  # Ken Burns 推拉幅度:zoom 在 1 与 1.08 之间缓慢变化

# 所有音频分支统一为 44.1kHz/立体声:解说 mp3 常见 24kHz/mono,不统一会让 acrossfade/amix
# 拿到参数不一致的流而时长错乱。新增音频分支务必复用以下常量,勿再手写数字。
AUDIO_RATE = 44100
AUDIO_CH = 2
_ANULLSRC = f"anullsrc=r={AUDIO_RATE}:cl=stereo"
_AR_AC = ["-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CH)]


def sh(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", "replace").strip() if e.stderr else ""
        raise RuntimeError(f"ffmpeg 失败({' '.join(cmd[:3])}…):{stderr}") from e


# 音色克隆参考音频的规格:16kHz 单声道 pcm_s16le。
# ⚠️ 刻意**不**复用上面的 AUDIO_RATE/AUDIO_CH——那条"所有音频分支统一 44.1kHz/立体声"的约束
# 是为了成片音轨的 acrossfade/amix 不错乱,而参考音频**不进成片**,它只是喂给 TTS 后端的输入;
# 而声纹提取与 Whisper 转写本来就工作在 16k 单声道上,升到 44.1k 立体声纯属浪费且无益。
VOICE_SAMPLE_RATE = 16000
VOICE_SAMPLE_MAX_S = 20     # 录音上限,与前端一致;这里是硬截断,不能只信前端


def voice_sample_cmd(src: Path, out: Path, in_fmt: str,
                     max_s: float = VOICE_SAMPLE_MAX_S) -> list[str]:
    """把上传的录音转成规范的参考音频 wav。这一步同时是**安全净化**:
    等同于图片路径的重编码——干掉伪装成音频的 polyglot 字节与容器里的任意元数据。

    `-f {in_fmt}` 显式指定输入 demuxer 而不是让 ffmpeg 自动探测:Pillow 是个纯解码器,
    而 ffmpeg 是一大堆解析器的集合,把用户字节直接丢给它自动探测,攻击面比图片那条路大得多,
    必须按前端声明的少数几种格式收窄。
    `-t` 在**输入之后**,是对输出的硬截断:超长录音直接切掉,不能只信前端的计时。"""
    return ["ffmpeg", "-y", "-f", in_fmt, "-i", str(src), "-t", f"{max_s:g}",
            "-ar", str(VOICE_SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out)]


def probe_duration_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    if not out or out == "N/A":
        raise ValueError(f"ffprobe 无法解析时长(输出为 {out!r}):{path}")
    return int(float(out) * 1000)


def clip_duration_s(duration_ms: int, has_audio: bool) -> float:
    # 有解说才补 0.5s 尾缓冲(免末字被过渡吃掉);静帧片头/片尾用原时长
    total_ms = duration_ms + BUFFER_MS if has_audio else duration_ms
    return total_ms / 1000


def _kenburns_vf(dur: float, zoom_in: bool) -> str:
    # 输入图已 1920×1080 满幅;先放大 2× 再 zoompan 裁切下采样,推拉更平滑不发虚。
    # 用输出帧号 on 线性驱动 zoom(d=1 → 每输入帧出 1 帧),奇偶页交替推近/拉远。
    span = ZOOM_MAX - 1
    frames = max(round(dur * FPS) - 1, 1)
    if zoom_in:
        z = f"min(1+{span:g}*on/{frames},{ZOOM_MAX:g})"
    else:
        z = f"max({ZOOM_MAX:g}-{span:g}*on/{frames},1)"
    return (f"scale=3840:2160,"
            f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"fps={FPS}:s=1920x1080,format=yuv420p")


def page_clip_cmd(image: Path, overlay: Path, audio: Path | None, duration_ms: int,
                  out: Path, zoom_in: bool = True) -> list[str]:
    # 底图 image 走 Ken Burns 推拉;overlay(透明 PNG 字幕/水印)作为独立静态输入
    # 在 zoompan 之后叠加,故字幕/水印保持不动,只有底图运动。
    dur = clip_duration_s(duration_ms, audio is not None)
    kb = _kenburns_vf(dur, zoom_in)
    fc = f"[0:v]{kb}[bg];[bg][1:v]overlay=0:0[v]"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image),
           "-loop", "1", "-i", str(overlay)]
    if audio:
        cmd += ["-i", str(audio), "-af", "apad"]
    else:
        cmd += ["-f", "lavfi", "-i", _ANULLSRC]
    # 强制 44.1kHz/立体声,与片头/片尾静音分支(anullsrc=r=44100:cl=stereo)对齐,
    # 否则解说 mp3(常见 24kHz/mono)会让后续 acrossfade 拿到参数不一致的音频流而错乱
    # 中间 clip 用 ultrafast:后面必被 xfade_concat 整片重编码一次,此处高 preset 白费时间且多一代
    # 有损;最终成片编码(xfade_concat/finalize)仍用默认 preset,画质不降。
    cmd += ["-t", f"{dur:g}", "-filter_complex", fc, "-map", "[v]", "-map", "2:a",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k",
            *_AR_AC, str(out)]
    return cmd


def still_clip_cmd(image: Path, audio: Path | None, duration_ms: int,
                   out: Path) -> list[str]:
    # 静帧片头/片尾卡:无 zoompan、无 overlay,烘焙好的文字不漂移。输出格式与页 clip 一致。
    dur = clip_duration_s(duration_ms, audio is not None)
    vf = f"scale=1920:1080,fps={FPS},format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image)]
    if audio:
        cmd += ["-i", str(audio), "-af", "apad"]
    else:
        cmd += ["-f", "lavfi", "-i", _ANULLSRC]
    # 中间 clip 用 ultrafast(同 page_clip_cmd):后面会被 xfade_concat 整片重编码,不必在此追求画质。
    cmd += ["-t", f"{dur:g}", "-vf", vf, "-r", str(FPS),
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k",
            *_AR_AC, str(out)]
    return cmd


def silent_audio_cmd(duration_ms: int, out: Path) -> list[str]:
    # TTS 不可用时的静音兜底音轨,44.1kHz/立体声与其它音频分支对齐
    dur = max(duration_ms, 1) / 1000
    return ["ffmpeg", "-y", "-f", "lavfi", "-i", _ANULLSRC,
            "-t", f"{dur:g}", "-c:a", "libmp3lame", "-q:a", "9",
            *_AR_AC, str(out)]


def concat_audio_cmd(parts: list[Path], list_file: Path, out: Path) -> list[str]:
    # 拼接分句合成的 mp3 为整页音轨。重编码(非 -c copy)避免各句 mp3 参数不一致导致
    # 时长/拼接错乱,统一 44.1kHz/立体声与其它音频分支对齐。
    # 调用方需先把 list_file 写成 concat demuxer 格式:每行 file '<绝对路径>'。
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:a", "libmp3lame", "-q:a", "2",
            *_AR_AC, str(out)]


SILENCE_THRESH_DB = "-45dB"  # 低于此视为静音(保守,不切软起音)
SILENCE_LEAD_S = 0.05        # 保护性 lead-in/tail-out,免切掉爆破/齿音


def trim_silence_cmd(src: Path, out: Path, pad_s: float = 0.18) -> list[str]:
    # 修剪每句首尾多余静音(reverse 惯用法两端剪)+ 尾部补固定微停顿,收紧句间节奏。
    # detection=peak 按可听样本判断,起音处停剪不吃字。输出统一 44.1kHz/立体声。
    leg = (f"silenceremove=start_periods=1:start_silence={SILENCE_LEAD_S:g}:"
           f"start_threshold={SILENCE_THRESH_DB}:detection=peak")
    af = f"{leg},areverse,{leg},areverse,apad=pad_dur={pad_s:g}"
    return ["ffmpeg", "-y", "-i", str(src), "-af", af,
            "-c:a", "libmp3lame", "-q:a", "2", *_AR_AC, str(out)]


def xfade_offsets(durations_s: list[float], t: float) -> list[float]:
    # 第 k 段过渡(0-based)起点 offset_k = Σ_{i≤k} d_i − (k+1)·T:
    # xfade 把前段视频尾部与下段头部重叠 T,累积时长每次减 T,故偏移随之累加。
    offsets, acc = [], 0.0
    for k in range(1, len(durations_s)):
        acc += durations_s[k - 1]
        offsets.append(acc - k * t)
    return offsets


def xfade_concat_cmd(clips: list[Path], durations_s: list[float], out: Path,
                     t: float = XFADE_S) -> list[str]:
    # 一条 filter_complex:视频 xfade 溶解、音频 acrossfade 交叉淡接(narration 不交叠),
    # 片头卡与片尾卡纳入同一溶解链。durations_s 与 clips 一一对应(clip_duration_s 算出)。
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    if len(clips) == 1:
        return ["ffmpeg", "-y", *inputs, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", *_AR_AC, str(out)]
    n = len(clips)
    offsets = xfade_offsets(durations_s, t)
    # 逐输入规整时基/帧率/像素格式,避免 xfade 因时基不一致而冻帧;
    # 首段补从黑淡入、末段补淡出到黑,使全片开合平滑不硬切(xfade 只做页间过渡)
    parts = []
    for k in range(n):
        vf = f"[{k}:v]settb=AVTB,fps={FPS},format=yuv420p"
        if k == 0:
            vf += f",fade=t=in:st=0:d={FADE_S:g}"
        if k == n - 1:
            vf += f",fade=t=out:st={durations_s[k] - FADE_S:g}:d={FADE_S:g}"
        parts.append(f"{vf}[v{k}]")
    prev = "[v0]"
    for k in range(1, n):
        label = "[vout]" if k == n - 1 else f"[vx{k}]"
        parts.append(f"{prev}[v{k}]xfade=transition=fade:duration={t:g}:"
                     f"offset={offsets[k - 1]:.3f}{label}")
        prev = label
    prev = "[0:a]"
    for k in range(1, n):
        label = "[aout]" if k == n - 1 else f"[ax{k}]"
        parts.append(f"{prev}[{k}:a]acrossfade=d={t:g}{label}")
        prev = label
    return ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", *_AR_AC, str(out)]


def finalize_cmd(video: Path, bgm: Path | None, out: Path) -> list[str]:
    loudnorm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    if bgm:
        fc = (f"[1:a]volume=0.18[bg];[0:a][bg]amix=inputs=2:duration=first[mix];"
              f"[mix]{loudnorm}[aout]")
        return ["ffmpeg", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    return ["ffmpeg", "-y", "-i", str(video), "-af", loudnorm,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]


def mux_subtitles_cmd(video: Path, subs: list[tuple[Path, str]], out: Path) -> list[str]:
    """把若干 SRT 作为软字幕轨封进 MP4。subs 是 (srt 路径, ISO 639-2 语种码) 列表,
    如 [(zh.srt, "zho"), (en.srt, "eng")]。

    独立一趟做,不并进 finalize_cmd:后者带 -stream_loop -1 的 BGM 输入和 -shortest,
    再塞进稀疏的字幕流容易让 -shortest 按字幕结束时刻截断整片。这里音视频都是 copy,
    没有重编码开销,代价可以忽略。"""
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    for path, _lang in subs:
        cmd += ["-i", str(path)]
    cmd += ["-map", "0:v", "-map", "0:a"]
    for i in range(len(subs)):
        cmd += ["-map", str(i + 1)]
    # mov_text 是 MP4 容器唯一广泛支持的字幕编码;srt 原样封装播放器多半不认。
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
    for i, (_path, lang) in enumerate(subs):
        cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
    cmd.append(str(out))
    return cmd
