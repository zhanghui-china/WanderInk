"""日式分格漫画排版:纯 PIL 合成,无网络依赖,可完全离线单测。
把 N 张独立生成的格子图按预定义版式拼成一整页;shot_type=="insert" 的格子
做圆角裁剪叠加在其它格子上面(漫画常见的嵌入式特写手法)。"""
import io

from PIL import Image, ImageDraw

from shanhai.schema import Panel
from shanhai.typeset import FRAME, cover

GUTTER = 12  # 格间装订线宽度(像素)
BORDER = (20, 16, 12)  # 画布底色 = 装订线颜色(深墨色)
INSET_SCALE = 0.55  # 特写叠加格相对宿主格的边长比例
INSET_MARGIN = 24  # 特写叠加格距宿主格边缘的留白(像素)
INSET_RADIUS = 24  # 特写叠加格圆角半径(像素)
OUTLINE_COLOR = (245, 240, 228)  # 特写叠加格描边颜色(米宣纸色)
OUTLINE_WIDTH = 6  # 特写叠加格描边宽度(像素)
# 每格塞进版位时的垂直裁切锚点:偏上,让残余裁切吃掉画面下部而不是人物头顶。
# 与 typeset.CAPTION_ANCHOR_Y 同为 0.4 但语义不同(那个是给字幕排版用的),故各自命名。
# 本次问题正是因为这里原先写死居中裁切(anchor 0.5),头顶被切在画框边缘。
PANEL_ANCHOR_Y = 0.4

# 每种"常规格数"(不含 insert 格)对应的归一化矩形列表 (x0, y0, x1, y1),0~1 比例坐标。
LAYOUTS: dict[int, list[tuple[float, float, float, float]]] = {
    1: [(0.0, 0.0, 1.0, 1.0)],
    2: [(0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 1.0)],
    3: [(0.0, 0.0, 1.0, 0.55), (0.0, 0.55, 0.5, 1.0), (0.5, 0.55, 1.0, 1.0)],
    4: [(0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 1.0, 0.5), (0.0, 0.5, 0.5, 1.0), (0.5, 0.5, 1.0, 1.0)],
}


def _rect_px(rect: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    w, h = FRAME
    return (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))


def _plan(panels: list[Panel]) -> tuple[int | None, list[int], list[tuple[float, float, float, float]]]:
    """版位规划:返回 (insert 格下标或 None, 常规格在 panels 里的下标列表, 版式矩形列表)。

    compose_manga_page 与 slot_sizes 都走这一个函数——两处各算一遍 insert 判定和
    LAYOUTS 查表迟早会漂移,而"生成时按什么比例出图"和"合成时塞进什么版位"一旦不一致,
    裁切量就失控(这正是本次人脸被裁的一半原因)。"""
    n = len(panels)
    insert_idx = next((i for i, p in enumerate(panels) if p.shot_type == "insert"), None)
    # 扣掉 insert 后至少要剩 2 个常规格,这个叠加才成立:
    # - n == 1:没有宿主格可叠加,退化为普通整页。
    # - n == 2(如 [medium, insert]):宿主格就是整页,而叠加尺寸是宿主格的 INSET_SCALE,
    #   于是"小格"占掉半张页——那不是漫画的嵌入式特写,是被压在主图上的第二格。线上
    #   f50b97f4 第 10 页正是如此:用户数分格页时数不到它,还反馈"分格有问题"。
    #   降为普通格排成上下两格,这一页才真的看得出分格。
    if insert_idx is not None and n <= 2:
        insert_idx = None
    regular = [i for i in range(n) if i != insert_idx]
    return insert_idx, regular, LAYOUTS[len(regular)]


def regular_slots(panels: list[Panel]) -> int:
    """这一页最终会被排成几个**独立版位**。

    与 len(panels) 不是一回事:insert 格是叠在别的格子上面的异形小格,不占独立版位。
    于是 [medium, insert] 这样的两格页会走 LAYOUTS[1]——整页满铺再叠一个角标,肉眼
    与不分格的单图页无异(线上 f50b97f4 第 10 页正是如此,用户数分格页时数不到它)。
    "分格了没有"必须按这个数判断,不能按 len(panels)。走 _plan 而不是另写一遍判定:
    版位规则只能有一处真源,否则迟早与实际排版漂移。"""
    return len(_plan(panels)[1]) if panels else 0


def _slot_wh(rect: tuple[float, float, float, float]) -> tuple[int, int]:
    """版位矩形 -> 扣掉装订线后的实际可用像素尺寸。"""
    x0, y0, x1, y1 = _rect_px(rect)
    return x1 - x0 - GUTTER, y1 - y0 - GUTTER


def slot_sizes(panels: list[Panel]) -> list[tuple[int, int]]:
    """每格最终要塞进的像素尺寸,顺序与 panels 一一对应(insert 格给它的叠加尺寸)。

    供 S4 在**生成前**按每格自己的版位比例下发 size——原先所有格都用整页的 3:2 尺寸,
    塞进 1.79~3.61 的各种版位时垂直方向要裁掉 16%~58%,人物头部首当其冲。"""
    insert_idx, regular, layout = _plan(panels)
    sizes: list[tuple[int, int]] = [(0, 0)] * len(panels)
    for slot_i, panel_i in enumerate(regular):
        sizes[panel_i] = _slot_wh(layout[slot_i])
    if insert_idx is not None:
        hx0, hy0, hx1, hy1 = _rect_px(layout[0])   # 宿主格固定是版式模板第一格
        sizes[insert_idx] = (round((hx1 - hx0) * INSET_SCALE),
                             round((hy1 - hy0) * INSET_SCALE))
    return sizes


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def compose_manga_page(panel_imgs: list[bytes], panels: list[Panel]) -> bytes:
    """panel_imgs 与 panels 等长、按格子顺序一一对应(调用方已按生成失败跳过对齐好两个列表)。
    返回一张 PNG 编码的整页图片,尺寸恒为 FRAME。"""
    n = len(panel_imgs)
    if n == 0:
        raise ValueError("没有可用的格子图片")

    insert_idx, regular, layout = _plan(panels)

    canvas = Image.new("RGB", FRAME, BORDER)
    for slot_i, panel_i in enumerate(regular):
        rect = layout[slot_i]
        x0, y0, _x1, _y1 = _rect_px(rect)
        w, h = _slot_wh(rect)
        img = Image.open(io.BytesIO(panel_imgs[panel_i])).convert("RGB")
        canvas.paste(cover(img, (w, h), anchor_y=PANEL_ANCHOR_Y),
                     (x0 + GUTTER // 2, y0 + GUTTER // 2))

    if insert_idx is not None:
        hx0, hy0, hx1, hy1 = _rect_px(layout[0])  # 宿主格固定选常规格里版式模板的第一格
        iw = round((hx1 - hx0) * INSET_SCALE)
        ih = round((hy1 - hy0) * INSET_SCALE)
        img = Image.open(io.BytesIO(panel_imgs[insert_idx])).convert("RGB")
        inset = cover(img, (iw, ih), anchor_y=PANEL_ANCHOR_Y)
        mask = _rounded_mask((iw, ih), INSET_RADIUS)
        pos = (hx1 - iw - INSET_MARGIN, hy1 - ih - INSET_MARGIN)
        outline = Image.new("RGB", (iw + OUTLINE_WIDTH * 2, ih + OUTLINE_WIDTH * 2), OUTLINE_COLOR)
        canvas.paste(outline, (pos[0] - OUTLINE_WIDTH, pos[1] - OUTLINE_WIDTH))
        canvas.paste(inset, pos, mask)

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()
