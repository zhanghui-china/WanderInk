# spike/report.py
"""汇总 spike/out 下所有图为对比页。评分标准(每页):
同一角色的发型/服饰/面部与三视图一致=1 分,明显漂移=0 分。
用法: uv run python spike/report.py && open spike/out/report.html
"""
import base64
from pathlib import Path

OUT = Path("spike/out")


def img_tag(p: Path) -> str:
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" style="width:280px;margin:4px">'


def main() -> None:
    rows = []
    for style_dir in sorted(d for d in OUT.iterdir() if d.is_dir() and d.name != "probe"):
        turnarounds = sorted(style_dir.glob("turnaround_*.png"))
        pages = sorted(style_dir.glob("page_*.png"))
        rows.append(f"<h2>{style_dir.name}</h2><h3>三视图(参考)</h3>"
                    + "".join(img_tag(p) for p in turnarounds)
                    + "<h3>页面</h3>"
                    + "".join(f'<figure style="display:inline-block">{img_tag(p)}'
                              f"<figcaption>{p.stem} 一致性:__/2 角色</figcaption></figure>"
                              for p in pages))
    html = ("<meta charset='utf-8'><title>角色一致性评分</title>"
            "<p>每页每个出场角色打 1(一致)或 0(漂移),总分 = 得分/总角色次。</p>"
            + "".join(rows))
    (OUT / "report.html").write_text(html, encoding="utf-8")
    print(f"written: {OUT / 'report.html'}")


if __name__ == "__main__":
    main()
