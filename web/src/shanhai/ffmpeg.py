import os
import re
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


# 单位秒。目的只是"别永远挂着":卡死的 ffmpeg 会让调用它的线程永久阻塞,而阻塞面很宽——
# _EXECUTOR 只有 4 个作业槽(4 个卡死即全站生成停摆)、S6 页编码池的 shutdown(wait=True) 会让
# 一页卡死拖住整个 worker、uploads 那条路更是直接跑在 HTTP 请求线程上(客户端永远收不到响应);
# 连带效应还有:非守护线程 join 不掉,重启必然走成 SIGKILL 硬杀(见 docs/ops-dgx.md)。
# 而 api._stalled 救不了这种情况——线程还活着,f.done() 恒为 False,前端会一直显示"正在生成…"。
#
# 取值宽松是有意的:实测最贵的单次调用是 xfade_concat_cmd(整片重编码),本机 M4 上
# 10 页/172s 成片 31s、22 页外推 60-70s,DGX 上按 3-5× 保守估 3-6 分钟;其余调用全部 <0.15s。
# 按本项目既有惯例(config.py 的 image_timeout 注释:实测 ×5)取 1800s。宽松无害(正常渲染
# 远达不到),无限有害。运维可用环境变量临时放宽,不必重新部署。
FFMPEG_TIMEOUT_S = float(os.getenv("SHANHAI_FFMPEG_TIMEOUT", "1800"))
# ffprobe 只读容器头算时长,实测 0.02s,没理由和整片编码共用同一份预算。
FFPROBE_TIMEOUT_S = float(os.getenv("SHANHAI_FFPROBE_TIMEOUT", "60"))


def _timeout_msg(cmd: list[str], timeout: float, stderr: bytes | str | None) -> str:
    tail = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    tail = tail.strip()[-500:]   # 只要尾部:卡住那一刻的进度行足以定位卡在哪一帧
    return f"ffmpeg 超时({timeout:g}s,已强杀)({' '.join(cmd[:3])}…):{tail}"


def sh(cmd: list[str], timeout: float | None = None) -> None:
    """执行一条 ffmpeg 命令;非零退出或超时都抛 RuntimeError(附 stderr 尾部)。

    **超时必须包成 RuntimeError,不能让 TimeoutExpired 裸奔**,两个具体理由:
    ① uploads.to_voice_sample_wav 只 `except RuntimeError` 来把解码失败转成 400,
       漏出去会退化成 500;
    ② 裸 TimeoutExpired 的消息是 "Command '[...]' timed out",整条 ffmpeg 命令行(含
       filter_complex 巨串与绝对路径)会被 _save_error 原样写进 project.json 的 pipeline
       字段并显示在界面上。

    subprocess.run 在超时后会先 kill() 子进程再收尾 communicate(),所以不会留下一个继续烧
    CPU 的孤儿 ffmpeg——这是"加超时"真正生效的前提,否则只是 Python 侧不等了、机器仍被占着。
    收尾那次 communicate 的产物会落在 TimeoutExpired.stderr 上(POSIX 下有值),故错误信息里
    仍能带上卡住那一刻的进度行。

    ⚠️ 救不了内核态硬卡:kill() 之后 stdlib 会做一次**无超时**的 wait(),若 ffmpeg 卡在不可
    中断 I/O(D 状态、坏盘、NFS 断连),这里依然会挂住。timeout 能救的是"活着但不出活"。"""
    budget = FFMPEG_TIMEOUT_S if timeout is None else timeout
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=budget)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(_timeout_msg(cmd, budget, e.stderr)) from e
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
    """读时长。超时同样包成 RuntimeError(与 sh 一致):唯一在 try 外调它的地方是
    uploads,那里不包装就会把整条 ffprobe 命令行漏进 500 响应;s5_audio 的调用点是
    `except Exception`,两种写法都接得住。"""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(path)]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True,
                             timeout=FFPROBE_TIMEOUT_S).stdout.strip()
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(_timeout_msg(cmd, FFPROBE_TIMEOUT_S, e.stderr)) from e
    if not out or out == "N/A":
        raise ValueError(f"ffprobe 无法解析时长(输出为 {out!r}):{path}")
    return int(float(out) * 1000)


# 成片人声的目标响度,与 finalize_cmd 里 loudnorm 的 I= 必须一致(它就是人声的基准线)。
VOICE_TARGET_LUFS = -16.0
# 配乐比人声低多少。广播/纪录片旁白配乐的通行档位是 15~20 dB;用户拍板 18。
BGM_BELOW_VOICE_DB = 18.0
# measure_lufs 解析失败时的兜底。**必须往"素材很响"的方向猜**:猜响 → 衰减更多 → 配乐偏轻,
# 顶多是不明显;猜轻 → 衰减不够 → 盖住解说,那正是这次要修的毛病。方向不能反。
_LUFS_FALLBACK = -8.0


