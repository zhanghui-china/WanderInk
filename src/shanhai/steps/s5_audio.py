"""S5 配音配乐。骨架局限:无 SSML 多音字标注(PRD F5),接国内 TTS/本地方案时补。"""
import json
from collections import Counter
from pathlib import Path

from shanhai.ffmpeg import probe_duration_ms
from shanhai.providers.tts import TTSClient
from shanhai.schema import Project

DEFAULT_MANIFEST = Path("assets/bgm/manifest.json")


def run(project: Project, tts: TTSClient, voice: str, workdir: Path,
        manifest_path: Path = DEFAULT_MANIFEST) -> Project:
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for cell in project.storyboard:
        out = audio_dir / f"page_{cell.index:02d}.mp3"
        if not (cell.audio and out.exists()):
            tts.synthesize(cell.caption, voice, out)
            cell.audio = str(out.relative_to(workdir))
        cell.duration_ms = probe_duration_ms(out)
    tracks = json.loads(manifest_path.read_text(encoding="utf-8")).get("tracks", [])
    if tracks and project.storyboard:
        mood = Counter(c.emotion for c in project.storyboard).most_common(1)[0][0]
        match = next((t for t in tracks if mood in t.get("emotions", [])), tracks[0])
        project.bgm = str(manifest_path.parent / match["file"])
    project.status["s5"] = "done"
    return project
