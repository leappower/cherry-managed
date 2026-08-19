"""JJC-20260819-001 方案B 步骤2/3：Agent 配置 CRUD + 推送 API 测试。

覆盖（方案第七节步骤2/3验证点）：
  步骤2：agent-configs CRUD 全套 + 权限（无 token 401）
  步骤3：push/agents 生成 dispatch_log、载荷含 sha256/rev/metadata；push/jobs 回执查询
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
ADMIN_PASS = "admin123"


@pytest.fixture()
def token():
    main.admin_auth.reset_lock(ADMIN_USER)
    r = client.post("/api/admin/login",
                    json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(autouse=True)
def _clean_agent_tables():
    """隔离：每个测试前清空 agent 相关表（共享 dev DB 防串扰）。"""
    conn = db.get_conn(main.DB_PATH)
    for t in ("agent_versions", "agent_configs", "deploy_status"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    yield
    for t in ("agent_versions", "agent_configs", "deploy_status"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def _pkg(name="财务助理", version="1.0.0", instructions="你是财务助理。"):
    return {
        "metadata": {"name": name, "version": version},
        "agent": {
            "name": name, "type": "claude-code",
            "model": "deepseek/deepseek-v4-flash",
            "instructions": instructions,
            "configuration": {"max_turns": 100},
            "skills": ["s1"],
        },
        "skills": [{"id": "s1", "name": "口径", "content": "内容"}],
        "resources": {"g.md": "A"},
    }


class TestNoAuth:
    def test_agent_configs_require_auth(self):
        # GET 端点：无 token 401
        for p in ["/api/admin/agent-configs", "/api/admin/push/jobs"]:
            assert client.get(p).status_code == 401, p
        # POST 端点：无 token 401（push/agents / agent-configs 创建）
        for p in ["/api/admin/agent-configs", "/api/admin/push/agents"]:
            assert client.post(p, json={}).status_code == 401, p


class TestAgentConfigCRUD:
    def test_create_list_get(self, token):
        h = {"X-Admin-Token": token}
        r = client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        assert r.status_code == 201, r.text
        rec = r.json()["data"]
        assert rec["rev"] == 1 and rec["sha256"]

        items = client.get("/api/admin/agent-configs", headers=h).json()["data"]
        assert any(i["name"] == "财务助理" and i["latest_rev"] == 1 for i in items)

        got = client.get("/api/admin/agent-configs/财务助理", headers=h).json()["data"]
        assert got["name"] == "财务助理" and got["config"]["metadata"]["rev"] == 1

    def test_update_new_rev(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        r = client.put("/api/admin/agent-configs/财务助理",
                       json=_pkg(version="1.1.0", instructions="新版"),
                       headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["rev"] == 2

        vers = client.get("/api/admin/agent-configs/财务助理/versions",
                          headers=h).json()["data"]
        assert [v["rev"] for v in vers] == [2, 1]

    def test_update_nonexistent_404(self, token):
        r = client.put("/api/admin/agent-configs/不存在",
                       json=_pkg("不存在"), headers={"X-Admin-Token": token})
        assert r.status_code == 404

    def test_bad_package_400(self, token):
        h = {"X-Admin-Token": token}
        r = client.post("/api/admin/agent-configs",
                        json={"metadata": {"name": "x"}, "agent": {}}, headers=h)
        assert r.status_code == 400
        body = r.json()["detail"]
        assert "errors" in body

    def test_bad_version_400(self, token):
        h = {"X-Admin-Token": token}
        r = client.post("/api/admin/agent-configs",
                        json=_pkg(version="not-semver"), headers=h)
        assert r.status_code == 400

    def test_duplicate_create_409(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        r = client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        assert r.status_code == 409


class TestPush:
    def test_push_agents_creates_dispatch_log(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        r = client.post("/api/admin/push/agents", headers=h,
                        json={"agent_name": "财务助理", "devices": ["dev-off-1"],
                              "target_rev": 1, "reason": "灰度"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["target_rev"] == 1
        assert len(data["request_ids"]) == 1
        # 离线设备入队：offline_queued = 1
        assert data["dispatch"]["offline_queued"] == 1

        # push/jobs 能查到
        jobs = client.get("/api/admin/push/jobs", headers=h).json()["data"]
        assert any(j["request_id"] == data["request_ids"][0] for j in jobs)

    def test_push_unknown_agent_404(self, token):
        h = {"X-Admin-Token": token}
        r = client.post("/api/admin/push/agents", headers=h,
                        json={"agent_name": "nope", "devices": ["dev-x"]})
        assert r.status_code == 404

    def test_push_unknown_rev_404(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        r = client.post("/api/admin/push/agents", headers=h,
                        json={"agent_name": "财务助理", "devices": ["dev-x"],
                              "target_rev": 99})
        assert r.status_code == 404

    def test_push_no_target_400(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        r = client.post("/api/admin/push/agents", headers=h,
                        json={"agent_name": "财务助理"})
        assert r.status_code == 400

    def test_push_group_target(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        # 无匹配 group 设备 → 空目标 → 400
        r = client.post("/api/admin/push/agents", headers=h,
                        json={"agent_name": "财务助理", "group": "pilot"})
        assert r.status_code == 400

    def test_rollback_to_rev(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        client.put("/api/admin/agent-configs/财务助理",
                   json=_pkg(version="1.1.0"), headers=h)  # rev2
        # 回滚到 rev1
        r = client.post("/api/admin/agent-configs/财务助理/rollback-to/1",
                        headers=h, json={"devices": ["dev-roll"], "reason": "rollback"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["target_rev"] == 1

    def test_push_locked_400(self, token):
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        from agent_repo import AgentRepo
        repo = AgentRepo(main.DB_PATH)
        repo.set_locked("财务助理", True)
        try:
            r = client.post("/api/admin/push/agents", headers=h,
                            json={"agent_name": "财务助理", "devices": ["dev-x"]})
            assert r.status_code == 400
        finally:
            repo.set_locked("财务助理", False)

    def test_confirm_agent_deploy_records_deploy_status(self, token):
        """回执带 deployed_rev → confirm_agent_deploy 落 deploy_status（对账锚点）。"""
        h = {"X-Admin-Token": token}
        client.post("/api/admin/agent-configs", json=_pkg(), headers=h)
        # 模拟设备回执：deployed_rev=1 到达
        main.ws_server.dispatch.confirm_agent_deploy(
            "req-deploy-1", True, agent_name="财务助理", rev=1,
            version="1.0.0", sha256="x", device_id="dev-40")
        from agent_repo import AgentRepo
        dep = AgentRepo(main.DB_PATH).get_deploy("dev-40", "财务助理")
        assert dep is not None
        assert dep["rev"] == 1 and dep["version"] == "1.0.0"
        # 若设备已部署最新 rev，if_changed push 应跳过（skipped_unchanged>0）
        r = client.post("/api/admin/push/agents", headers=h,
                        json={"agent_name": "财务助理", "devices": ["dev-40"],
                              "if_changed": True, "target_rev": 1})
        data = r.json()["data"]
        assert data["dispatch"]["skipped_unchanged"] == 1