def measure_lufs(path: Path) -> float:
    """整体响度(LUFS)。ACE-Step 出的曲子响度完全不受控——实测三首分别是
    -21.4 / -11.5 / -9.4,跨度 12 dB。以前用固定的 volume=0.18 盲乘,于是最响那首混完
    只比人声低 4.1 dB(而最轻那首低 17.3 dB),同一套参数听感天差地别。
    先量再算增益,相对关系才是确定的。60 秒素材约一秒跑完,代价可以忽略。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        # ebur128 的汇总块形如 "  I:         -9.4 LUFS";取最后一个即整体值
        found = re.findall(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", r.stderr, re.M)
        if found:
            return float(found[-1])
    except Exception as e:  # noqa: BLE001 —— ffmpeg 缺失/超时/输出格式变化都不该拖垮合成
        print(f"⚠️ 响度测量失败({path.name}),按 {_LUFS_FALLBACK} LUFS 兜底:{e}")
    return _LUFS_FALLBACK


def bgm_gain_db(bgm_lufs: float) -> float:
    """把实测响度换算成要施加的增益,使配乐恒定落在"比人声低 BGM_BELOW_VOICE_DB"。
    例:实测 -9.4 → -24.6 dB;实测 -21.4 → -12.6 dB。目标恒为 -34 LUFS。"""
    return VOICE_TARGET_LUFS - BGM_BELOW_VOICE_DB - bgm_lufs


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


# 人声闪避:说话时把配乐再压下去,停顿时(release 内)浮回来。
# 调参方向:threshold 越小越容易触发闪避;ratio 越大压得越狠;release 太短会有明显的
# "呼吸感"(配乐一起一伏),太长则段落间隙里配乐一直起不来。400ms 是二者的折中。
_DUCK = "sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400:makeup=1"


def finalize_cmd(video: Path, bgm: Path | None, out: Path,
                 bgm_gain_db: float = -20.0) -> list[str]:
    """成片收尾:响度规范化 +(可选)配乐混音。

    滤镜链的顺序是这次重做的核心,三处都不能随手改回去:

    1. **loudnorm 只作用于人声支路,不作用于混音结果。** 原实现是 `[mix]loudnorm`,
       而单遍 loudnorm 是**动态**的:人声间隙里它会抬高整体增益,把配乐顶上来——
       用户报的"背景音**有时候**比人声大"就是这个,"有时候"三个字正是动态增益的指纹。
    2. **配乐增益由实测响度算出(见 measure_lufs / bgm_gain_db),不再是固定的 volume=0.18。**
       ACE-Step 出的曲子响度跨度实测 12 dB,盲乘同一个系数,最响那首混完只比人声低 4.1 dB。
    3. **amix 必须显式 normalize=0。** 默认 normalize=1 会把每路除以路数(-6 dB),
       原先靠后面的 loudnorm 补回来;现在 loudnorm 已经挪到人声支路,不关掉整片会轻一半。

    结尾的 alimiter 是最后一道闸:人声已被 loudnorm 限到 TP=-1.5,再叠一路低 18 dB 的
    配乐理论上抬不过 0.5 dB,但闸门便宜,不留侥幸。
    """
    loudnorm = f"loudnorm=I={VOICE_TARGET_LUFS:g}:TP=-1.5:LRA=11"
    if bgm:
        # asplit 显式写出来:[voice] 要被消费两次(闪避的旁链 + 混音输入),而滤镜输出
        # 只能连一次。现代 ffmpeg 会自动插 asplit(实测无警告、结果正确),但那是隐式行为,
        # 换个版本就未必——写死更稳,也让读代码的人一眼看出这里有个分叉。
        fc = (f"[0:a]{loudnorm},asplit[voice][vkey];"
              # 开头 2 秒淡入:-stream_loop 从第一帧硬起,突然进来的乐声很突兀
              f"[1:a]volume={bgm_gain_db:.1f}dB,afade=t=in:d=2[bgraw];"
              f"[bgraw][vkey]{_DUCK}[duck];"
              f"[voice][duck]amix=inputs=2:duration=first:normalize=0[mix];"
              f"[mix]alimiter=limit=0.85[aout]")
        return ["ffmpeg", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    return ["ffmpeg", "-y", "-i", str(video), "-af", loudnorm,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]


def mux_subtitles_cmd(video: Path, subs: list[tuple[Path, str]], out: Path,
                      default_lang: str = "") -> list[str]:
    """把若干 SRT 作为软字幕轨封进 MP4。subs 是 (srt 路径, ISO 639-2 语种码) 列表,
    如 [(zh.srt, "zho"), (en.srt, "eng")]。default_lang 指定哪条轨为默认(ISO 639-2)。

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
        # 显式给每条轨定 disposition:命中 default_lang 的置 default,其余显式清 0。
        # 不写 0 不行——ffmpeg 会保留源流的 disposition,可能出现两条都是 default 或
        # 都不是。没有 default 时播放器一律选第一条,英文版就会弹中文字幕,用户的
        # 观感就是"没有英文字幕"(这正是本次反馈的成因之一)。
        cmd += [f"-disposition:s:{i}", "default" if lang == default_lang else "0"]
    cmd.append(str(out))
    return cmd
