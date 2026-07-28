"""读取 version.json —— 版本号的后端读者(唯一写者是 scripts/stamp-version.py)。

绝不抛异常:版本号是纯展示信息,文件缺失/损坏时降级成 dev 即可,不该让服务起不来,
也不该让 /api/version 返回 500(部署脚本正靠它自证线上跑的是哪一版)。
"""
import json
from pathlib import Path

# 路径算法与 api.py 的 _WEB_DIST 同源(src/shanhai/x.py → parents[2] = 仓库根)
_VERSION_FILE = Path(__file__).resolve().parents[2] / "version.json"
_FALLBACK = {"build": 0, "sha": "dev", "dirty": True, "stamped_at": ""}

_cache: dict | None = None


def build_info() -> dict:
    """{build, sha, dirty, stamped_at}。进程内缓存一次——文件在部署时写死,运行期不会变,
    每次请求读盘没有意义。"""
    global _cache
    if _cache is None:
        try:
            data = json.loads(_VERSION_FILE.read_text(encoding="utf-8"))
            _cache = {**_FALLBACK, **{k: data[k] for k in _FALLBACK if k in data}}
        except Exception:   # noqa: BLE001 文件缺失(新克隆)/ 坏 JSON / 权限:一律降级
            _cache = dict(_FALLBACK)
    return dict(_cache)   # 复制:调用方(FastAPI 序列化)改不到缓存
