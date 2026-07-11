import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
FRAME = (1920, 1080)
CAPTION_GRAD_H = 240  # 底部渐变字幕高度
CAPTION_ANCHOR_Y = 0.4  # cover-crop 垂直锚点:偏上,优先保住人物头部
WATERMARK = "AI 生成"


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def _cover(img: Image.Image, size: tuple[int, int], anchor_y: float = 0.5) -> Image.Image:
    """缩放并裁剪填满 size(cover-fit)。anchor_y 为垂直裁切锚点(0=保留顶部,1=保留底部)。"""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    resized = img.resize((max(round(img.width * scale), tw), max(round(img.height * scale), th)))
    left = (resized.width - tw) // 2
    top = round((resized.height - th) * anchor_y)
    return resized.crop((left, top, left + tw, top + th))


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def compose_page(art: bytes, out: Path) -> None:
    # 只产出 cover-crop 满幅底图(锚点偏上保住头部);字幕/水印移到 overlay_layer,
    # 由 ffmpeg 作为静态层叠加,使 Ken Burns 只推拉底图、字幕/水印保持不动。
    img = Image.open(io.BytesIO(art)).convert("RGB")
    frame = _cover(img, FRAME, anchor_y=CAPTION_ANCHOR_Y)
    frame.save(out)


def overlay_image(caption: str) -> Image.Image:
    # 1920×1080 透明画布:仅底部渐变遮罩 + 白色字幕 + 右上"AI 生成"水印。
    # 上部完全透明,合成时不遮画面;整层静态,不随 Ken Burns 推拉。
    layer = Image.new("RGBA", FRAME, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    # 底部半透明渐变遮罩(透明→半透明黑,越往下越暗),承载字幕但不切走画面
    grad_top = FRAME[1] - CAPTION_GRAD_H
    for i in range(CAPTION_GRAD_H):
        alpha = round(200 * i / (CAPTION_GRAD_H - 1))
        draw.line([(0, grad_top + i), (FRAME[0], grad_top + i)], fill=(0, 0, 0, alpha))
    font = _font(40)
    lines = _wrap(caption, font, FRAME[0] - 240)[:2]
    line_h = 56
    y0 = FRAME[1] - 20 - line_h * len(lines)
    for i, line in enumerate(lines):
        w = font.getlength(line)
        draw.text(((FRAME[0] - w) / 2, y0 + i * line_h), line, font=font, fill="white",
                  stroke_width=2, stroke_fill="black")
    wm_font = _font(28)
    # 深色描边保证 AI 标识在留白/浅色画面上始终可见(合规:标识不可失效)
    draw.text((FRAME[0] - wm_font.getlength(WATERMARK) - 24, 20), WATERMARK,
              font=wm_font, fill=(255, 255, 255, 180),
              stroke_width=2, stroke_fill=(0, 0, 0, 160))
    return layer


def overlay_layer(caption: str, out: Path) -> None:
    overlay_image(caption).save(out)


def _text_card(lines: list[str], sizes: list[int], out: Path) -> None:
    frame = Image.new("RGB", FRAME, "black")
    draw = ImageDraw.Draw(frame)
    total_h = sum(sizes) + 30 * (len(lines) - 1)
    y = (FRAME[1] - total_h) / 2
    for line, size in zip(lines, sizes):
        font = _font(size)
        draw.text(((FRAME[0] - font.getlength(line)) / 2, y), line, font=font, fill="white")
        y += size + 30
    frame.save(out)


def title_card(title: str, subtitle: str, out: Path) -> None:
    _text_card([title, subtitle], [88, 52], out)


def credits_card(lines: list[str], out: Path) -> None:
    _text_card(lines, [36] * len(lines), out)
