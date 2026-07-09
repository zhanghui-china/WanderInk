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
CHARS_PER_SEC = 4.0       # 解说语速估算(与 PRD S1 字数-时长模型同量级)
MIN_MS = 2500             # 单页最短显示时长
MIN_MS_PER_CHAR = 380     # 完整解说约 420+ms/字;低于字数×380ms 几乎必是 TTS 截断
TTS_TRIES = 3             # 小模型 TTS 偶发截断/空返回,重合成取最长的一次
CLAUSE_DELIMS = "。！？；，、：!?;,:\n"  # 全角+半角句/读点;短输入避开小模型的确定性提前停止
MIN_CLAUSE_CHARS = 3      # 短于此的碎片并入相邻句,避免逐字合成发碎


def _estimate_ms(caption: str) -> int:
    return max(MIN_MS, round(len(caption) / CHARS_PER_SEC * 1000))


def _split_clauses(caption: str) -> list[str]:
    """按标点切成短句(分隔符留在前段末尾,换行不保留);短碎片并入相邻句。
    无标点→单元素;空串→[]。"""
    frags: list[str] = []
    buf = ""
    for ch in caption:
        if ch in CLAUSE_DELIMS:
            if ch != "\n":
                buf += ch
            if buf.strip():
                frags.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        frags.append(buf.strip())
    merged: list[str] = []
    for f in frags:
        if merged and len(f) < MIN_CLAUSE_CHARS:
            merged[-1] += f
        else:
            merged.append(f)
    if len(merged) > 1 and len(merged[0]) < MIN_CLAUSE_CHARS:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged


def _synthesize_clause(tts: TTSClient, text: str, voice: str, dest: Path) -> int:
    """合成一句并检测截断:时长明显偏短则重合成,始终保留最长的一次。返回时长 ms。"""
    floor = len(text) * MIN_MS_PER_CHAR
    tmp = dest.with_suffix(".try.mp3")
    best_ms = 0
    for _ in range(TTS_TRIES):
        tts.synthesize(text, voice, tmp)
        ms = probe_duration_ms(tmp)
        if ms > best_ms:
            tmp.replace(dest)
            best_ms = ms
        else:
            tmp.unlink(missing_ok=True)
        if best_ms >= floor:
            break
    return best_ms


def _synthesize_full(tts: TTSClient, caption: str, voice: str, out: Path) -> int:
    """按标点分句、逐句合成(避开确定性截断)、逐句修剪首尾静音、拼接为整页音轨。
    返回真实总时长 ms;失败向上抛。"""
    clauses = _split_clauses(caption)
    if not clauses:
        raise ValueError("空文案,无法合成")
    raws = [out.with_suffix(f".raw{i:02d}.mp3") for i in range(len(clauses))]
    parts = [out.with_suffix(f".part{i:02d}.mp3") for i in range(len(clauses))]
    list_file = out.with_suffix(".concat.txt")
    try:
        for clause, raw, part in zip(clauses, raws, parts):
            _synthesize_clause(tts, clause, voice, raw)
            ffmpeg.sh(ffmpeg.trim_silence_cmd(raw, part))   # 修剪该句首尾静音
        if len(parts) == 1:
            parts[0].replace(out)
        else:
            list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in parts),
                                 encoding="utf-8")
            ffmpeg.sh(ffmpeg.concat_audio_cmd(parts, list_file, out))
        return probe_duration_ms(out)
    finally:
        for r in raws:
            r.unlink(missing_ok=True)
            r.with_suffix(".try.mp3").unlink(missing_ok=True)
        for p in parts:
            p.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)


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
            cell.duration_ms = _synthesize_full(tts, cell.caption, voice, out)
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
