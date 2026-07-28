"""auth 模块 + 登录流程测试:bcrypt 校验、current_user 401、真实 cookie 登录/登出闭环。"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shanhai import api, auth


def test_hash_and_verify_password_roundtrip():
    h = auth.hash_password("s3cret")
    assert h != "s3cret"                          # 不是明文
    assert auth.verify_password("s3cret", h) is True
    assert auth.verify_password("wrong", h) is False


def test_verify_password_bad_hash_is_false():
    assert auth.verify_password("x", "") is False          # 空哈希不外抛,判定不匹配
    assert auth.verify_password("x", "not-a-hash") is False


def test_add_user_and_verify_login(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("wuzi", "pw1")
    assert users.exists()
    assert auth.verify_login("wuzi", "pw1") is True
    assert auth.verify_login("wuzi", "bad") is False
    assert auth.verify_login("nobody", "pw1") is False     # 未知用户


def test_add_user_overwrites_password(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("wuzi", "old")
    auth.add_user("wuzi", "new")                           # 同名覆盖口令,不建重复账号
    import json
    data = json.loads(users.read_text(encoding="utf-8"))
    assert [u["username"] for u in data["users"]] == ["wuzi"]
    assert auth.verify_login("wuzi", "new") is True
    assert auth.verify_login("wuzi", "old") is False


def test_protected_endpoint_401_without_session():
    # 无依赖覆盖(本文件不带 test_api 的 autouse override)、无 cookie → current_user 抛 401
    c = TestClient(api.app)
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/meta").status_code == 401


def test_login_cookie_flow(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("wuzi", "pw1")
    c = TestClient(api.app)                                # TestClient 自动持有 cookie

    assert c.get("/api/meta").status_code == 401           # 登录前受保护端点 401

    bad = c.post("/api/login", json={"username": "wuzi", "password": "bad"})
    assert bad.status_code == 401                          # 错密码不下发 cookie

    ok = c.post("/api/login", json={"username": "wuzi", "password": "pw1"})
    assert ok.status_code == 200 and ok.json() == {"username": "wuzi"}
    assert c.get("/api/me").json() == {"username": "wuzi", "is_admin": False}
    assert c.get("/api/meta").status_code == 200           # 带 cookie 访问受保护端点通过

    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/meta").status_code == 401           # 登出后原 cookie 失效


def test_verify_login_unknown_user_still_hits_bcrypt(tmp_path, monkeypatch):
    # 时序侧信道加固:用户名不存在时也要跑一次 bcrypt 比对(耗时与真实用户一致),
    # 不能"查表未命中就秒回 False"——否则响应延迟能被用来枚举有效用户名。
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("wuzi", "pw1")
    with patch("shanhai.auth.verify_password", wraps=auth.verify_password) as spy:
        assert auth.verify_login("nobody", "whatever") is False
    spy.assert_called_once_with("whatever", auth._DUMMY_HASH)


def test_every_api_route_requires_login_except_login_logout():
    # 对抗审计发现的盲区:tests/test_api.py 的 autouse dependency_overrides 只在端点确实声明了
    # Depends(current_user) 时才生效——若某端点漏加该依赖,override 是空操作,测试仍会全绿。
    # 用反射枚举全部 /api 路由的真实依赖树,而不是硬编码路径清单(硬编码列表本身不会随新端点
    # 增长,起不到兜底作用)。
    # 豁免名单必须逐条有理由,不是"加进来让测试变绿":
    # login/logout 是登录流程本身;version 是部署脚本的自证端点(curl 无 cookie 也要能拿到,
    # 且前端在登录页之前就要读它比对前后端版本),泄露面只有一个 commit id。
    exempt = {("POST", "/api/login"), ("POST", "/api/logout"), ("GET", "/api/version")}
    checked = 0
    for route in api.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not path.startswith("/api/") or not methods:
            continue
        dependant = getattr(route, "dependant", None)
        calls = [d.call for d in dependant.dependencies] if dependant else []
        for method in methods:
            if method == "HEAD" or (method, path) in exempt:
                continue
            checked += 1
            assert auth.current_user in calls, f"{method} {path} 缺少 Depends(current_user)"
    assert checked >= 15   # 防止筛选逻辑写坏导致循环体从未真正执行


def test_add_user_admin_flag_persists(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("boss", "pw1", admin=True)
    assert auth.is_admin("boss") is True
    assert auth.is_admin("nobody") is False           # 未知用户视为非管理员


def test_add_user_default_not_admin(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("wuzi", "pw1")
    assert auth.is_admin("wuzi") is False


def test_add_user_password_reset_preserves_admin_when_unspecified(tmp_path, monkeypatch):
    # admin=None(默认)重置口令时不应静默把已有管理员降级为普通用户。
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("boss", "old", admin=True)
    auth.add_user("boss", "new")                      # 不传 admin,只重置口令
    assert auth.is_admin("boss") is True
    assert auth.verify_login("boss", "new") is True


def test_add_user_can_explicitly_revoke_admin(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    auth.add_user("boss", "pw1", admin=True)
    auth.add_user("boss", "pw1", admin=False)
    assert auth.is_admin("boss") is False


def test_add_user_long_password_raises_friendly_error(tmp_path, monkeypatch):
    # bcrypt 对 >72 字节口令抛 ValueError;add_user 应转成友好提示,不是裸 bcrypt 报错。
    users = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", users)
    with pytest.raises(ValueError, match="密码过长"):
        auth.add_user("bob", "b" * 200)
    assert not users.exists()          # 失败不应留下半写文件
