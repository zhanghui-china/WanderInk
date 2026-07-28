"""生成站点图标。一次性脚本,产物入 git(web/public/),平时不需要跑。

图标 = 顶栏那枚方印本身(App.tsx:156-158):圆角方块 + 青绿渐变 + 白色「墨」。
不另起炉灶,让标签页里的小图标与页面左上角是同一个东西。

为什么是 PNG 而不是 SVG:SVG 里的汉字要么依赖系统字体(favicon 在浏览器 chrome 里渲染,
拿不到页面的 webfont;万一系统缺 CJK 字体就是豆腐块),要么得把字形转成路径(需要
fontTools,本仓库没装)。用 Pillow + 仓库自带的 Noto 直接栅格化,渲染结果确定、
零运行时依赖。favicon 本来也不需要矢量——最大用到 180px。

用法:.venv/bin/python scripts/make-favicon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
OUT_DIR = ROOT / "web" / "public"

# 用「山」而不是顶栏那个「墨」:实测 32px 下 15 笔的「墨」糊成一团、加粗也救不回来,
# 而 3 笔的「山」在 16px 都清晰可辨。favicon 的职责是在标签栏里被认出来,可读性优先于
# 与页头逐字一致;「山」也对应项目中文名「山海」与水墨山形装饰(decor.InkScape)。
# 想换回「墨」只改这一行即可(视觉上会退化成"一个青绿方块",靠颜色和形状辨识)。
CHAR = "山"
CHAR_SCALE = 0.66      # 字面占比:笔画少的字要放大些才压得住方块
CHAR_BOLD = 0.010      # 合成加粗(Noto Regular 在小尺寸偏细)
# tailwind.config.js 的 cinnabar-bright / cinnabar-deep(名字叫朱砂,实际是青绿)
C1, C2 = (0x57, 0x9C, 0x92), (0x21, 0x5A, 0x52)
RICE = (0xEE, 0xF5, 0xF1)          # 字色
GOLD = (0xA9, 0x8A, 0x45)          # 描金内环,与 ring-gold/30 同源
SS = 8                             # 超采样倍数:先大后缩,拿到干净的抗锯齿边缘


def _gradient(size: int) -> Image.Image:
    """左上 → 右下的对角线性渐变,对应 bg-gradient-to-br。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(C1, C2))
    return img


def make(size: int, opaque: bool = False) -> Image.Image:
    n = size * SS
    radius = round(n * 0.22)        # 对应 rounded-[10px] / 44px ≈ 0.227

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius, fill=255)

    icon = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    icon.paste(_gradient(n), (0, 0), mask)

    d = ImageDraw.Draw(icon)
    # 描金内环:16px 下细到看不见,只在够大的尺寸画,免得糊成一圈脏边
    if size >= 48:
        inset = round(n * 0.055)
        d.rounded_rectangle((inset, inset, n - 1 - inset, n - 1 - inset),
                            round(radius * 0.8), outline=(*GOLD, 90), width=max(1, round(n * 0.012)))

    font = ImageFont.truetype(str(FONT), round(n * CHAR_SCALE))
    # 用 anchor="mm" 按字形外接框居中:CJK 字面框上下留白不对称,按基线摆会偏
    d.text((n / 2, n / 2 + n * 0.01), CHAR, font=font, fill=RICE, anchor="mm",
           stroke_width=round(n * CHAR_BOLD), stroke_fill=RICE)

    icon = icon.resize((size, size), Image.LANCZOS)
    if opaque:
        # iOS 会把透明区域填成黑色,apple-touch-icon 必须自带不透明底
        bg = Image.new("RGB", (size, size), C2)
        bg.paste(icon, (0, 0), icon)
        return bg
    return icon


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size, opaque in (("favicon-32.png", 32, False),
                               ("favicon-192.png", 192, False),
                               ("apple-touch-icon.png", 180, True)):
        p = OUT_DIR / name
        make(size, opaque).save(p)
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size}B")

    # 顺带出一份 .ico:声明了 <link rel=icon> 的浏览器不会来要它,但链接预览器、
    # RSS 阅读器、老工具会盲探 /favicon.ico,留个 404 在日志里没必要。
    ico = OUT_DIR / "favicon.ico"
    make(64).save(ico, sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"{ico.relative_to(ROOT)}  {ico.stat().st_size}B")
