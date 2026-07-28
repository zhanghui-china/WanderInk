import io
from pathlib import Path
from PIL import Image
from shanhai import typeset

def _art(size=(1536, 1024), color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()

def test_compose_page_frame_size(tmp_path: Path):
    out = tmp_path / "p.png"
    typeset.compose_page(_art(), out)
    assert Image.open(out).size == (1920, 1080)

def test_compose_page_portrait_fills_frame_no_dead_bars(tmp_path: Path):
    # cover-crop 满幅铺满:竖图四角均为清晰画面内容(无黑边、无压暗背景层)
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(600, 900), color=(255, 0, 0)), out)
    im = Image.open(out).convert("RGB")
    for x, y in [(0, 0), (1919, 0), (0, 300), (1919, 300)]:
        r, g, b = im.getpixel((x, y))
        assert r > 200 and g < 30 and b < 30       # 原色红,非死黑边、非压暗背景

def test_compose_page_landscape_fills_frame(tmp_path: Path):
    # 横图输入同样满幅铺满,无letterbox
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(2000, 800), color=(0, 0, 255)), out)
    im = Image.open(out).convert("RGB")
    assert im.size == (1920, 1080)
    for x, y in [(0, 0), (1919, 0), (0, 500), (1919, 500)]:
        r, g, b = im.getpixel((x, y))
        assert b > 200 and r < 30

def test_compose_page_vertical_anchor_favors_top(tmp_path: Path):
    # 裁切锚点偏上(约 40%):居中裁切本应切掉的头部标记条带,偏上锚点下应保留
    src = Image.new("RGB", (600, 900), "black")
    marker = Image.new("RGB", (600, 20), (255, 255, 0))
    src.paste(marker, (0, 230))                    # 源图第 230~250 行(头部区)
    buf = io.BytesIO()
    src.save(buf, "PNG")
    out = tmp_path / "p.png"
    typeset.compose_page(buf.getvalue(), out)
    im = Image.open(out).convert("RGB")
    r, g, b = im.getpixel((960, 48))               # 偏上锚点下,标记应映射到此处附近
    assert r > 200 and g > 200 and b < 50          # 黄色标记可见 = 未被居中裁切切掉

def test_compose_page_has_no_baked_subtitle(tmp_path: Path):
    # 字幕/水印已移到 overlay_layer:底图纯净,底部字幕区仍是原色画面,无白字/压暗遮罩
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(1920, 1080), color=(0, 120, 0)), out)
    im = Image.open(out).convert("RGB")
    caption_area = [im.getpixel((x, y))
                    for y in range(1080 - 100, 1080 - 10)
                    for x in range(200, 1720, 4)]
    assert all(p == (0, 120, 0) for p in caption_area)   # 无白字、无渐变遮罩,全是原图

def test_overlay_layer_transparent_top_gradient_and_text(tmp_path: Path):
    # overlay:上部完全透明(不遮画面),底部渐变+白字可辨,右上水印可见
    out = tmp_path / "o.png"
    typeset.overlay_layer("断桥初遇烟雨蒙蒙", out)
    im = Image.open(out)
    assert im.mode == "RGBA" and im.size == (1920, 1080)
    assert im.getpixel((960, 400))[3] == 0                # 上部透明,不遮画面
    assert im.getpixel((10, 1080 - 240 + 5))[3] > 0       # 底部渐变遮罩有 alpha
    caption_area = [im.getpixel((x, y))
                    for y in range(1080 - 100, 1080 - 10)
                    for x in range(200, 1720, 4)]
    assert any(p[0] > 200 and p[1] > 200 and p[2] > 200 and p[3] > 0
               for p in caption_area)                     # 白色字幕可辨
    watermark_area = [im.getpixel((x, y))
                      for y in range(20, 60)
                      for x in range(1600, 1900, 4)]
    assert any(p[3] > 0 for p in watermark_area)          # 右上水印像素存在

def test_title_and_credits(tmp_path: Path):
    typeset.title_card("雷峰塔", "白蛇传", tmp_path / "t.png")
    typeset.credits_card(["来源:《警世通言》", "本片为 AI 生成内容"], tmp_path / "c.png")
    assert Image.open(tmp_path / "t.png").size == (1920, 1080)
    assert Image.open(tmp_path / "c.png").size == (1920, 1080)


def test_overlay_burns_three_lines_for_long_caption(tmp_path: Path):
    """caption 上限放宽到 120 后,烧录必须能出三行——两行只装 84 字,超出部分会被
    _wrap(...)[:n] **静默吞掉**,导出的 PDF/ZIP 里就是半句话。这条守住那个耦合。

    判据用"最高的那行白像素在哪":字幕整体贴着底边往上排,y0 = 高 - 20 - 56×行数,
    两行是 948、三行是 892。数白像素"带"的条数不行——汉字笔画自带横向空隙,
    一行字会被数成好几条(实测 3 行数出 9 条)。"""
    out = tmp_path / "o.png"
    typeset.overlay_layer("字" * 120, out)
    im = Image.open(out).convert("RGBA")
    w, h = im.size
    top = next(y for y in range(h - typeset.CAPTION_GRAD_H, h)
               if any(im.getpixel((x, y))[:3] == (255, 255, 255) for x in range(120, w - 120, 4)))
    expected = h - 20 - 56 * 3
    assert abs(top - expected) < 20, f"字幕顶在 {top},三行应在 {expected} 附近(两行会是 {h - 20 - 56 * 2})"
