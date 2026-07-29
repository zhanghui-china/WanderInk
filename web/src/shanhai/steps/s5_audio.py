"""S5 配音配乐。骨架局限:无 SSML 多音字标注(PRD F5),接国内 TTS/本地方案时补。
TTS 不可用时按文案字数估算时长、生成静音音轨兜底,成片完整但无解说。"""
import concurrent.futures as cf
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from shanhai import ffmpeg
from shanhai.ffmpeg import probe_duration_ms
from shanhai.providers.music import MusicClient
from shanhai.providers.tts import TTSClient
from shanhai.schema import LocalizedTrack, Project, StoryboardCell

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "assets" / "bgm" / "manifest.json"
MIN_MS = 2500             # 单页最短显示时长
DEFAULT_LANG = "zh"       # 主语言:文本/音频仍存在 StoryboardCell 自身字段上

# 情绪基调 → 器乐风格标签(ACE-Step 的 style 是音乐风格描述,不经 LLM,直接查表拼装)。
TONE_MUSIC_TAGS = {
    "温情": ["Warm strings", "Gentle piano", "Slow tempo", "Nostalgic"],
    "奇幻": ["Cinematic orchestral", "Mystical", "Ethereal choir pads", "Sweeping"],
    "悬疑": ["Dark ambient", "Tense strings", "Low drones", "Suspenseful"],
}
# 画风 style_preset(styles.py) → 配器风格标签,保持声音质感与画面质感呼应。
STYLE_PRESET_MUSIC_TAGS = {
    "guofeng_ink": ["Guzheng", "Erhu", "Traditional Chinese instrumentation", "Ink-wash ambience"],
    "kids_picture_book": ["Playful woodwinds", "Glockenspiel", "Light and bouncy", "Storybook whimsy"],
    "modern_illust": ["Modern cinematic", "Soft synth pads", "Clean acoustic guitar"],
}
MUSIC_MAX_S = 180.0       # ACE-Step 单曲合理上限;超时长交给 finalize_cmd 的 -stream_loop 循环兜底
MUSIC_RETRIES = 1         # 见 providers/music.py 的取舍说明:单次生成慢,不宜死磕重试
# 每语种的朗读节奏:(每秒字符数, 每字符最短毫秒数)。
# - 每秒字符数用于 TTS 不可用时按文案长度估算静音兜底时长。
# - 每字符最短毫秒数是"疑似截断"的下限阈值:低于 字符数×该值 才判截断。
# zh:DGX 实测 CosyVoice2 连读约 240–270ms/字(见 docs/deploy-dgx.md P1 实证),旧值 380 高于
#    真实语速会把正常语音误判为截断而空转 TTS_TRIES,150 只兜住真正的严重截断。
# en:2026-07-27 在 DGX 上拿 Qwen3-TTS(EN-Female)实测 5 段真实体量解说词定标,取代此前
#    按通用语速折算的估值 (14.0, 55):实测 16.55 字符/秒,54.1~66.9ms/字符(均值 60.4)。
#    旧的下限 55 比实测最小值 54.1 还高,正常英文会被判成截断而空转 TTS_TRIES——这正是
#    当年中文 380 那个坑的翻版。下限按中文同款留白比例(150/221≈0.68)取 35。
LANG_PACE: dict[str, tuple[float, int]] = {"zh": (4.0, 150), "en": (16.5, 35)}


def _pace(lang: str) -> tuple[float, int]:
    """未知语种回落到主语言参数——宁可沿用中文阈值,也不要因为查不到而崩掉整轮配音。"""
    return LANG_PACE.get(lang, LANG_PACE[DEFAULT_LANG])
TTS_TRIES = 3             # 弱模型 TTS 偶发截断/空返回,分句退化路径里重合成取最长的一次
CLAUSE_DELIMS = "。！？；，、：!?;,:\n"  # 全角+半角句/读点;短输入避开小模型的确定性提前停止
MIN_CLAUSE_CHARS = 3      # 短于此的碎片并入相邻句,避免逐字合成发碎
S5_CONCURRENCY = 3        # 逐页配音并发上限,与 S4 同量级(代理过载/本地 TTS 排队保守取值)


