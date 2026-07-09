import io
from pathlib import Path
from PIL import Image
from shanhai import typeset

def _art() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1536, 1024), "red").save(buf, "PNG")
    return buf.getvalue()

def test_compose_page_frame_size(tmp_path: Path):
    out = tmp_path / "p.png"
    typeset.compose_page(_art(), "西湖烟雨,断桥初遇。", out)
    assert Image.open(out).size == (1920, 1080)

def test_title_and_credits(tmp_path: Path):
    typeset.title_card("雷峰塔", "白蛇传", tmp_path / "t.png")
    typeset.credits_card(["来源:《警世通言》", "本片为 AI 生成内容"], tmp_path / "c.png")
    assert Image.open(tmp_path / "t.png").size == (1920, 1080)
    assert Image.open(tmp_path / "c.png").size == (1920, 1080)
