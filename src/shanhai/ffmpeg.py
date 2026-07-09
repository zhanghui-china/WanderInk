import subprocess
from pathlib import Path

FPS = 25
BUFFER_MS = 500  # 每页时长 = 解说音频 + 0.5s(PRD F6)
XFADE_S = 0.5    # 页间交叉溶解时长,落在每页尾部 0.5s 缓冲静音处使解说不重叠
FADE_S = 0.5     # 全片开合:首段从黑淡入、末段淡出到黑(xfade 只做页间过渡,不含此)
ZOOM_MAX = 1.08  # Ken Burns 推拉幅度:zoom 在 1 与 1.08 之间缓慢变化


def sh(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", "replace").strip() if e.stderr else ""
        raise RuntimeError(f"ffmpeg 失败({' '.join(cmd[:3])}…):{stderr}") from e


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
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    # 强制 44.1kHz/立体声,与片头/片尾静音分支(anullsrc=r=44100:cl=stereo)对齐,
    # 否则解说 mp3(常见 24kHz/mono)会让后续 acrossfade 拿到参数不一致的音频流而错乱
    cmd += ["-t", f"{dur:g}", "-filter_complex", fc, "-map", "[v]", "-map", "2:a",
            "-r", str(FPS), "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2", str(out)]
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
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-t", f"{dur:g}", "-vf", vf, "-r", str(FPS),
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2", str(out)]
    return cmd


def silent_audio_cmd(duration_ms: int, out: Path) -> list[str]:
    # TTS 不可用时的静音兜底音轨,44.1kHz/立体声与其它音频分支对齐
    dur = max(duration_ms, 1) / 1000
    return ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", f"{dur:g}", "-c:a", "libmp3lame", "-q:a", "9",
            "-ar", "44100", "-ac", "2", str(out)]


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
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(out)]
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
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(out)]


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
