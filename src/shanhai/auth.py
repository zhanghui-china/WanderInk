"""登录与身份:bcrypt 口令哈希 + users.json 账号存储 + FastAPI 鉴权依赖。

账号存 users.json(项目根,和 config.json 同一 gitignore 待遇,含密码哈希不入库),
读写复用 store.atomic_write_text 的原子写(唯一临时名 + os.replace),不新建存储抽象。
current_user 是跨任务契约:后续端点靠 `user: str = Depends(current_user)` 拿登录名,勿改名。
"""
import json
import re
import threading
from collections.abc import Callable
from pathlib import Path

import bcrypt
from fastapi import HTTPException, Request

from shanhai import store

USERS_PATH = Path("users.json")  # 在调用点动态解析(供测试 monkeypatch 到 tmp 路径)

# 用户名不存在时也要跑一次这个假哈希,让"查无此人"与"密码错误"耗时一致,
# 不给时序侧信道留可乘之机(bcrypt 只在真实存在的用户上才会被调用,否则秒回)。
_DUMMY_HASH = bcrypt.hashpw(b"dummy-constant-time-padding", bcrypt.gensalt()).decode("utf-8")

MIN_PASSWORD_LEN = 8   # 内网十人团队:长度下限性价比最高,不强制字符组合(那只会逼出 Passw0rd!)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")

# 写锁:所有写操作走 _update_users,在锁内完成 读→变换→写。
# store.atomic_write_text 只防**单次写入撕裂**(唯一临时名 + os.replace),完全不防丢更新——
# 两个线程各读到同一份旧 JSON、各改各的字段,后写的那个整份覆盖前一个。此前唯一的写路径是
# 人工敲 CLI adduser(天然串行)所以没暴露过,加了 HTTP 端点后并发写就是常态。
# 形状照 runtime_config.update_overrides。同样只防同进程多线程,跨进程(CLI 与 web 同时写)
# 不保护——与 runtime_config 同一限度。
_WRITE_LOCK = threading.Lock()

# 解析结果缓存,键是 (mtime_ns, size)。current_user 现在每个已鉴权请求都要查一次账号记录
# (pwd_ver/disabled),而 is_admin 本来就每次调用重读整份文件——加这层两边都受益,
# 也才对得起 api._may_edit 那句"常规路径不产生文件 IO"的用心。
# 写侧在锁内直接清缓存,不依赖 mtime 的时间精度(同一毫秒内的连续两次写会撞键)。
_CACHE: dict[Path, tuple[tuple[int, int], dict]] = {}


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
    """读 users.json,按 (mtime_ns, size) 缓存解析结果。

    返回的是缓存里那份 dict 的**深拷贝**:调用方(如 _update_users 的 mutate)会就地改它,
    不拷贝就会污染缓存,让下一次读拿到还没落盘的内容。"""
    p = path if path is not None else USERS_PATH
    try:
        st = p.stat()
    except OSError:
        return {"users": []}          # 文件不存在/不可读:视为空账号表,与原行为一致
    key = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(p)
    if hit is None or hit[0] != key:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 坏 JSON 不再冒 500:与 runtime_config.load_overrides 同一取舍——告警 + 降级,
            # 而不是让登录端点直接 500(那会让整站进不去,且看不出原因)。
            print(f"[auth] 读取 {p} 失败,按空账号表处理:{p}")
            return {"users": []}
        if not isinstance(data, dict) or not isinstance(data.get("users"), list):
            print(f"[auth] {p} 结构不是 {{'users': [...]}},按空账号表处理")
            return {"users": []}
        _CACHE[p] = (key, data)
        hit = _CACHE[p]
    return json.loads(json.dumps(hit[1]))


def _update_users(mutate: Callable[[dict], None], path: Path | None = None) -> None:
    """锁内原子地 读→变换→写。**所有**写操作都必须走这里,不要各自 _load_users +
    atomic_write_text——那正是丢更新的来源(见模块顶部 _WRITE_LOCK 的说明)。
    mutate 就地改传入的 dict;它抛异常则不落盘(校验失败不留半写文件,与 add_user 原有语义一致)。"""
    p = path if path is not None else USERS_PATH
    with _WRITE_LOCK:
        data = _load_users(p)
        mutate(data)
        store.atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
        _CACHE.pop(p, None)   # 不依赖 mtime 精度:同一毫秒内连写两次会撞键,直接失效最稳


def _find(data: dict, username: str) -> dict | None:
    for u in data.get("users", []):
        if u.get("username") == username:
            return u
    return None


def _check_password(password: str) -> None:
    """长度下限在这里统一判。bcrypt 的 72 字节上限由 hash_password 负责(它抛的也是 ValueError,
    文案已中文化),两者调用方都按 400 处理。"""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LEN} 位")


def _assert_not_last_admin(data: dict, username: str) -> None:
    """不能让最后一个**启用中的**管理员被降级或停用——否则没人能再建号/重置密码,
    只能 SSH 上服务器手改 users.json。"""
    others = [u for u in data.get("users", [])
              if u.get("username") != username and u.get("is_admin") and not u.get("disabled")]
    if not others:
        raise ValueError("这是最后一个启用中的管理员,不能降级或停用")


