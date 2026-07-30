import io

import pytest
from PIL import Image

from shanhai.paneling import (GUTTER, LAYOUTS, PANEL_ANCHOR_Y, compose_manga_page,
                              regular_slots, slot_sizes)
from shanhai.schema import Panel
from shanhai.typeset import FRAME, cover


def _solid(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color).save(buf, "PNG")
    return buf.getvalue()


# 上部一条色带 + 下部另一色的构造图,用来验证"垂直裁切保住了画面上部"。
# 纯色方块(_solid)对裁切完全免疫——裁掉多少中心点颜色都不变,这是原有 4 条用例的盲区,
# 本次"人脸被切"的 bug 正是它们全绿也没拦住的。
TOP_BAND = (255, 0, 0)
BOTTOM = (0, 0, 255)


def _banded(top_frac: float = 0.2, size: tuple[int, int] = (1536, 1024)) -> bytes:
    img = Image.new("RGB", size, BOTTOM)
    band_h = round(size[1] * top_frac)
    img.paste(Image.new("RGB", (size[0], band_h), TOP_BAND), (0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _has_color(img: Image.Image, box: tuple[int, int, int, int], want: tuple[int, int, int]) -> bool:
    """box 区域内是否出现过 want 这个颜色(容差 30,兼容缩放插值)。"""
    colors = img.crop(box).convert("RGB").getcolors(maxcolors=1 << 24) or []
    return any(all(abs(c - w) < 30 for c, w in zip(px, want)) for _cnt, px in colors)


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
    host, second, insert = _solid((10, 10, 10)), _solid((0, 0, 200)), _solid((255, 255, 255))
    panels = [Panel(visual_desc="host", shot_type="wide"),
              Panel(visual_desc="second", shot_type="medium"),
              Panel(visual_desc="closeup", shot_type="insert")]
    out = Image.open(io.BytesIO(compose_manga_page([host, second, insert], panels)))
    assert out.getpixel((40, 40)) == (10, 10, 10)          # 宿主格左上角未被叠加覆盖
    # 宿主格是版式模板第一格(LAYOUTS[2] 的上半),叠加落在它的右下角
    hx1, hy1 = FRAME[0], FRAME[1] // 2
    near = out.getpixel((hx1 - 150, hy1 - 150))
    assert near[0] > 200 and near[1] > 200 and near[2] > 200  # 能采到叠加的白色特写


def test_compose_manga_page_lone_insert_falls_back_to_full_page():
    # 唯一一格标了 insert 时没有宿主格可叠加,应退化为普通铺满整页,不报错
    img = compose_manga_page([_solid((0, 200, 0))], [Panel(visual_desc="v", shot_type="insert")])
    out = Image.open(io.BytesIO(img))
    assert out.size == FRAME
    assert out.getpixel((FRAME[0] // 2, FRAME[1] // 2))[1] > 150


def test_two_panels_with_insert_render_as_two_real_slots():
    """[medium, insert] 曾经排成"整页铺满 + 占半张页的叠加格":宿主格就是整页,而叠加
    尺寸是宿主格的 INSET_SCALE。那不是漫画的嵌入式特写,是被压在主图上的第二格——
    线上 f50b97f4 第 10 页正是如此,用户反馈"分格有问题"、数分格页时也数不到它。
    扣掉 insert 后剩不下 2 个常规格,就把 insert 降为普通格,排成真正的上下两格。"""
    panels = [Panel(visual_desc="a", shot_type="medium"),
              Panel(visual_desc="b", shot_type="insert")]
    assert regular_slots(panels) == 2
    out = Image.open(io.BytesIO(compose_manga_page([_solid((200, 0, 0)), _solid((0, 0, 200))],
                                                   panels)))
    w, h = FRAME
    assert out.getpixel((w // 2, h // 4))[0] > 150      # 上半格是红
    assert out.getpixel((w // 2, 3 * h // 4))[2] > 150  # 下半格是蓝


def test_regular_slots_counts_layout_slots_not_panel_count():
    """"分格了没有"要按独立版位数判断,不能按 len(panels)——S2 的分格下限卡的就是这个数。"""
    assert regular_slots([]) == 0
    assert regular_slots([Panel(visual_desc="a", shot_type="insert")]) == 1   # 孤立 insert 退化为整页
    assert regular_slots([Panel(visual_desc="a", shot_type="wide"),
                          Panel(visual_desc="b", shot_type="insert")]) == 2   # insert 降为普通格
    three = [Panel(visual_desc="a", shot_type="wide"), Panel(visual_desc="b", shot_type="medium"),
             Panel(visual_desc="c", shot_type="insert")]
    assert regular_slots(three) == 2      # 3 格里有 insert → 只占 2 个版位,少于 len(panels)


# ---------- 裁切保头(原有用例的盲区) ----------

# 生产里每格现在按版位比例出图,后端能给的最宽画幅是 21:9,所以极端长条版位收到的
# 是 21:9 而不是从前那种 3:2——下面两条用这个尺寸建模真实行为。
_ULTRAWIDE = (1908, 818)


def test_extreme_band_layout_keeps_top_of_image():
    """2 格横条是裁切最狠的版式(版位比 3.6:1)。配合按版位出图 + 保头锚点,
    画面上部(人物头顶所在)必须活下来。"""
    panels = [Panel(visual_desc=f"v{i}", shot_type="medium") for i in range(2)]
    imgs = [_banded(size=_ULTRAWIDE), _banded(size=_ULTRAWIDE)]
    out = Image.open(io.BytesIO(compose_manga_page(imgs, panels)))
    w, h = FRAME
    # 第一格占上半页,它内部的顶部色带应出现在这一格的上沿附近
    assert _has_color(out, (0, GUTTER, w, h // 4), TOP_BAND), "上部色带被裁掉了,保头锚点未生效"


def test_three_panel_banner_keeps_top_of_image():
    """3 格版式的顶部通栏同样极端(3.28:1)。"""
    panels = [Panel(visual_desc=f"v{i}", shot_type="medium") for i in range(3)]
    imgs = [_banded(size=_ULTRAWIDE), _solid((0, 255, 0)), _solid((255, 255, 0))]
    out = Image.open(io.BytesIO(compose_manga_page(imgs, panels)))
    w, h = FRAME
    assert _has_color(out, (0, GUTTER, w, round(h * 0.2)), TOP_BAND), "顶部通栏把画面上部裁掉了"


def test_anchor_keeps_more_top_than_center_crop():
    """已知限制的回归守卫:某格生成失败会让版式降级,届时图的比例与版位又对不上,
    裁切仍然很狠、上部未必保得住。此时保头锚点做不到全保,但**必须严格优于居中裁**——
    这条锁的就是"别哪天有人把 anchor 改回 0.5"。"""
    assert PANEL_ANCHOR_Y < 0.5, "锚点必须偏上才能保头"
    # 垂直渐变图:像素亮度随 y 单调递增,于是"裁切窗口取自源图哪个高度"可以直接从
    # 结果的平均亮度读出来。比用色带断言稳健得多——不依赖任何具体裁切像素数。
    h = 1024
    grad = Image.new("L", (1536, h))
    grad.putdata([round(255 * (y / (h - 1))) for y in range(h) for _ in range(1536)])
    src = grad.convert("RGB")
    slot = slot_sizes([Panel(visual_desc="v", shot_type="medium")] * 2)[0]

    def _mean(anchor: float) -> float:
        px = list(cover(src, slot, anchor_y=anchor).convert("L").getdata())
        return sum(px) / len(px)

    assert _mean(PANEL_ANCHOR_Y) < _mean(0.5), \
        "保头锚点没有把裁切窗口上移(取到的画面不比居中裁更靠上)"


# ---------- slot_sizes 与实际版位必须同源 ----------

@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_slot_sizes_match_layout_rects(n: int):
    """slot_sizes 是"生成前按什么比例出图"的依据,compose_manga_page 是"实际塞进多大版位"。
    两者一旦漂移,裁切量就失控——这是本方案最大的维护风险,必须锁死。"""
    panels = [Panel(visual_desc=f"v{i}", shot_type="medium") for i in range(n)]
    sizes = slot_sizes(panels)
    assert len(sizes) == n
    w, h = FRAME
    for size, rect in zip(sizes, LAYOUTS[n]):
        x0, y0, x1, y1 = rect
        expect = (round(x1 * w) - round(x0 * w) - GUTTER,
                  round(y1 * h) - round(y0 * h) - GUTTER)
        assert size == expect


def test_slot_sizes_gives_insert_its_inset_size():
    # insert 格不占版位,它叠加在宿主格右下角,尺寸应明显小于宿主格。
    # 用三格:扣掉 insert 后要剩得下 2 个常规格,这个叠加才成立(见 _plan 的说明)。
    panels = [Panel(visual_desc="host", shot_type="wide"),
              Panel(visual_desc="second", shot_type="medium"),
              Panel(visual_desc="face", shot_type="insert")]
    host_size, _second, insert_size = slot_sizes(panels)
    assert insert_size[0] < host_size[0] and insert_size[1] < host_size[1]


def test_slot_sizes_lone_insert_falls_back_to_full_page():
    # 与 compose_manga_page 的退化逻辑保持一致:唯一一格标 insert 时按整页算
    only = slot_sizes([Panel(visual_desc="v", shot_type="insert")])
    assert only == slot_sizes([Panel(visual_desc="v", shot_type="wide")])


def test_slot_sizes_two_panels_with_insert_are_two_equal_slots():
    """与 compose 的降级保持一致:[medium, insert] 按上下两格出图,而不是"整页 + 半页叠加"。
    生成时的尺寸与合成时的版位必须同源(_plan),否则裁切量失控。"""
    with_insert = slot_sizes([Panel(visual_desc="a", shot_type="medium"),
                              Panel(visual_desc="b", shot_type="insert")])
    plain = slot_sizes([Panel(visual_desc="a", shot_type="medium"),
                        Panel(visual_desc="b", shot_type="wide")])
    assert with_insert == plain
