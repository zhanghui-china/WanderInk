"""日式分格漫画排版:纯 PIL 合成,无网络依赖,可完全离线单测。
把 N 张独立生成的格子图按预定义版式拼成一整页;shot_type=="insert" 的格子
做圆角裁剪叠加在其它格子上面(漫画常见的嵌入式特写手法)。"""
import io

from PIL import Image, ImageDraw

from shanhai.schema import Panel
from shanhai.typeset import FRAME

GUTTER = 12  # 格间装订线宽度(像素)
BORDER = (20, 16, 12)  # 画布底色 = 装订线颜色(深墨色)
INSET_SCALE = 0.55  # 特写叠加格相对宿主格的边长比例
INSET_MARGIN = 24  # 特写叠加格距宿主格边缘的留白(像素)
INSET_RADIUS = 24  # 特写叠加格圆角半径(像素)
OUTLINE_COLOR = (245, 240, 228)  # 特写叠加格描边颜色(米宣纸色)
OUTLINE_WIDTH = 6  # 特写叠加格描边宽度(像素)

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


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """缩放并裁剪填满 (w, h),cover-fit,居中裁切。"""
    scale = max(w / img.width, h / img.height)
    resized = img.resize((max(round(img.width * scale), w), max(round(img.height * scale), h)))
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


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

    insert_idx = next((i for i, p in enumerate(panels) if p.shot_type == "insert"), None)
    if insert_idx is not None and n == 1:
        insert_idx = None  # 唯一一格标了 insert 也没有宿主格可叠加,退化为普通整页

    regular = [(img, p) for i, (img, p) in enumerate(zip(panel_imgs, panels)) if i != insert_idx]
    layout = LAYOUTS[len(regular)]

    canvas = Image.new("RGB", FRAME, BORDER)
    for (data, _), rect in zip(regular, layout):
        x0, y0, x1, y1 = _rect_px(rect)
        w, h = x1 - x0 - GUTTER, y1 - y0 - GUTTER
        img = Image.open(io.BytesIO(data)).convert("RGB")
        canvas.paste(_cover(img, w, h), (x0 + GUTTER // 2, y0 + GUTTER // 2))

    if insert_idx is not None:
        hx0, hy0, hx1, hy1 = _rect_px(layout[0])  # 宿主格固定选常规格里版式模板的第一格
        iw = round((hx1 - hx0) * INSET_SCALE)
        ih = round((hy1 - hy0) * INSET_SCALE)
        img = Image.open(io.BytesIO(panel_imgs[insert_idx])).convert("RGB")
        inset = _cover(img, iw, ih)
        mask = _rounded_mask((iw, ih), INSET_RADIUS)
        pos = (hx1 - iw - INSET_MARGIN, hy1 - ih - INSET_MARGIN)
        outline = Image.new("RGB", (iw + OUTLINE_WIDTH * 2, ih + OUTLINE_WIDTH * 2), OUTLINE_COLOR)
        canvas.paste(outline, (pos[0] - OUTLINE_WIDTH, pos[1] - OUTLINE_WIDTH))
        canvas.paste(inset, pos, mask)

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()
