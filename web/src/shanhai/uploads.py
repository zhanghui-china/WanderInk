"""用户上传文件的接收与净化(目前只有角色参考图)。

这是全仓第一处接收「用户提供的字节」的地方,所以校验从严:客户端声明的
content_type、文件名、扩展名一律不信,只信解码结果;并且**绝不把客户端字节原样落盘**,
一律重新编码成 PNG。
"""

import os
import secrets
import tempfile
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps

from shanhai import ffmpeg
from shanhai.steps.s4_pages import REF_MAX

# Pillow 12.3.0 默认 MAX_IMAGE_PIXELS=89478485,这里取更严的值:参考图最终只会被缩到
# REF_MAX,没有任何理由接收上亿像素的图,放宽只会给解压炸弹留窗口。
Image.MAX_IMAGE_PIXELS = 50_000_000

MAX_UPLOAD_BYTES = 8 * 1024 * 1024   # 8 MiB:手机直出照片绰绰有余,再大只会拖慢上传
MAX_PIXELS = 50_000_000              # 解压炸弹:文件几十 KB 却解出上亿像素,吃光内存
MIN_SIDE = 200                       # 太小的图放大后喂给图像模型只会糊,不如直接拒绝
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


async def read_limited(file: UploadFile) -> bytes:
    """流式读取并在超限的第一时间中断。

    ⚠️ **这里挡不住"字节落盘"**,只挡住"字节进内存"。FastAPI 在进 handler **之前**就已经
    `await request.form()` 把整个 multipart 解析完、spool 到临时文件了(而且发生在
    solve_dependencies 之前,连未登录请求也一样)。所以真正的落盘护栏在
    `api.BodySizeLimitMiddleware`——它在 form 解析前就按 content-length / 累计字节拒掉。
    这个函数是第二道:middleware 放行后,仍不把超限字节全部读进内存。
    Content-Length 不能作为唯一判据——分块传输根本没有这个头。
    """
    raw = bytearray()
    while chunk := await file.read(1 << 20):
        raw.extend(chunk)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"图片超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MiB 上限")
    return bytes(raw)


def to_reference_png(raw: bytes) -> bytes:
    """把上传字节净化成可安全落盘的参考图 PNG。

    重新编码这一步同时干掉四件事,缺一不可:
      1. polyglot 文件——伪装成图片、同时也是合法脚本/压缩包的字节,重编码后只剩像素;
      2. EXIF 里的 GPS 等隐私信息——用户上传的手机照片常带精确坐标;
      3. 超大原图——统一缩到 REF_MAX,与 S4 喂图像模型的尺寸一致;
      4. 格式杂乱——下游只需处理 PNG 一种。
    """
    try:
        im = Image.open(BytesIO(raw))
        im.verify()
    except Exception as e:   # noqa: BLE001 —— Pillow 对坏图抛什么异常没有稳定契约
        raise HTTPException(400, "无法识别的图片文件") from e
    if im.format not in ALLOWED_FORMATS:
        # 只看解码出来的真实格式,不看 content_type/扩展名(两者都由客户端随意声明)
        raise HTTPException(400, "仅支持 PNG / JPEG / WEBP 图片(iPhone 的 HEIC 请先转成 JPEG)")

    # verify() 会让图像对象失效(Pillow 的已知行为),必须重新 open 才能拿到真实像素数据
    im = Image.open(BytesIO(raw))
    w, h = im.size
    if w * h > MAX_PIXELS:
        raise HTTPException(400, "图片像素过大")
    if min(w, h) < MIN_SIDE:
        raise HTTPException(400, f"图片过小,最短边需不小于 {MIN_SIDE}px")

    # 真正的解码在这里才发生,所以这一段**必须**也在 try 里:Pillow 的 Image.verify()
    # 基类实现是空的,只有 PNG 重写了它(校验 CRC)。截断的 JPEG 能干干净净地通过上面
    # 的 verify(),到这一行才抛 OSError("image file is truncated")——包不住就是 HTTP 500,
    # 前端只能显示"HTTP 500"。而 JPEG 恰好是手机相册最常见的格式,云盘没同步完、
    # 传输中断留下的半张图都会中招,不需要恶意构造。
    try:
        # exif_transpose 必须在丢掉 EXIF 之前做:手机竖拍图的方向只存在 EXIF 里,
        # 先丢再转就永远救不回来了,画面会躺着。
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((REF_MAX, REF_MAX))
        buf = BytesIO()
        im.save(buf, "PNG")
    except Exception as e:   # noqa: BLE001 —— 同上,Pillow 的异常类型没有稳定契约
        raise HTTPException(400, "图片已损坏或不完整,无法处理") from e
    return buf.getvalue()


