import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path("assets/fonts/NotoSansCJKsc-Regular.otf")
FRAME = (1920, 1080)
CAPTION_H = 160
WATERMARK = "AI 生成"


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


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


def compose_page(art: bytes, caption: str, out: Path) -> None:
    frame = Image.new("RGB", FRAME, "black")
    img = Image.open(io.BytesIO(art)).convert("RGB")
    area_h = FRAME[1] - CAPTION_H
    img.thumbnail((FRAME[0], area_h))
    frame.paste(img, ((FRAME[0] - img.width) // 2, (area_h - img.height) // 2))
    draw = ImageDraw.Draw(frame, "RGBA")
    draw.rectangle([0, area_h, FRAME[0], FRAME[1]], fill=(0, 0, 0, 200))
    font = _font(40)
    lines = _wrap(caption, font, FRAME[0] - 240)[:2]
    for i, line in enumerate(lines):
        w = font.getlength(line)
        draw.text(((FRAME[0] - w) / 2, area_h + 24 + i * 56), line, font=font, fill="white")
    wm_font = _font(28)
    # 深色描边保证 AI 标识在留白/浅色画面上始终可见(合规:标识不可失效)
    draw.text((FRAME[0] - wm_font.getlength(WATERMARK) - 24, 20), WATERMARK,
              font=wm_font, fill=(255, 255, 255, 180),
              stroke_width=2, stroke_fill=(0, 0, 0, 160))
    frame.save(out)


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
