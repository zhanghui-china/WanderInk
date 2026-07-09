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

def test_compose_page_portrait_no_dead_bars(tmp_path: Path):
    # 竖图不应留死黑边:两侧应是同图模糊填充(压暗的红),而非纯黑
    out = tmp_path / "p.png"
    typeset.compose_page(_art(size=(600, 900), color="red"), "文案", out)
    im = Image.open(out).convert("RGB")
    r, g, b = im.getpixel((10, 460))                 # 左侧边缘、画面区内
    assert r > 20 and g < 30 and b < 30              # 暗红填充,非纯黑

def test_title_and_credits(tmp_path: Path):
    typeset.title_card("雷峰塔", "白蛇传", tmp_path / "t.png")
    typeset.credits_card(["来源:《警世通言》", "本片为 AI 生成内容"], tmp_path / "c.png")
    assert Image.open(tmp_path / "t.png").size == (1920, 1080)
    assert Image.open(tmp_path / "c.png").size == (1920, 1080)