# --- 音色克隆的参考录音 ---
# 两个来源:浏览器 MediaRecorder(webm/opus,Safari 给 mp4/aac)与用户直接上传的 wav/mp3。
# 无论哪种都必须转码——转码本身就是净化(见 ffmpeg.voice_sample_cmd)。
#
# ⚠️ 8 MiB 是**上限**,不能再往上加:全局 BodySizeLimitMiddleware 是
# MAX_UPLOAD_BYTES(8 MiB) + 1 MiB = 9 MiB(api.py),超过这里就必须同时抬中间件。
# 为什么从 4 MiB 提上来:4 MiB 是按"20 秒 opus 约 300KB"定的,只够录音;而用户上传的
# 20 秒 48k/24bit/立体声 wav 就有 5.8 MB,会被无谓拒掉。
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MIN_VOICE_MS = 5_000                # 太短的样本克隆不出像的音色,不如当场拒绝让用户重录

# demuxer 白名单。**只用来在这几种里选一个,绝不等于放开 ffmpeg 自动探测**——
# 显式 `-f {fmt}` 仍是这条路径的安全基石(理由见 ffmpeg.voice_sample_cmd)。
AUDIO_FORMATS = frozenset({"webm", "ogg", "wav", "mp3", "mp4"})

# 魔数 → demuxer。**优先按内容判定,不认客户端声明的 content_type**:
# 图片那条路的注释白纸黑字写着"绝不信 content_type 或扩展名",音频这条此前却在信。
# 用户手选文件后更糟——浏览器按扩展名猜的 MIME 在不同系统上有 audio/x-wav、audio/wave、
# audio/mp3 等变体,合法文件会被随机拒掉;反过来攻击者也能靠声明 content_type 挑解析器。
_AUDIO_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"RIFF", "wav"),           # 还要再验 offset 8 是 WAVE,见 _sniff_audio
    (0, b"OggS", "ogg"),
    (0, b"\x1a\x45\xdf\xa3", "webm"),   # EBML,Matroska/WebM 共用
    (0, b"ID3", "mp3"),
    (4, b"ftyp", "mp4"),           # m4a/mp4:Safari 的 MediaRecorder 产出
)

# 仅在魔数认不出时兜底(极少数容器头部不规范的情况)。别名收全,免得同一个 wav
# 在 Windows 上报 audio/x-wav、在别处报 audio/wave 就被拒。
AUDIO_DEMUXERS = {
    "audio/webm": "webm", "video/webm": "webm",
    "audio/ogg": "ogg", "application/ogg": "ogg",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/x-mpeg-3": "mp3",
    "audio/mp4": "mp4", "audio/x-m4a": "mp4", "audio/aac": "mp4",
}


def _sniff_audio(raw: bytes) -> str | None:
    """按字节头认出 demuxer;认不出返回 None(交给 content_type 兜底)。"""
    for off, sig, fmt in _AUDIO_MAGIC:
        if raw[off:off + len(sig)] != sig:
            continue
        if fmt == "wav" and raw[8:12] != b"WAVE":
            continue          # RIFF 也可能是 avi/webp,必须再验一层
        return fmt
    # 无 ID3 标签的裸 mp3:帧同步 11 个 1(0xFF Ex/Fx),放在最后避免误伤其它格式
    if raw[:1] == b"\xff" and len(raw) > 1 and (raw[1] & 0xE0) == 0xE0:
        return "mp3"
    return None


async def read_limited_audio(file: UploadFile) -> bytes:
    """同 read_limited,只是上限与文案换成音频的。"""
    raw = bytearray()
    while chunk := await file.read(1 << 20):
        raw.extend(chunk)
        if len(raw) > MAX_AUDIO_BYTES:
            raise HTTPException(413, f"音频超过 {MAX_AUDIO_BYTES // 1024 // 1024} MiB 上限")
    return bytes(raw)


def to_voice_sample_wav(raw: bytes, content_type: str) -> bytes:
    """把上传的录音/音频文件净化成 16k 单声道 wav。与 to_reference_png 同一套哲学:
    绝不把客户端字节原样落盘,一律经 ffmpeg 重编码。

    格式判定**先看魔数、后看 content_type**(见 _AUDIO_MAGIC 上方的说明)。兜底那一路
    也要再过一次白名单——AUDIO_DEMUXERS 的值域本就在白名单内,这层校验是为了防止
    以后有人往表里加了一项却忘了同步 AUDIO_FORMATS。"""
    fmt = _sniff_audio(raw) \
        or AUDIO_DEMUXERS.get((content_type or "").split(";")[0].strip().lower())
    if fmt not in AUDIO_FORMATS:
        raise HTTPException(400, "无法识别的音频格式,支持 wav / mp3 / m4a,或直接用浏览器录音")
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / f"in.{fmt}", Path(td) / "out.wav"
        src.write_bytes(raw)
        try:
            ffmpeg.sh(ffmpeg.voice_sample_cmd(src, out, fmt))
        except RuntimeError as e:   # ffmpeg.sh 把非零退出包成 RuntimeError
            raise HTTPException(400, "音频无法解码,请换一个文件或重新录制") from e
        if not out.exists() or not out.stat().st_size:
            raise HTTPException(400, "音频无法解码,请换一个文件或重新录制")
        ms = ffmpeg.probe_duration_ms(out)
        if ms < MIN_VOICE_MS:
            raise HTTPException(400, f"音频太短({ms / 1000:.1f} 秒),至少需要 "
                                     f"{MIN_VOICE_MS // 1000} 秒才能克隆出像的音色")
        return out.read_bytes()