def add_user(username: str, password: str, admin: bool | None = None,
             path: Path | None = None) -> None:
    """bcrypt 哈希后追加到 users.json;同名用户则覆盖其口令(不建重复账号)。
    admin 为 None 时保留该用户原有管理员标记(新建账号则默认 False),不因重置口令而
    静默降级已有管理员。

    ⚠️ **"同名则覆盖"这条语义是 CLI adduser 的既定行为**(tests/test_auth.py 钉着),
    所以它不能拿来做 HTTP 的"新增用户"——那会静默改掉已有用户的密码。建号走 create_user。"""
    ph = hash_password(password)          # 先算:超长密码在这里抛,users.json 完全没被碰过

    def _mutate(data: dict) -> None:
        u = _find(data, username)
        if u is None:
            data.setdefault("users", []).append(
                {"username": username, "password_hash": ph, "is_admin": bool(admin),
                 "pwd_ver": 0, "disabled": False})
            return
        u["password_hash"] = ph
        u["pwd_ver"] = int(u.get("pwd_ver", 0)) + 1   # 改了口令就让旧会话失效
        if admin is not None:
            u["is_admin"] = admin

    _update_users(_mutate, path)


def create_user(username: str, password: str, admin: bool = False,
                path: Path | None = None) -> None:
    """新建账号。**已存在则抛 ValueError**——这正是它与 add_user 的分水岭:
    add_user 遇同名会覆盖口令(CLI 的既定语义),用它做"新增用户"等于给了任何管理员
    一个静默改掉别人密码的入口,而界面上写的是"新增"。"""
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError("用户名只能用字母、数字、下划线、点、短横线,长度 2-32")
    _check_password(password)
    ph = hash_password(password)

    def _mutate(data: dict) -> None:
        if _find(data, username) is not None:
            raise ValueError(f"用户已存在: {username}")
        data.setdefault("users", []).append(
            {"username": username, "password_hash": ph, "is_admin": bool(admin),
             "pwd_ver": 0, "disabled": False})

    _update_users(_mutate, path)


def set_password(username: str, password: str, path: Path | None = None) -> None:
    """改口令并自增 pwd_ver —— 后者让该用户**所有设备上**的现有会话立刻失效
    (current_user 每次请求都会比对)。改密与重置共用它。"""
    _check_password(password)
    ph = hash_password(password)

    def _mutate(data: dict) -> None:
        u = _find(data, username)
        if u is None:
            raise ValueError(f"用户不存在: {username}")
        u["password_hash"] = ph
        u["pwd_ver"] = int(u.get("pwd_ver", 0)) + 1

    _update_users(_mutate, path)


def set_admin(username: str, value: bool, path: Path | None = None) -> None:
    def _mutate(data: dict) -> None:
        u = _find(data, username)
        if u is None:
            raise ValueError(f"用户不存在: {username}")
        if not value:
            _assert_not_last_admin(data, username)
        u["is_admin"] = bool(value)

    _update_users(_mutate, path)


def set_disabled(username: str, value: bool, path: Path | None = None) -> None:
    """停用/启用。刻意**不做硬删除**:Project.owner 存的是用户名字符串,删号会让那些作品
    变成"有主但人不存在"——除管理员外没有任何人能再编辑,界面上还看不出原因。
    停用达成同样的目的(登不进来)但归属完好、随时可恢复。"""
    def _mutate(data: dict) -> None:
        u = _find(data, username)
        if u is None:
            raise ValueError(f"用户不存在: {username}")
        if value and u.get("is_admin"):
            _assert_not_last_admin(data, username)
        u["disabled"] = bool(value)

    _update_users(_mutate, path)


def list_users(path: Path | None = None) -> list[dict]:
    """账号清单。**逐字段挑选,绝不带 password_hash 出去**——这是给 HTTP 端点用的。"""
    return [{"username": u.get("username", ""),
             "is_admin": bool(u.get("is_admin", False)),
             "disabled": bool(u.get("disabled", False))}
            for u in _load_users(path).get("users", [])]


def find_user(username: str, path: Path | None = None) -> dict | None:
    """单条账号记录(含哈希),供 current_user 校验 pwd_ver/disabled。不要直接吐给 HTTP。"""
    return _find(_load_users(path), username)


def is_admin(username: str, path: Path | None = None) -> bool:
    """该用户是否管理员(读 users.json 的 is_admin 字段;历史账号无该字段视为 False)。"""
    for u in _load_users(path).get("users", []):
        if u.get("username") == username:
            return bool(u.get("is_admin", False))
    return False


def verify_login(username: str, password: str, path: Path | None = None) -> bool:
    for u in _load_users(path).get("users", []):
        if u.get("username") == username:
            return verify_password(password, u.get("password_hash", ""))
    verify_password(password, _DUMMY_HASH)  # 用户名不存在:仍跑一次 bcrypt,抹平耗时差异
    return False


def current_user(request: Request) -> str:
    """FastAPI 依赖:从签名 session cookie 取登录名,未登录抛 401。
    受保护端点统一写 `user: str = Depends(current_user)`(参数名恒为 user,跨任务契约)。

    除"有没有登录名"外还校验两件事,因为 Starlette 的签名 cookie **没有服务端存储**、
    默认有效期 14 天,服务端手上没有可撤销的会话对象:
      · pwd_ver 与账号当前值不符 → 改过密码(自己改或被管理员重置),旧会话立刻作废
      · disabled → 已停用,现有会话也一并断掉,而不是等 cookie 自然过期
    老 cookie 兼容:老 session 没有 pwd_ver 键、老账号记录也没有,两边都取默认 0 相等,
    所以本功能上线**不会**把现有登录的人踢下线。
    读盘成本由 _load_users 的 mtime 缓存兜住(顺带也让 is_admin 不再每次重解析整份文件)。"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "未登录")
    rec = find_user(user)
    if rec is None or rec.get("disabled") \
            or int(rec.get("pwd_ver", 0)) != int(request.session.get("pwd_ver", 0)):
        raise HTTPException(401, "登录态已失效,请重新登录")
    return user
