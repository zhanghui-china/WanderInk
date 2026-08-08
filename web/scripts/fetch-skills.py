#!/usr/bin/env python3
"""把「山音超级编剧/导演大师」两个 skill 取到 assets/skills/。

为什么用脚本取而不是把正文提交进仓库:
  · 那是 @山音 的作品(MIT,允许再分发,但我们选择不在自己仓库里再放一份)
  · assets/skills/ 在 .gitignore 里 → 一次 clone 拿不到,必须有可复现的取回路径
  · rsync 部署**不看 .gitignore**,所以取到本地后会照常同步到 DGX

许可(两个仓库均为 MIT):可自由用于个人/商业创作,须保留 @山音 署名,
不得把 skill 文件本身作为付费产品转售。我们的用法(在自己的产品里调用它生成剧本)在允许范围内。

用法:
    uv run python scripts/fetch-skills.py          # 取到 assets/skills/
    uv run python scripts/fetch-skills.py --check  # 只检查是否齐全,不下载(退出码非 0 = 缺件)
"""
import argparse
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets" / "skills"

# 两个 skill 的 GitHub 仓库。取 main 分支的 zip,不依赖本机装 git。
REPOS = {
    "screenwriting-master": "https://github.com/Shanyin-ai/shanyin-screenwriting-master",
    "director-master": "https://github.com/Shanyin-ai/shanyin-director-master",
}

# 取回后必须存在的文件(相对 assets/skills/<skill>/)。缺任何一个都算没取全——
# 让缺件在这里暴露,而不是等到生成时才发现 skill 静默没生效(2026-08-08 刚吃过这种亏)。
REQUIRED = {
    "screenwriting-master": [
        "SKILL.md",
        "references/core-methodology.md",
        "references/format-ultrashort.md",
        "references/format-short.md",
    ],
    "director-master": [
        "SKILL.md",
        "references/core-methodology.md",
        "references/shot-design.md",
        "references/storyboard-format.md",
        "references/genre-A-mood.md",
        "references/genre-B-genre.md",
        "references/genre-D-theme.md",
    ],
}


def missing() -> list[str]:
    out = []
    for skill, files in REQUIRED.items():
        for f in files:
            if not (DEST / skill / f).is_file():
                out.append(f"{skill}/{f}")
    return out


def _extract(repo_url: str, skill: str) -> None:
    """下载仓库 zip,把里面 <skill>/ 那棵子树抠出来放到 assets/skills/<skill>/。

    两个仓库的布局不同:director 仓库里有现成的 director-master/ 目录;
    screenwriting 仓库把内容打包在 .skill(其实是 zip)里。两种都要认。"""
    with urllib.request.urlopen(f"{repo_url}/archive/refs/heads/main.zip", timeout=120) as f:
        outer = zipfile.ZipFile(io.BytesIO(f.read()))

    dest = DEST / skill
    if dest.exists():
        shutil.rmtree(dest)

    # ① 仓库里直接有 <skill>/SKILL.md 这棵树
    hits = [n for n in outer.namelist() if f"/{skill}/" in n and not n.endswith("/")]
    if any(n.endswith(f"/{skill}/SKILL.md") for n in hits):
        for n in hits:
            rel = n.split(f"/{skill}/", 1)[1]
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(outer.read(n))
        return

    # ② 内容打包在 .skill(zip)里
    for n in outer.namelist():
        if not n.endswith(".skill"):
            continue
        inner = zipfile.ZipFile(io.BytesIO(outer.read(n)))
        for m in inner.namelist():
            if m.endswith("/") or f"{skill}/" not in m:
                continue
            rel = m.split(f"{skill}/", 1)[1]
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(inner.read(m))
        return
    raise RuntimeError(f"{repo_url} 里既没有 {skill}/ 目录,也没有 .skill 包")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查不下载")
    a = ap.parse_args()

    if a.check:
        miss = missing()
        if miss:
            print("✗ assets/skills/ 缺件,跑 `uv run python scripts/fetch-skills.py` 取回:")
            for m in miss:
                print("   ", m)
            return 1
        print("✓ assets/skills/ 齐全")
        return 0

    DEST.mkdir(parents=True, exist_ok=True)
    for skill, url in REPOS.items():
        print(f"取 {skill} ← {url}")
        try:
            _extract(url, skill)
        except Exception as e:  # noqa: BLE001 —— 运维脚本,失败要说人话
            print(f"  ✗ {type(e).__name__}: {e}")
            return 1
        n = sum(1 for _ in (DEST / skill).rglob("*") if _.is_file())
        print(f"  ✓ {n} 个文件 → {(DEST / skill).relative_to(ROOT)}")

    miss = missing()
    if miss:
        print("\n✗ 取回后仍缺件(上游可能改了文件名):")
        for m in miss:
            print("   ", m)
        return 1
    print("\n全部齐全。许可 MIT · Designed by @山音 · 保留署名、不得转售 skill 本身。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
