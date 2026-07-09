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
    typeset.compose_page(_art(), "西湖烟雨,断桥初遇。", out)
    assert Image.open(out).size == (1920, 1080)

def test_compose_page_portrait_fills_frame_no_dead_bars(tmp_path: Path):
    # cover-crop 满幅铺满:竖图四角均为清晰画面内容(无黑边、无压暗背景层)
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(600, 900), color=(255, 0, 0)), "文案", out)
    im = Image.open(out).convert("RGB")
    for x, y in [(0, 0), (1919, 0), (0, 300), (1919, 300)]:
        r, g, b = im.getpixel((x, y))
        assert r > 200 and g < 30 and b < 30       # 原色红,非死黑边、非压暗背景

def test_compose_page_landscape_fills_frame(tmp_path: Path):
    # 横图输入同样满幅铺满,无letterbox
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(2000, 800), color=(0, 0, 255)), "文案", out)
    im = Image.open(out).convert("RGB")
    assert im.size == (1920, 1080)
    for x, y in [(0, 0), (1919, 0), (0, 500), (1919, 500)]:   # 避开底部渐变字幕区
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
    typeset.compose_page(buf.getvalue(), "文案", out)
    im = Image.open(out).convert("RGB")
    r, g, b = im.getpixel((960, 48))               # 偏上锚点下,标记应映射到此处附近
    assert r > 200 and g > 200 and b < 50          # 黄色标记可见 = 未被居中裁切切掉

def test_compose_page_caption_gradient_legible(tmp_path: Path):
    # 底部渐变字幕:遮罩非纯黑(可透见画面)且叠加的白字清晰可辨
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(1920, 1080), color=(0, 120, 0)), "断桥初遇烟雨蒙蒙", out)
    im = Image.open(out).convert("RGB")
    r, g, b = im.getpixel((10, 1080 - 240 + 5))    # 渐变区顶部(靠近画面,遮罩最浅)
    assert g > 20                                  # 仍透出底图绿色,非纯黑遮罩
    caption_area = [im.getpixel((x, y))
                     for y in range(1080 - 100, 1080 - 10)
                     for x in range(200, 1720, 4)]
    assert any(p[0] > 200 and p[1] > 200 and p[2] > 200 for p in caption_area)  # 白字可辨

def test_title_and_credits(tmp_path: Path):
    typeset.title_card("雷峰塔", "白蛇传", tmp_path / "t.png")
    typeset.credits_card(["来源:《警世通言》", "本片为 AI 生成内容"], tmp_path / "c.png")
    assert Image.open(tmp_path / "t.png").size == (1920, 1080)
    assert Image.open(tmp_path / "c.png").size == (1920, 1080)
