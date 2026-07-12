"""S5 配音配乐。骨架局限:无 SSML 多音字标注(PRD F5),接国内 TTS/本地方案时补。
TTS 不可用时按文案字数估算时长、生成静音音轨兜底,成片完整但无解说。"""
import concurrent.futures as cf
import json
from collections import Counter
from pathlib import Path

from shanhai import ffmpeg
from shanhai.ffmpeg import probe_duration_ms
from shanhai.providers.tts import TTSClient
from shanhai.schema import Project

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "assets" / "bgm" / "manifest.json"
CHARS_PER_SEC = 4.0       # 解说语速估算(与 PRD S1 字数-时长模型同量级)
MIN_MS = 2500             # 单页最短显示时长
MIN_MS_PER_CHAR = 380     # 完整解说约 420+ms/字;低于字数×380ms 几乎必是 TTS 截断
TTS_TRIES = 3             # 小模型 TTS 偶发截断/空返回,重合成取最长的一次
CLAUSE_DELIMS = "。！？；，、：!?;,:\n"  # 全角+半角句/读点;短输入避开小模型的确定性提前停止
MIN_CLAUSE_CHARS = 3      # 短于此的碎片并入相邻句,避免逐字合成发碎
S5_CONCURRENCY = 3        # 逐页配音并发上限,与 S4 同量级(代理过载/本地 TTS 排队保守取值)


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


def _synthesize_clause(tts: TTSClient, text: str, voice: str, dest: Path,
                       speed: float = 1.0) -> int:
    """合成一句并检测截断:时长明显偏短则重合成,始终保留最长的一次。返回时长 ms。"""
    floor = len(text) * MIN_MS_PER_CHAR
    tmp = dest.with_suffix(".try.mp3")
    best_ms = 0
    for _ in range(TTS_TRIES):
        tts.synthesize(text, voice, tmp, speed=speed)
        ms = probe_duration_ms(tmp)
        if ms > best_ms:
            tmp.replace(dest)
            best_ms = ms
        else:
            tmp.unlink(missing_ok=True)
        if best_ms >= floor:
            break
    if best_ms < floor:  # H3:重试用尽仍偏短 → 截断可见(音频仍是真人,只是偏短,不改 silent 语义)
        print(f"⚠️ TTS 疑似截断:「{text[:12]}」best={best_ms}ms < floor={floor}ms")
    return best_ms


def _synthesize_full(tts: TTSClient, caption: str, voice: str, out: Path,
                     speed: float = 1.0) -> int:
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
            _synthesize_clause(tts, clause, voice, raw, speed=speed)
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


def _process_cell(cell, tts: TTSClient, voice: str, speed: float,
                  audio_dir: Path, workdir: Path) -> None:
    """单页配音:线程安全——只写各自 page_NN.mp3 与各自 cell,不共享可变态。
    TTS 失败→静音兜底;兜底也失败→留空(异常在此吞掉,不炸线程池)。"""
    out = audio_dir / f"page_{cell.index:02d}.mp3"
    # 静音兜底页不短路:即使已有音轨也应重试真人合成,以便 TTS 恢复后重跑补回解说。
    if cell.audio and not cell.silent and out.exists():
        cell.duration_ms = max(probe_duration_ms(out), MIN_MS)   # M6:续跑复用也套 MIN_MS 下限
        return
    try:
        # M6:成功路径抬到 MIN_MS,避免修剪后极短音频让页面一闪而过。
        cell.duration_ms = max(_synthesize_full(tts, cell.caption, voice, out, speed=speed),
                               MIN_MS)
        cell.audio = str(out.relative_to(workdir))
        cell.silent = False
    except Exception as e:  # noqa: BLE001 TTS/探测失败 → 静音兜底,成片完整但该页无解说
        try:
            dur = _estimate_ms(cell.caption)
            ffmpeg.sh(ffmpeg.silent_audio_cmd(dur, out))
            cell.audio = str(out.relative_to(workdir))
            cell.duration_ms = dur
            cell.silent = True
            print(f"第 {cell.index} 页 TTS 失败,静音兜底({dur}ms):{e}")
        except Exception as e2:  # noqa: BLE001 兜底也失败 → 留空,S6 跳过该页
            print(f"第 {cell.index} 页配音+兜底均失败:{e2}")
            cell.audio = ""
            cell.duration_ms = 0
            cell.silent = False   # 无音轨,silent 复位,免让 UI 误标"静音兜底"


def run(project: Project, tts: TTSClient, voice: str, workdir: Path,
        manifest_path: Path = DEFAULT_MANIFEST) -> Project:
    effective_voice = project.params.voice or voice
    speed = project.params.speed
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    # H4:BGM 选曲前置并「整体」兜底——manifest 缺失/损坏/字段不全(如 track 缺 file)都不该
    # 毁掉后面已完成的合成,故置于 TTS 之前且整段捕获:BGM 是非关键增强,任何失败都降级为无配乐。
    try:
        tracks = json.loads(manifest_path.read_text(encoding="utf-8")).get("tracks", [])
        if tracks and project.storyboard:
            mood = Counter(c.emotion for c in project.storyboard).most_common(1)[0][0]
            match = next((t for t in tracks if mood in t.get("emotions", [])), tracks[0])
            project.bgm = str(manifest_path.parent / match["file"])
    except Exception as e:  # noqa: BLE001 BGM 非关键,任何失败都跳过配乐而非拖垮 S5
        print(f"⚠️ BGM 选曲失败({manifest_path}),跳过配乐:{e}")
    # PERF1:逐页配音并行(仿 S4)。线程安全:每页写各自 page_NN.mp3 与各自 cell,
    # _synthesize_full 的临时文件均由 out 派生(page_NN.*),各页互不相干;单页异常已在
    # _process_cell 内吞掉并兜底,as_completed 收集不让一页炸掉线程池。
    with cf.ThreadPoolExecutor(max_workers=S5_CONCURRENCY) as ex:
        futures = [ex.submit(_process_cell, cell, tts, effective_voice, speed,
                             audio_dir, workdir) for cell in project.storyboard]
        for f in cf.as_completed(futures):
            f.result()
    # 诚实状态:仅当每页都有真人解说(有音频且非静音兜底)才算 done;否则 partial
    narrated = bool(project.storyboard) and all(
        c.audio and not c.silent for c in project.storyboard)
    project.status["s5"] = "done" if narrated else "partial"
    return project
