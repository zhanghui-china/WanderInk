import io

import pytest
from PIL import Image

from shanhai.paneling import compose_manga_page
from shanhai.schema import Panel
from shanhai.typeset import FRAME


def _solid(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color).save(buf, "PNG")
    return buf.getvalue()


def test_compose_manga_page_empty_raises():
    with pytest.raises(ValueError, match="没有可用"):
        compose_manga_page([], [])


def test_compose_manga_page_single_panel_fills_frame():
    img = compose_manga_page([_solid((255, 0, 0))], [Panel(visual_desc="v", shot_type="wide")])
    out = Image.open(io.BytesIO(img))
    assert out.size == FRAME
    r, g, b = out.getpixel((FRAME[0] // 2, FRAME[1] // 2))
    assert r > 200 and g < 50 and b < 50  # 唯一一格铺满全页,中心点应是红色


def test_compose_manga_page_four_panels_land_in_quadrants():
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    imgs = [_solid(c) for c in colors]
    panels = [Panel(visual_desc=f"v{i}", shot_type="medium") for i in range(4)]
    out = Image.open(io.BytesIO(compose_manga_page(imgs, panels)))
    assert out.size == FRAME
    w, h = FRAME
    points = [(w // 4, h // 4), (3 * w // 4, h // 4), (w // 4, 3 * h // 4), (3 * w // 4, 3 * h // 4)]
    for (x, y), expect in zip(points, colors):
        assert out.getpixel((x, y)) == expect  # 纯色格子缩放/裁切后中心点应仍是原色


def test_compose_manga_page_insert_overlays_host():
    host = _solid((10, 10, 10))
    insert = _solid((255, 255, 255))
    panels = [Panel(visual_desc="host", shot_type="wide"),
              Panel(visual_desc="closeup", shot_type="insert")]
    out = Image.open(io.BytesIO(compose_manga_page([host, insert], panels)))
    w, h = FRAME
    assert out.getpixel((40, 40)) == (10, 10, 10)          # 宿主格左上角未被叠加覆盖
    near = out.getpixel((w - 150, h - 150))
    assert near[0] > 200 and near[1] > 200 and near[2] > 200  # 宿主格右下角能采到叠加的白色特写


def test_compose_manga_page_lone_insert_falls_back_to_full_page():
    # 唯一一格标了 insert 时没有宿主格可叠加,应退化为普通铺满整页,不报错
    img = compose_manga_page([_solid((0, 200, 0))], [Panel(visual_desc="v", shot_type="insert")])
    out = Image.open(io.BytesIO(img))
    assert out.size == FRAME
    assert out.getpixel((FRAME[0] // 2, FRAME[1] // 2))[1] > 150