def _estimate_ms(caption: str, lang: str = DEFAULT_LANG) -> int:
    chars_per_sec, _ = _pace(lang)
    return max(MIN_MS, round(len(caption) / chars_per_sec * 1000))


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
                       speed: float = 1.0, lang: str = DEFAULT_LANG) -> int:
    """合成一句并检测截断:时长明显偏短则重合成,始终保留最长的一次。返回时长 ms。"""
    # floor 随 speed 缩放:每字符最短毫秒数按 speed=1.0 校准,语速越快每字应有的最短时长越短,
    # 否则高语速正常语音会被误判截断而空转 TTS_TRIES。speed=1.0 时与原值一致(无回归)。
    floor = round(len(text) * _pace(lang)[1] / speed)
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
                     speed: float = 1.0, lang: str = DEFAULT_LANG) -> int:
    """整段单发优先:CosyVoice2 类稳定模型一次合成整句最自然、且不截断(DGX 实测,见
    docs/deploy-dgx.md P1),省去逐句的多次调用与句间硬拼。仅当单发结果疑似截断
    (时长 < 字符数×每字符最短毫秒数)才退化到逐句合成,兼容会确定性截断的弱模型。
    返回真实时长 ms;失败向上抛。"""
    if not caption.strip():
        raise ValueError("空文案,无法合成")
    ms = _synthesize_single(tts, caption, voice, out, speed=speed)
    # floor 随 speed 缩放(同 _synthesize_clause):高语速下正常语音更短,不应误判为截断而退化逐句。
    floor = round(len(caption) * _pace(lang)[1] / speed)
    if ms >= floor:
        return ms
    print(f"⚠️ 整段合成疑似截断({lang}:{ms}ms<{floor}ms),退化逐句合成")
    return _synthesize_chunked(tts, caption, voice, out, speed=speed, lang=lang)


def _synthesize_single(tts: TTSClient, caption: str, voice: str, out: Path,
                       speed: float = 1.0) -> int:
    """整段一次性合成 + 修剪首尾静音。返回时长 ms。"""
    raw = out.with_suffix(".raw.mp3")
    try:
        tts.synthesize(caption, voice, raw, speed=speed)
        ffmpeg.sh(ffmpeg.trim_silence_cmd(raw, out))
        return probe_duration_ms(out)
    finally:
        raw.unlink(missing_ok=True)


def _synthesize_chunked(tts: TTSClient, caption: str, voice: str, out: Path,
                        speed: float = 1.0, lang: str = DEFAULT_LANG) -> int:
    """退化路径:按标点分句、逐句合成(避开弱模型的确定性截断)、逐句修剪首尾静音、拼接为整页音轨。
    返回真实总时长 ms;失败向上抛。"""
    clauses = _split_clauses(caption)
    if not clauses:
        raise ValueError("空文案,无法合成")
    raws = [out.with_suffix(f".raw{i:02d}.mp3") for i in range(len(clauses))]
    parts = [out.with_suffix(f".part{i:02d}.mp3") for i in range(len(clauses))]
    list_file = out.with_suffix(".concat.txt")
    try:
        for clause, raw, part in zip(clauses, raws, parts):
            _synthesize_clause(tts, clause, voice, raw, speed=speed, lang=lang)
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


def track_of(cell: StoryboardCell, lang: str) -> StoryboardCell | LocalizedTrack:
    """取该语种的文本/音频载体。主语言直接用 cell 自身的字段(零改动、零迁移);
    其它语种用 cell.tracks[lang],不存在则就地建一个空轨。
    两者字段同名(caption/audio/duration_ms/silent),调用方无需分支。"""
    if lang == DEFAULT_LANG:
        return cell
    return cell.tracks.setdefault(lang, LocalizedTrack())


