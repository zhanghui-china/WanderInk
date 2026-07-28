"""SRT 软字幕生成。字幕不再烧进画面(见 typeset.overlay_image 的 caption 可选),
改由成片内嵌软字幕轨承载,观众可开关、可中英切换。

时间轴要点:成片是 xfade 交叉溶解拼接的,相邻片段重叠 XFADE_S 秒,所以每个 clip 在
最终时间轴上的起点**不是**前面时长的天真累加——必须走 ffmpeg.xfade_offsets 的同一套
偏移计算,否则字幕会随页数递增而越漂越远。
"""
from pathlib import Path

from shanhai import ffmpeg

Cue = tuple[float, float, str]   # (起, 止, 文本),单位秒


def clip_start_times(durations_s: list[float], t: float = ffmpeg.XFADE_S) -> list[float]:
    """每个 clip 在成片时间轴上的起始时刻(秒)。首段从 0 开始,其余取 xfade 过渡起点
    ——xfade_offsets 算的正是"下一段开始淡入"的时刻,与 clip 起点同义。"""
    if not durations_s:
        return []
    return [0.0, *ffmpeg.xfade_offsets(durations_s, t)]


def _ts(seconds: float) -> str:
    """秒 -> SRT 时间戳 HH:MM:SS,mmm(负值夹到 0,避免异常输入产出非法字幕)。"""
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _blocks(cues: list[Cue], sep: str) -> list[str]:
    """SRT 与 VTT 的唯一实质差别就是时间戳的毫秒分隔符(SRT 用逗号、VTT 用点),
    所以 cue 的筛选与编号逻辑必须共用——两边各写一遍迟早漂移。
    空文本的 cue 直接跳过(某页没有该语种译文时不产出空字幕块),序号按实际写出的
    条目连续编号,不留空洞。"""
    out: list[str] = []
    for start, end, text in cues:
        text = text.strip()
        if not text:
            continue
        a, b = _ts(start).replace(",", sep), _ts(end).replace(",", sep)
        out.append(f"{len(out) + 1}\n{a} --> {b}\n{text}\n")
    return out


def build_srt(cues: list[Cue], out: Path) -> None:
    """写 SRT。给 ffmpeg 的 mov_text 内嵌字幕轨用。"""
    out.write_text("\n".join(_blocks(cues, ",")), encoding="utf-8")


def build_vtt(cues: list[Cue], out: Path) -> None:
    """写 WebVTT。**给网页播放器用**——浏览器的 HTML5 <video> 不解析 MP4 容器内的
    mov_text 字幕轨(Chrome/Firefox/Edge 一律忽略),网页里显示字幕唯一的办法是
    <track kind="subtitles"> 外挂 VTT;而 SRT 也不是浏览器认的格式。
    与 build_srt 共用同一份 cues,时间轴不重算(见 _blocks)。"""
    out.write_text("WEBVTT\n\n" + "\n".join(_blocks(cues, ".")), encoding="utf-8")
