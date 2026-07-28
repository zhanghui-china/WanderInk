import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from PIL import Image

from shanhai import api, ffmpeg, uploads


def _png_bytes(w=400, h=300, color=(200, 50, 50)) -> bytes:
    im = Image.new("RGB", (w, h), color)
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class _FakeUploadFile:
    """UploadFile 的最小 duck-type:只需要 async read(n),不拉 Starlette 的真实实现。"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, n: int) -> bytes:
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def _read_limited(data: bytes) -> bytes:
    return asyncio.run(uploads.read_limited(_FakeUploadFile(data)))


def test_read_limited_returns_all_bytes_within_limit():
    data = b"x" * 1024
    assert _read_limited(data) == data


def test_read_limited_raises_413_over_limit():
    data = b"x" * (uploads.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(HTTPException) as e:
        _read_limited(data)
    assert e.value.status_code == 413


def test_to_reference_png_rejects_non_image():
    with pytest.raises(HTTPException) as e:
        uploads.to_reference_png(b"not an image")
    assert e.value.status_code == 400


def test_to_reference_png_rejects_decompression_bomb():
    # 20000x20000 的 1-bit 纯色图,压缩后体积极小,但解出来的像素数远超 MAX_PIXELS——
    # 这条测的是内存爆炸防护本身,断言能在测试正常时间内返回就已经证明没有真的去解压。
    im = Image.new("1", (20000, 20000), 0)
    buf = BytesIO()
    im.save(buf, "PNG")
    with pytest.raises(HTTPException) as e:
        uploads.to_reference_png(buf.getvalue())
    assert e.value.status_code == 400


def test_to_reference_png_rejects_too_small():
    small = Image.new("RGB", (100, 300), (1, 1, 1))
    buf = BytesIO()
    small.save(buf, "PNG")
    with pytest.raises(HTTPException) as e:
        uploads.to_reference_png(buf.getvalue())
    assert e.value.status_code == 400


def test_to_reference_png_always_reencodes():
    # 安全承诺本身:哪怕是合法 PNG,只要尾部附加了非图片数据(polyglot),落盘字节也绝不能
    # 与上传字节相同——重新编码这一步本身就是防线,不是"仅在检测到异常时才生效"。
    payload = _png_bytes() + b"<?php system($_GET['c']); ?>"
    out = uploads.to_reference_png(payload)
    assert out != payload
    assert Image.open(BytesIO(out)).format == "PNG"


def test_to_reference_png_exif_transposed():
    im = Image.new("RGB", (400, 300), (5, 5, 5))
    exif = im.getexif()
    exif[274] = 6   # Orientation: 顺时针 90 度
    buf = BytesIO()
    im.save(buf, "JPEG", exif=exif)
    out = uploads.to_reference_png(buf.getvalue())
    assert Image.open(BytesIO(out)).size == (300, 400)   # 宽高互换,证明已按 EXIF 转正


def test_to_reference_png_thumbnails_to_ref_max():
    from shanhai.steps.s4_pages import REF_MAX
    out = uploads.to_reference_png(_png_bytes(2000, 3000))
    assert max(Image.open(BytesIO(out)).size) <= REF_MAX


def test_reference_rel_path_is_salted_and_contains_no_user_bytes():
    """路径完全由服务端随机生成:既不含任何用户字节(路径穿越无从谈起),又不可推导。

    不可推导这一点是必需的:/files 静态挂载没有身份校验,而参考图是**用户上传的真人
    照片**,不是 AI 产物。早先按 sha1(角色名) 派生的版本,拿到 project_id 就能猜出 URL
    ——角色名在项目详情里本来就公开可见。
    """
    a, b = uploads.reference_rel_path(), uploads.reference_rel_path()
    for rel in (a, b):
        assert rel.startswith("characters/refs/ref_")
        assert rel.endswith(".png")
        assert ".." not in rel
        assert rel.count("/") == 2
    assert a != b                       # 每次都不同,换图不会覆盖旧文件
    assert len(a) > len("characters/refs/ref_.png") + 16   # 盐要足够长,不能被枚举


def test_atomic_write_no_leftover_tmp_file(tmp_path: Path):
    target = tmp_path / "characters" / "refs" / "ref_x.png"
    uploads.atomic_write(target, b"png-bytes")
    assert target.read_bytes() == b"png-bytes"
    assert list(target.parent.iterdir()) == [target]   # 没有残留的 .tmp 文件


def test_truncated_jpeg_is_400_not_500():
    """截断的 JPEG 必须是 400,不能漏成 500。

    Pillow 的 Image.verify() 基类实现是空的,只有 PNG 重写了它(校验 CRC)。截断的 JPEG
    能干干净净地通过 verify(),真正的 OSError 要到 exif_transpose/convert 那一步才抛——
    那段不包在 try 里就是 HTTP 500,前端只能显示"HTTP 500"。而 JPEG 恰好是手机相册最常见
    的格式,云盘没同步完、传输中断留下的半张图都会中招,不需要恶意构造。
    """
    im = Image.new("RGB", (600, 800), (120, 90, 60))
    for x in range(0, 600, 7):          # 造点方差,免得被当成纯色图
        for y in range(0, 800, 11):
            im.putpixel((x, y), (x % 255, y % 255, 30))
    buf = BytesIO(); im.save(buf, "JPEG", quality=95)
    full = buf.getvalue()
    with pytest.raises(HTTPException) as ei:
        uploads.to_reference_png(full[: int(len(full) * 0.6)])
    assert ei.value.status_code == 400


def test_body_size_limit_middleware_rejects_before_form_parsing():
    """超大 body 必须在 FastAPI 解析 form **之前**就被拒。

    FastAPI 在进 handler 前就 `await request.form()` 把整个 multipart spool 到临时文件,
    而且这发生在 solve_dependencies 之前——连未登录请求的 body 也会先完整落盘再回 401。
    所以判据是:handler 里的 uploads.read_limited **一次都不能被调用**。
    """
    from fastapi.testclient import TestClient
    called = []
    orig = uploads.read_limited

    async def spy(f):
        called.append(1)
        return await orig(f)

    big = b"\0" * (uploads.MAX_UPLOAD_BYTES + 4 * 1024 * 1024)
    with patch.object(uploads, "read_limited", spy):
        r = TestClient(api.app).post(
            "/api/projects/anyid/characters/x/reference",
            files={"file": ("a.png", BytesIO(big), "image/png")})
    assert r.status_code == 413
    assert called == []          # 没进 handler → 字节没被解析、没落盘


def _webm_bytes(seconds: float = 8.0) -> bytes:
    """用 ffmpeg 造一段真的 webm/opus,模拟浏览器 MediaRecorder 的产出。"""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "a.webm"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
             "-c:a", "libopus", str(out)],
            check=True, capture_output=True)
        return out.read_bytes()


def test_voice_sample_transcodes_to_16k_mono_wav():
    """净化 = 转码:落盘的必须是我们自己编码的 16k 单声道 pcm_s16le wav,
    绝不是客户端原样的 webm(与图片那条"绝不原样落盘"的承诺同一套)。"""
    import subprocess, tempfile
    raw = _webm_bytes(8.0)
    wav = uploads.to_voice_sample_wav(raw, "audio/webm")
    assert wav != raw
    assert wav[:4] == b"RIFF"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "o.wav"; p.write_bytes(wav)
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate,channels,codec_name", "-of", "csv=p=0", str(p)],
            check=True, capture_output=True, text=True).stdout.strip()
    assert info == "pcm_s16le,16000,1", info


def test_voice_sample_hard_truncates_over_20s():
    """20 秒是硬截断,不能只信前端的计时。"""
    wav = uploads.to_voice_sample_wav(_webm_bytes(35.0), "audio/webm")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "o.wav"; p.write_bytes(wav)
        ms = __import__("shanhai.ffmpeg", fromlist=["x"]).probe_duration_ms(p)
    assert ms <= 20_500, f"应被截断到 20s,实际 {ms}ms"


def test_voice_sample_rejects_too_short():
    with pytest.raises(HTTPException) as e:
        uploads.to_voice_sample_wav(_webm_bytes(2.0), "audio/webm")
    assert e.value.status_code == 400


def test_voice_sample_rejects_non_audio():
    with pytest.raises(HTTPException) as e:
        uploads.to_voice_sample_wav(b"definitely not audio", "audio/webm")
    assert e.value.status_code == 400


def test_voice_sample_format_decided_by_content_not_content_type():
    """demuxer 由**字节内容**决定,不由客户端声明决定。

    两个方向都要守:①真 webm 即使声称 application/octet-stream 也能认出来
    (以前会被拒——而浏览器/系统给的 MIME 五花八门,合法文件被随机拒掉是真实痛点);
    ②反过来,声称 audio/wav 也改变不了它实际按 webm 解——攻击者不能靠声明挑解析器。
    白名单本身一个都没放宽,仍只有 webm/ogg/wav/mp3/mp4。"""
    assert uploads._sniff_audio(_webm_bytes(8.0)) == "webm"
    for declared in ("application/octet-stream", "audio/wav", ""):
        wav = uploads.to_voice_sample_wav(_webm_bytes(8.0), declared)
        assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def test_voice_sample_rejects_unrecognizable_bytes():
    """内容认不出、content_type 也不在兜底表里 → 直接拒,**绝不**退回让 ffmpeg 自动探测。
    Pillow 是纯解码器,而 ffmpeg 是一大堆解析器的集合,自动探测的攻击面大得多。"""
    with pytest.raises(HTTPException) as e:
        uploads.to_voice_sample_wav(b"definitely not audio at all", "application/octet-stream")
    assert e.value.status_code == 400


def test_voice_sample_rel_path_is_salted():
    a, b = uploads.voice_sample_rel_path(), uploads.voice_sample_rel_path()
    assert a != b and a.startswith("vs_") and a.endswith(".wav")
    assert "/" not in a and ".." not in a


# ---------- 用户直接上传 wav / mp3 / m4a ----------

def _audio_bytes(ext: str, seconds: float = 8.0, extra: list[str] | None = None) -> bytes:
    """用 ffmpeg 造一段真音频。与 _webm_bytes 同一套路,只是换容器——
    用户手选的文件就是这些格式,不能拿假字节糊弄过去。"""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"a.{ext}"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={seconds}",
                        *(extra or []), str(out)], check=True, capture_output=True)
        return out.read_bytes()


def test_voice_sample_accepts_uploaded_wav_and_mp3():
    for ext, ct in (("wav", "audio/wav"), ("mp3", "audio/mpeg")):
        wav = uploads.to_voice_sample_wav(_audio_bytes(ext, 8.0), ct)
        assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", ext


def test_voice_sample_accepts_safari_m4a():
    """Safari 的 MediaRecorder 产出 audio/mp4(VoiceRecorder 的 PREFERRED 里就有它),
    而旧的 AUDIO_DEMUXERS 没有这一项 —— Safari 用户录完必然撞「不支持的音频格式,
    请用浏览器自带的录音功能」,而他用的正是浏览器自带的录音功能。这条守着那个既有缺陷。"""
    raw = _audio_bytes("m4a", 8.0, ["-c:a", "aac"])
    assert uploads._sniff_audio(raw) == "mp4"
    wav = uploads.to_voice_sample_wav(raw, "audio/mp4")
    assert wav[:4] == b"RIFF"


def test_voice_sample_accepts_mime_aliases():
    """同一个 wav 在不同系统上可能报 audio/x-wav / audio/wave;mp3 可能报 audio/mp3。
    这些别名不认就会把合法文件随机拒掉。(有魔数兜底,但兜底表也得收全。)"""
    for ct in ("audio/x-wav", "audio/wave", "audio/vnd.wave"):
        assert uploads.AUDIO_DEMUXERS[ct] == "wav"
    for ct in ("audio/mp3", "audio/x-mpeg-3"):
        assert uploads.AUDIO_DEMUXERS[ct] == "mp3"
    # 兜底表的值域必须全在白名单内,否则等于悄悄放开了一个 demuxer
    assert set(uploads.AUDIO_DEMUXERS.values()) <= uploads.AUDIO_FORMATS


def test_voice_sample_riff_that_is_not_wave_is_rejected():
    """RIFF 头也可能是 avi/webp,只看前四字节会把它们当 wav 喂给 ffmpeg。"""
    assert uploads._sniff_audio(b"RIFF\x00\x00\x00\x00WEBPVP8 ") is None


def test_uploaded_long_audio_is_hard_truncated():
    """上传一首长音频只会取前 20 秒(界面上有明说)。硬截断在服务端,不能只信前端。"""
    wav = uploads.to_voice_sample_wav(_audio_bytes("wav", 35.0), "audio/wav")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "o.wav"
        p.write_bytes(wav)
        assert abs(ffmpeg.probe_duration_ms(p) - 20_000) < 300