def _process_cell(cell, tts: TTSClient, voice: str, speed: float,
                  audio_dir: Path, workdir: Path, lang: str = DEFAULT_LANG) -> None:
    """单页配音:线程安全——只写各自 page_NN[.lang].mp3 与各自 cell,不共享可变态。
    TTS 失败→静音兜底;兜底也失败→留空(异常在此吞掉,不炸线程池)。"""
    track = track_of(cell, lang)
    suffix = "" if lang == DEFAULT_LANG else f".{lang}"
    out = audio_dir / f"page_{cell.index:02d}{suffix}.mp3"
    if not track.caption.strip():
        return   # 该语种还没有译文(如英文轨尚未翻译到这一页),跳过而不是合成空音频
    try:
        # 静音兜底页不短路:即使已有音轨也应重试真人合成,以便 TTS 恢复后重跑补回解说。
        # ⚠️ 这个复用分支**必须**在 try 内:probe_duration_ms 对损坏的 mp3(上次进程被 kill、
        # 磁盘写满)会抛,而它抛在 try 外就会穿透线程池炸掉整轮 S5——坏文件又一直躺在盘上,
        # 每次重跑都在同一页炸,永远无法自愈。落进下面的静音兜底才能让这一轮跑完。
        if track.audio and not track.silent and out.exists():
            track.duration_ms = max(probe_duration_ms(out), MIN_MS)  # M6:续跑复用也套 MIN_MS 下限
            return
        # M6:成功路径抬到 MIN_MS,避免修剪后极短音频让页面一闪而过。
        track.duration_ms = max(
            _synthesize_full(tts, track.caption, voice, out, speed=speed, lang=lang), MIN_MS)
        track.audio = str(out.relative_to(workdir))
        track.silent = False
    except Exception as e:  # noqa: BLE001 TTS/探测失败 → 静音兜底,成片完整但该页无解说
        try:
            dur = _estimate_ms(track.caption, lang)
            ffmpeg.sh(ffmpeg.silent_audio_cmd(dur, out))
            track.audio = str(out.relative_to(workdir))
            track.duration_ms = dur
            track.silent = True
            print(f"第 {cell.index} 页 TTS 失败({lang}),静音兜底({dur}ms):{e}")
        except Exception as e2:  # noqa: BLE001 兜底也失败 → 留空,S6 跳过该页
            print(f"第 {cell.index} 页配音+兜底均失败({lang}):{e2}")
            track.audio = ""
            track.duration_ms = 0
            track.silent = False   # 无音轨,silent 复位,免让 UI 误标"静音兜底"


def _build_music_prompt(project: Project) -> str:
    """纯器乐 BGM 风格描述,拼给 ACE-Step 的 style 文本。不含地名/传说名——style 节点是
    音乐风格描述,不是歌词/主题文本,放进去对生成无意义甚至误导。"""
    tags = TONE_MUSIC_TAGS.get(project.params.tone, []) \
         + STYLE_PRESET_MUSIC_TAGS.get(project.style_preset, [])
    tags.append("Instrumental")  # 双保险:即便 lyrics 字段未生效,style 里也显式声明纯器乐
    dedup = list(dict.fromkeys(tags))  # 去重保序
    return "Style:\n" + "\n".join(f"- {t}" for t in dedup)


def _target_music_duration_s(project: Project) -> float:
    return min(project.params.duration_min * 60, MUSIC_MAX_S)


def _select_manifest_bgm(project: Project, manifest_path: Path) -> str | None:
    """静态曲库按情绪选曲。曲库为空/无内容页 → None(正常的"无 BGM",不是异常);
    manifest 缺失/损坏/字段不全等才是异常,向上抛给调用方 catch。"""
    tracks = json.loads(manifest_path.read_text(encoding="utf-8")).get("tracks", [])
    if not tracks or not project.storyboard:
        return None
    mood = Counter(c.emotion for c in project.storyboard).most_common(1)[0][0]
    match = next((t for t in tracks if mood in t.get("emotions", [])), tracks[0])
    return str(manifest_path.parent / match["file"])


def _generate_ai_bgm(project: Project, music: MusicClient, workdir: Path) -> str:
    """调本机 ACE-Step shim 生成纯器乐 BGM,写入 workdir/audio/bgm.mp3。
    失败(shim 未部署/超时/非法响应)直接向上抛,由 run() 捕获后降级到 manifest 曲库。"""
    prompt = _build_music_prompt(project)
    duration_s = _target_music_duration_s(project)
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out = audio_dir / "bgm.mp3"
    music.generate(prompt, duration_s, out, retries=MUSIC_RETRIES)
    return str(out)


