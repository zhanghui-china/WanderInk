"""把当前 git 状态写成仓库根的 version.json —— 版本号的唯一生成器。

为什么落成文件而不是各处现算 git:
- DGX 上算不得。日常部署 rsync 带 --exclude='.git'(docs/ops-dgx.md),那边的 .git
  冻结在 2026-07-11 的 bootstrap 状态,现算会报一个几个月前的提交,比没有版本号更危险。
- 前端(vite.config.ts,Node)与后端(shanhai/version.py,Python)各写一份读 git 的
  逻辑必然迟早漂移——这个仓库已经因为"同一个判断写两份"吃过好几次亏。一个生成器、
  一个文件、两个读者。

version.json 必须 gitignore:内容依赖 HEAD,提交它就永远差一个提交(鸡生蛋)。
代价是新克隆的仓库没有它,所以两个读者都要有 dev 降级路径。

用法:python3 scripts/stamp-version.py
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "version.json"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True,
                                   stderr=subprocess.DEVNULL).strip()


def stamp() -> dict:
    """已知局限:build 数的是**当前分支**的提交数(git rev-list --count HEAD),
    换分支或 rebase 会跳变。单人单 main 分支够用,不为它引入 tag 体系。"""
    try:
        return {
            "build": int(_git("rev-list", "--count", "HEAD")),
            "sha": _git("rev-parse", "--short", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
            "stamped_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    except Exception:   # noqa: BLE001 不在 git 仓库里(tar 包解出来的)/ 没装 git:降级,不阻断构建
        return {"build": 0, "sha": "dev", "dirty": True,
                "stamped_at": datetime.now().astimezone().isoformat(timespec="seconds")}


if __name__ == "__main__":
    info = stamp()
    OUT.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}  b{info['build']}·{info['sha']}"
          f"{'·dirty' if info['dirty'] else ''}")
