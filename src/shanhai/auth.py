"""登录与身份:bcrypt 口令哈希 + users.json 账号存储 + FastAPI 鉴权依赖。

账号存 users.json(项目根,和 config.json 同一 gitignore 待遇,含密码哈希不入库),
读写复用 store.atomic_write_text 的原子写(唯一临时名 + os.replace),不新建存储抽象。
current_user 是跨任务契约:后续端点靠 `user: str = Depends(current_user)` 拿登录名,勿改名。
"""
import json
from pathlib import Path

import bcrypt
from fastapi import HTTPException, Request

from shanhai import store

USERS_PATH = Path("users.json")  # 在调用点动态解析(供测试 monkeypatch 到 tmp 路径)

# 用户名不存在时也要跑一次这个假哈希,让"查无此人"与"密码错误"耗时一致,
# 不给时序侧信道留可乘之机(bcrypt 只在真实存在的用户上才会被调用,否则秒回)。
_DUMMY_HASH = bcrypt.hashpw(b"dummy-constant-time-padding", bcrypt.gensalt()).decode("utf-8")


def hash_password(password: str) -> str:
    try:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except ValueError as e:  # bcrypt 对 >72 字节口令报错,转成更友好的提示(CLI 交互场景常见)
        raise ValueError("密码过长(bcrypt 上限 72 字节),请换一个更短的密码") from e


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:  # 空哈希/非法盐格式:视为不匹配,不外抛
        return False


def _load_users(path: Path | None = None) -> dict:
    p = path if path is not None else USERS_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"users": []}


def add_user(username: str, password: str, path: Path | None = None) -> None:
    """bcrypt 哈希后追加到 users.json;同名用户则覆盖其口令(不建重复账号)。"""
    p = path if path is not None else USERS_PATH
    data = _load_users(p)
    users = data.setdefault("users", [])
    ph = hash_password(password)
    for u in users:
        if u.get("username") == username:
            u["password_hash"] = ph
            break
    else:
        users.append({"username": username, "password_hash": ph})
    store.atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


def verify_login(username: str, password: str, path: Path | None = None) -> bool:
    for u in _load_users(path).get("users", []):
        if u.get("username") == username:
            return verify_password(password, u.get("password_hash", ""))
    verify_password(password, _DUMMY_HASH)  # 用户名不存在:仍跑一次 bcrypt,抹平耗时差异
    return False


def current_user(request: Request) -> str:
    """FastAPI 依赖:从签名 session cookie 取登录名,未登录抛 401。
    受保护端点统一写 `user: str = Depends(current_user)`(参数名恒为 user,跨任务契约)。"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "未登录")
    return user
