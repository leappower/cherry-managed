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
                          headers={"X-Admin-Token": tok}).json()
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
                          headers={"X-Admin-Token": tok}).json()
        assert any(x["action"] == "dispatch_agent" and x["request_id"] == "req-audit-admin"
                   for x in rows)


class TestAdminPage:
    def test_admin_static_served(self):
        r = client.get("/admin/")
        assert r.status_code == 200
        assert "CherryStudio" in r.text