def _resolve_bgm(project: Project, music: MusicClient | None, workdir: Path,
                 manifest_path: Path) -> str:
    """三级降级选 BGM:AI 生成 → 静态曲库 → 无 BGM,返回路径(无则空串)。
    H4:任何失败(AI shim 未部署/超时、manifest 缺失/损坏/字段不全)都整段捕获降级,绝不向上抛——
    BGM 是非关键增强,不该拖垮更关键的配音;在独立线程里跑亦不会炸掉 TTS 线程池或整个 S5。

    结果一律写进 project.status["bgm"](ai/manifest/failed/skipped)。这是 2026-07-27 的教训:
    此前失败完全静默——实际触发的那两条分支一行日志都不打、status 里没有任何 BGM 键、前端
    也零处显示,于是 music-shim 的模板路径写错这件事攒了 33 个无配乐的作品才被用户发现。"""
    if not project.params.bgm:
        # 用户没勾配乐:直接跳过,不白烧一次 ACE-Step(单曲最长 180s 且与生图抢同一块 GPU)
        project.status["bgm"] = "skipped"
        return ""
    bgm_path: str | None = None
    if music is not None:
        try:
            bgm_path = _generate_ai_bgm(project, music, workdir)
        except Exception as e:  # noqa: BLE001 AI BGM 非关键,失败降级到静态曲库
            print(f"⚠️ AI BGM 生成失败,降级到静态曲库:{e}")
    if bgm_path is not None:
        project.status["bgm"] = "ai"
        return bgm_path
    try:
        bgm_path = _select_manifest_bgm(project, manifest_path)
    except Exception as e:  # noqa: BLE001 BGM 非关键,任何失败都跳过配乐而非拖垮 S5
        print(f"⚠️ BGM 选曲失败({manifest_path}),跳过配乐:{e}")
    if bgm_path:
        project.status["bgm"] = "manifest"
        return bgm_path
    # 曲库为空也算 failed 而不是"正常无配乐":用户勾了配乐却没拿到,就是没满足他的要求
    print("⚠️ AI 生成与静态曲库均未产出 BGM,本片无配乐")
    project.status["bgm"] = "failed"
    return ""


def run(project: Project, tts: TTSClient, voice: str, workdir: Path,
        music: MusicClient | None = None, manifest_path: Path = DEFAULT_MANIFEST,
        cancel_check: Callable[[], bool] | None = None,
        lang: str = DEFAULT_LANG) -> Project:
    # 非主语言走 params.voice_en(留空则用调用方传入的配置层默认);BGM 与语言无关,
    # 已经由主语言那轮生成过,附加语种轨不再重跑,免得白烧一次 GPU 还覆盖掉现有 project.bgm。
    is_main = lang == DEFAULT_LANG
    effective_voice = (project.params.voice or voice) if is_main \
        else (project.params.voice_en or voice)
    speed = project.params.speed
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    # PERF:BGM 生成(AI 最长 180s)与逐页 TTS 并行——BGM 结果仅 s6 finalize 用到,与逐页 TTS 无
    # 数据依赖,不必串行等它跑完才配音。BGM 独占一个 worker(+1),TTS 仍有 S5_CONCURRENCY 路并发。
    # 逐页 TTS 线程安全:每页写各自 page_NN.mp3 与各自 cell,_synthesize_full 的临时文件均由 out
    # 派生(page_NN.*),各页互不相干;单页异常已在 _process_cell 内吞掉并兜底,不炸线程池。
    # BGM 在独立线程里三级降级、异常自兜底(_resolve_bgm 内整段捕获),故 bgm_future.result() 不抛。
    with cf.ThreadPoolExecutor(max_workers=S5_CONCURRENCY + 1) as ex:
        # BGM 与语言无关,主语言那轮已经选好/生成好了:附加语种轨不再重跑,既省一次 GPU,
        # 也避免用曲库兜底的结果覆盖掉已有的 AI BGM。
        bgm_future = ex.submit(_resolve_bgm, project, music, workdir, manifest_path) \
            if is_main else None
        futures = [ex.submit(_process_cell, cell, tts, effective_voice, speed,
                             audio_dir, workdir, lang) for cell in project.storyboard]
        cancelled = False
        for f in cf.as_completed(futures):
            if cancel_check and cancel_check():
                cancelled = True
                for pending_f in futures:
                    pending_f.cancel()  # 已开始的取消不了(Python 线程池物理限制),但能拦掉还没排上的
                break
            f.result()
        if bgm_future is None:
            pass   # 附加语种轨不碰 BGM,沿用主语言那轮的 project.bgm
        elif cancelled:
            bgm_future.cancel()  # 已开始的 BGM 生成取消不了,但至少不再无条件等它(BGM 非关键,允许缺失)
            if not bgm_future.cancelled():
                project.bgm = bgm_future.result()  # 已经在跑,只能等它跑完;_resolve_bgm 内部自兜底不会抛
        else:
            project.bgm = bgm_future.result()
    # 诚实状态:仅当每页都有真人解说(有音频且非静音兜底)才算 done;否则 partial。
    # 附加语种轨记在自己的状态键上,不覆盖主语言的 s5。
    tracks = [track_of(c, lang) for c in project.storyboard]
    narrated = bool(tracks) and all(t.audio and not t.silent for t in tracks)
    project.status["s5" if is_main else f"s5_{lang}"] = "done" if narrated else "partial"
    return project
