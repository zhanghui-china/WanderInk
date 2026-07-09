import subprocess
from pathlib import Path

FPS = 25
FADE = 0.25
BUFFER_MS = 500  # 每页时长 = 解说音频 + 0.5s(PRD F6)


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


def page_clip_cmd(image: Path, audio: Path | None, duration_ms: int, out: Path) -> list[str]:
    # 有解说音频才补 0.5s 尾缓冲,免得末字被淡出吃掉;静帧片头/片尾用原时长
    total_ms = duration_ms + BUFFER_MS if audio else duration_ms
    dur = total_ms / 1000
    vf = (f"scale=1920:1080:force_original_aspect_ratio=decrease,"
          f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
          f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(dur - FADE, 0):.2f}:d={FADE},"
          f"format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image)]
    if audio:
        cmd += ["-i", str(audio), "-af", "apad"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    # 强制 44.1kHz/立体声,与片头/片尾静音分支(anullsrc=r=44100:cl=stereo)对齐,
    # 否则解说 mp3(常见 24kHz/mono)会让 concat demuxer 拿到参数不一致的音频流而错乱
    cmd += ["-t", f"{dur:g}", "-vf", vf, "-r", str(FPS),
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2", str(out)]
    return cmd


def concat_cmd(clips: list[Path], list_file: Path, out: Path) -> list[str]:
    list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", str(out)]


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
