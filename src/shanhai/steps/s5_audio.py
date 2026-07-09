"""S5 配音配乐。骨架局限:无 SSML 多音字标注(PRD F5),接国内 TTS/本地方案时补。
TTS 不可用时按文案字数估算时长、生成静音音轨兜底,成片完整但无解说。"""
import json
from collections import Counter
from pathlib import Path

from shanhai import ffmpeg
from shanhai.ffmpeg import probe_duration_ms
from shanhai.providers.tts import TTSClient
from shanhai.schema import Project

DEFAULT_MANIFEST = Path("assets/bgm/manifest.json")
CHARS_PER_SEC = 4.0   # 解说语速估算(与 PRD S1 字数-时长模型同量级)
MIN_MS = 2500         # 单页最短显示时长


def _estimate_ms(caption: str) -> int:
    return max(MIN_MS, round(len(caption) / CHARS_PER_SEC * 1000))


def run(project: Project, tts: TTSClient, voice: str, workdir: Path,
        manifest_path: Path = DEFAULT_MANIFEST) -> Project:
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for cell in project.storyboard:
        out = audio_dir / f"page_{cell.index:02d}.mp3"
        if cell.audio and out.exists():
            cell.duration_ms = probe_duration_ms(out)
            continue
        try:
            tts.synthesize(cell.caption, voice, out)
            cell.duration_ms = probe_duration_ms(out)
            cell.audio = str(out.relative_to(workdir))
        except Exception as e:  # noqa: BLE001 TTS/探测失败 → 静音兜底,成片完整但该页无解说
            try:
                dur = _estimate_ms(cell.caption)
                ffmpeg.sh(ffmpeg.silent_audio_cmd(dur, out))
                cell.audio = str(out.relative_to(workdir))
                cell.duration_ms = dur
                print(f"第 {cell.index} 页 TTS 失败,静音兜底({dur}ms):{e}")
            except Exception as e2:  # noqa: BLE001 兜底也失败 → 留空,S6 跳过该页
                print(f"第 {cell.index} 页配音+兜底均失败:{e2}")
                cell.audio = ""
                cell.duration_ms = 0
    tracks = json.loads(manifest_path.read_text(encoding="utf-8")).get("tracks", [])
    if tracks and project.storyboard:
        mood = Counter(c.emotion for c in project.storyboard).most_common(1)[0][0]
        match = next((t for t in tracks if mood in t.get("emotions", [])), tracks[0])
        project.bgm = str(manifest_path.parent / match["file"])
    project.status["s5"] = "done" if all(c.audio for c in project.storyboard) else "partial"
    return project
