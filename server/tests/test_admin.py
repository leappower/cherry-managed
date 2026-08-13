"""批次F：D-2 Web 管理后台测试。

覆盖 AC：
  AC1 登录鉴权：错误密码拒绝(401)，正确密码发 token
  AC2 管理 API 鉴权：无 token 401，有 token 200
  AC3 审计日志：登录/派发写 audit_log 可查询
  AC4 管理页面：/admin/ 可访问（静态页）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import db  # noqa: E402

client = TestClient(main.app)

ADMIN_USER = main.CONFIG.get("admin_user", "admin")
ADMIN_PASS = "admin123"  # config.json admin_password_hash 对应的明文（测试用）


def _login_ok() -> str:
    """登录成功并返回 token。"""
    r = client.post("/api/admin/login",
                    json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(autouse=True)
def _reset_admin_lock():
    """每个测试前清零登录失败计数/锁，隔离跨测试的限速状态。"""
    main.admin_auth.reset_lock(ADMIN_USER)
    yield


class TestLogin:
    def test_wrong_password_rejected(self):
        r = client.post("/api/admin/login",
                        json={"username": ADMIN_USER, "password": "wrong"})
        assert r.status_code == 401

    def test_wrong_user_rejected(self):
        r = client.post("/api/admin/login",
                        json={"username": "nobody", "password": ADMIN_PASS})
        assert r.status_code == 401

    def test_correct_password_returns_token(self):
        r = client.post("/api/admin/login",
                        json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200
        body = r.json()
        assert body["token"]
        assert body["user"] == ADMIN_USER


class TestAdminAPIAuth:
    def test_no_token_401(self):
        r = client.get("/api/admin/devices")
        assert r.status_code == 401

    def test_bad_token_401(self):
        r = client.get("/api/admin/devices", headers={"X-Admin-Token": "bogus"})
        assert r.status_code == 401

    def test_with_token_200(self):
        tok = _login_ok()
        r = client.get("/api/admin/devices", headers={"X-Admin-Token": tok})
        assert r.status_code == 200

    def test_all_admin_get_endpoints_require_auth(self):
        paths = ["/api/admin/devices", "/api/admin/dispatch_log", "/api/admin/usage",
                 "/api/admin/audit_log", "/api/admin/reconcile", "/api/admin/agents"]
        for p in paths:
            assert client.get(p).status_code == 401, p


class TestAudit:
    def test_login_writes_audit(self):
        # 清空 audit_log，登录后应写入 admin_login
        conn = db.get_conn(main.DB_PATH)
        conn.execute("DELETE FROM audit_log")
        conn.commit()
        tok = _login_ok()
        rows = client.get("/api/admin/audit_log",
                          headers={"X-Admin-Token": tok}).json()["items"]
        actions = [r["action"] for r in rows]
        assert "admin_login" in actions
        # 有对应的 admin_login_failed（登录成功前 test 里触发过，DB 非隔离）
        assert any(r["operator"] == ADMIN_USER for r in rows)

    def test_admin_dispatch_writes_audit(self):
        tok = _login_ok()
        # 派发一个离线设备（会写 dispatch + audit）
        r = client.post("/api/admin/dispatch/agent",
                        headers={"X-Admin-Token": tok},
                        json={"device_id": "dev-audit", "action": "create",
                              "agent": {"name": "a", "model": "m"},
                              "request_id": "req-audit-admin"})
        assert r.status_code == 200, r.text
        rows = client.get("/api/admin/audit_log",
                          headers={"X-Admin-Token": tok}).json()["items"]
        assert any(x["action"] == "dispatch_agent" and x["request_id"] == "req-audit-admin"
                   for x in rows)


class TestRateLimit:
    """登录失败限速：第 5 次失败锁定，第 6 次直接拒绝（锁 15 分钟）。"""

    def _wrong_login(self):
        return client.post("/api/admin/login",
                           json={"username": ADMIN_USER, "password": "wrongpass"})

    def test_5_failures_locks_then_6th_rejected(self):
        main.admin_auth.reset_lock(ADMIN_USER)  # 隔离：清零上次测试可能残留的锁
        # 前 4 次失败：普通 401
        for i in range(4):
            assert self._wrong_login().status_code == 401, i
        # 第 5 次失败：仍 401（计数达成触发锁定），锁已生效
        assert self._wrong_login().status_code == 401
        # 第 6 次尝试：锁定期内直接拒绝，仍 401
        assert self._wrong_login().status_code == 401
        # 即便拿正确密码，锁定期内也应被拒（锁定态优先）
        r = client.post("/api/admin/login",
                        json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 401

    def test_lock_expiry_allows_login(self):
        auth = main.admin_auth
        auth.reset_lock(ADMIN_USER)
        # 触发锁定
        for _ in range(5):
            assert self._wrong_login().status_code == 401
        # 直接改写内部 lock_until 让锁定立即过期（绕过对 LOCK_SECONDS 的依赖）
        auth._fails[ADMIN_USER] = (auth._fails[ADMIN_USER][0], 0.0)
        # 锁判定应返回 False，正确密码可登录
        assert auth._is_locked(ADMIN_USER) is False
        r = client.post("/api/admin/login",
                        json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200
        auth.reset_lock(ADMIN_USER)


class TestPagination:
    """audit_log / devices 分页筛选：limit/offset/total 正确。"""

    def test_audit_pagination_metadata(self):
        tok = _login_ok()
        # 确保有审计记录
        conn = db.get_conn(main.DB_PATH)
        conn.execute("DELETE FROM audit_log")
        conn.commit()
        tok2 = _login_ok()
        body = client.get("/api/admin/audit_log", headers={"X-Admin-Token": tok2},
                          params={"limit": 2, "offset": 0}).json()
        assert "total" in body and "limit" in body and "offset" in body and "items" in body
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["items"]) == min(2, body["total"])

    def test_audit_pagination_action_operator_filter(self):
        tok = _login_ok()
        # 筛选出 admin_login 记录
        body = client.get("/api/admin/audit_log", headers={"X-Admin-Token": tok},
                          params={"action": "admin_login"}).json()
        assert body["total"] >= 1
        assert all(i["action"] == "admin_login" for i in body["items"])

    def test_devices_pagination_metadata(self):
        tok = _login_ok()
        body = client.get("/api/admin/devices", headers={"X-Admin-Token": tok},
                          params={"limit": 5, "offset": 0}).json()
        assert "total" in body and "limit" in body and "offset" in body and "items" in body
        assert body["limit"] == 5
        assert body["offset"] == 0
        assert len(body["items"]) == min(5, body["total"])


class TestAdminPage:
    def test_admin_static_served(self):
        r = client.get("/admin/")
        assert r.status_code == 200
        assert "CherryStudio" in r.text
