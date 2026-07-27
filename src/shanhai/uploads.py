"""用户上传文件的接收与净化(目前只有角色参考图)。

这是全仓第一处接收「用户提供的字节」的地方,所以校验从严:客户端声明的
content_type、文件名、扩展名一律不信,只信解码结果;并且**绝不把客户端字节原样落盘**,
一律重新编码成 PNG。
"""

import os
import secrets
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps

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