# 用户保存的 BGM 的最短时长。**不是随手定的**:混音链的 afade 只在整条流最开头淡入一次,
# 而 -stream_loop 的循环接缝处**没有任何交叉淡化**——每绕一圈就是一个硬切。AI 生成的 60 秒
# 曲子放进 85 秒的片子只接一次缝,所以这毛病一直没被注意到;存一段 10 秒的短 loop 进 5 分钟
# 的片子就是三十次硬切。20 秒把最难听的一档挡在门外(接缝交叉淡化要改滤镜链,是另一件事)。
MIN_BGM_MS = 20_000


def to_bgm_mp3(raw: bytes, content_type: str) -> bytes:
    """把上传的音频净化成规范的 BGM mp3(44.1k 立体声)。骨架同 to_voice_sample_wav——
    魔数优先的格式判定、白名单双层校验、一律经 ffmpeg 重编码,四条安全基线一条不少。

    **但绝不能直接复用 to_voice_sample_wav**:它输出 16k 单声道 pcm,那是喂 TTS 做声纹
    提取的规格,拿来处理音乐等于毁掉它。差异集中在 ffmpeg.bgm_upload_cmd 里。"""
    fmt = _sniff_audio(raw) \
        or AUDIO_DEMUXERS.get((content_type or "").split(";")[0].strip().lower())
    if fmt not in AUDIO_FORMATS:
        raise HTTPException(400, "无法识别的音频格式,支持 wav / mp3 / m4a")
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / f"in.{fmt}", Path(td) / "out.mp3"
        src.write_bytes(raw)
        try:
            ffmpeg.sh(ffmpeg.bgm_upload_cmd(src, out, fmt))
        except RuntimeError as e:   # ffmpeg.sh 把非零退出与超时都包成 RuntimeError
            raise HTTPException(400, "音频无法解码,请换一个文件") from e
        if not out.exists() or not out.stat().st_size:
            raise HTTPException(400, "音频无法解码,请换一个文件")
        ms = ffmpeg.probe_duration_ms(out)
        if ms < MIN_BGM_MS:
            raise HTTPException(400, f"配乐太短({ms / 1000:.1f} 秒),至少需要 "
                                     f"{MIN_BGM_MS // 1000} 秒——太短的片段循环起来接缝很明显")
        return out.read_bytes()


def bgm_rel_path() -> str:
    """BGM 库文件名,带随机盐。理由同 voice_sample_rel_path:文件名不该可推导。"""
    return f"bgm_{secrets.token_urlsafe(16)}.mp3"


def voice_sample_rel_path() -> str:
    """参考录音的相对路径,带随机盐。理由与 reference_rel_path 相同且更强:
    `/files` 静态挂载没有身份校验,而这是用户**真人声音**,敏感度不低于照片。"""
    return f"vs_{secrets.token_urlsafe(16)}.wav"


def reference_rel_path() -> str:
    """参考图的相对路径由服务端决定,且**带随机盐**。

    绝不用角色名或客户端文件名拼路径:中文名不进路径,`../`、`/`、NUL 更没有可乘之机。
    而用角色名的哈希也不行——`/files` 静态挂载没有身份校验(它托管的一直是 AI 生成的产物,
    泄露成本低),而角色名在项目详情里是公开可见的、sha1 无盐,等于把用户上传的**真人照片**
    放在一个可推导的公开 URL 上。加随机盐后,不拿到 project.json 里的路径就猜不出来。
    已知限制:这仍是"靠 URL 保密",链接一旦泄漏就永久有效——真要严格隔离得让参考图走
    带鉴权的端点,那是更大的改动,本次按用户决定先用随机盐。"""
    return f"characters/refs/ref_{secrets.token_urlsafe(16)}.png"


def atomic_write(path: Path, data: bytes) -> None:
    """同目录 .tmp + os.replace,沿用仓库既有做法(见 s4_pages._downscaled_ref)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
