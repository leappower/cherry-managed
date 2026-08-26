"""JJC-20260826-001：从设备导入补全 skills/MCP —— 服务端 agent-import 测试。

覆盖：
  - 列表分支透传完整 AgentEntity（不再 6 字段白名单截断；含信封 skills 等）
  - 导入分支：pkg.skills 仅 enabled 完整 skill 实体（InstalledSkillSchema 字段，
    无 content）；agent.skills 引用与 pkg.skills 同源一致；agent 补全
    mcps/knowledgeBaseIds/disabledTools/modelName；configuration 透传
  - 存量 sidecar：回执 agent 缺 skills 键 → 519 ERR_SKILLS_MISSING（含
    「请升级员工端侧车」文案），拒绝落库；skills 空数组合法（不报错）
  - 回执 skills 单 agent 拉取失败降级空列表 → 可正常导入（空数组合法）
  - GET /api/admin/agent-configs/{name} 返回的 config 含 skills/mcps/knowledgeBaseIds

实现方式：monkeypatch main.ws_server 里的方法 + 直接 resolve_reply 唤醒等待，
避免引入真实 WS 设备。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from ws_server import wait_for_reply, resolve_reply  # noqa: E402

import main  # noqa: E402
import db  # noqa: E402

client = TestClient(main.app)

ADMIN_USER = main.CONFIG.get("admin_user", "admin")
ADMIN_PASS = "admin123"

DEVICE = "managed-61af1a78f6f36976"

_FULL_AGENT = {
    "id": "agent-uuid-1",
    "type": "claude-code",
    "name": "测试助手",
    "description": "desc",
    "instructions": "你是测试助手。",
    "model": "deepseek/deepseek-v4-flash",
    "modelName": "deepseek-v4-flash",
    "configuration": {"permission_mode": "default", "max_turns": 50, "env_vars": {"K": "V"}},
    "mcps": [{"name": "mcp1", "type": "stdio", "command": "npx"}],
    "knowledgeBaseIds": ["kb-1", "kb-2"],
    "disabledTools": ["tool_x"],
}

_SKILLS = [
    {"id": "s1", "name": "启用技能", "description": "d1", "folderName": "f1",
     "source": "local", "sourceUrl": "", "namespace": "ns1", "author": "a1",
     "version": "1.0.0", "sourceTags": ["tag"], "contentHash": "h1",
     "isEnabled": True, "content": "SHOULD-NOT-APPEAR"},
    {"id": "s2", "name": "禁用技能", "description": "d2", "folderName": "f2",
     "source": "local", "sourceUrl": "", "namespace": "ns2", "author": "a2",
     "version": "1.0.0", "sourceTags": [], "contentHash": "h2",
     "isEnabled": False, "content": "SHOULD-NOT-APPEAR"},
]


@pytest.fixture()
def token():
    main.admin_auth.reset_lock(ADMIN_USER)
    r = client.post("/api/admin/login",
                    json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(autouse=True)
def _clean_agent_tables():
    conn = db.get_conn(main.DB_PATH)
    for t in ("agent_versions", "agent_configs", "deploy_status"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    yield
    for t in ("agent_versions", "agent_configs", "deploy_status"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def _patch_import_flow(reply: dict):
    """把 agent-import 的 WS 交互替换为直答 reply。

    用内存 registry 替身：get() 报在线、get_connection() 返回 FakeWS，
    回执由 resolve_reply 直接唤醒 wait_for_reply。测试结束后恢复原 registry
    （避免污染同进程其他测试用例）。
    """
    original_registry = main.ws_server.registry

    class FakeWS:
        async def send_json(self, msg):
            # 收到 with_skills: true 的 agent_list 指令后，延后一拍回执：
            # 端点先 await send_json 再 wait_for_reply 注册 future，
            # 直接 resolve 会早于 future 注册（→ 504 超时）。
            assert msg.get("type") == "agent_list", msg
            assert msg.get("with_skills") is True, "服务端必须带 with_skills: true"
            rid = msg.get("request_id")

            async def _reply():
                resolve_reply(rid, reply)

            asyncio.get_running_loop().create_task(_reply())

    class FakeReg:
        def get(self, device_id):
            return {"device_id": device_id, "online": True}

        def get_connection(self, device_id):
            return FakeWS()

    main.ws_server.registry = FakeReg()

    def _restore():
        main.ws_server.registry = original_registry

    return _restore


@pytest.fixture()
def import_flow():
    """返回 _patch_import_flow 的便捷 fixture：用后自动恢复主 registry。"""
    restore_holders = []

    def _patch(reply):
        restore = _patch_import_flow(reply)
        restore_holders.append(restore)

    yield _patch
    for restore in restore_holders:
        restore()


class TestImportListBranch:
    def test_list_passes_through_full_agent_entity(self, token, import_flow):
        """AC：列表分支透传完整 AgentEntity（mcps/knowledgeBaseIds/configuration/skills 信封）。"""
        reply = {"success": True, "with_skills": True,
                 "agents": [{**_FULL_AGENT, "skills": _SKILLS}]}
        import_flow(reply)
        r = client.post(f"/api/admin/devices/{DEVICE}/agent-import",
                        json={}, headers={"X-Admin-Token": token})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["imported"] is False
        ag = data["agents"][0]
        # 全字段透传（不再 6 字段白名单截断）
        assert ag["id"] == "agent-uuid-1"
        assert ag["mcps"] == [{"name": "mcp1", "type": "stdio", "command": "npx"}]
        assert ag["knowledgeBaseIds"] == ["kb-1", "kb-2"]
        assert ag["disabledTools"] == ["tool_x"]
        assert ag["modelName"] == "deepseek-v4-flash"
        assert ag["configuration"] == {"permission_mode": "default", "max_turns": 50,
                                       "env_vars": {"K": "V"}}
        assert ag["instructions"] == "你是测试助手。"  # 不再被截断丢弃
        assert [s["id"] for s in ag["skills"]] == ["s1", "s2"]  # 信封 skills 透传


class TestImportBranch:
    def test_import_pkg_skills_enabled_entities_and_refs(self, token, import_flow):
        """AC：导入包 agent.skills 引用 + pkg.skills 实体同源一致。

        注：isEnabled 过滤是 sidecar 侧契约（见 sidecar/tests/test_agent_list_skills.py），
        本测试模拟新 sidecar 已过滤后的回执（skills 仅含 enabled 实体）。
        """
        reply = {"success": True, "with_skills": True,
                 "agents": [{**_FULL_AGENT, "skills": [_SKILLS[0]]}]}
        import_flow(reply)
        r = client.post(f"/api/admin/devices/{DEVICE}/agent-import",
                        json={"agent_name": "测试助手"}, headers={"X-Admin-Token": token})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["imported"] is True
        cfg = data["config"]["config"]
        # pkg.skills：仅 enabled 完整实体，InstalledSkillSchema 字段（无 content）
        assert [s["id"] for s in cfg["skills"]] == ["s1"]
        ent = cfg["skills"][0]
        assert ent["contentHash"] == "h1" and ent["namespace"] == "ns1" \
            and ent["sourceTags"] == ["tag"] and ent["folderName"] == "f1"
        assert "content" not in ent, "Fork InstalledSkillSchema 不返回 content"
        # agent.skills 引用与 pkg.skills 一致（同源自 pick）
        assert cfg["agent"]["skills"] == ["s1"]
        from agent_repo import validate_pkg
        assert validate_pkg(cfg, require_id=True) == [], "包结构校验必须通过"
        # 完整字段入包
        assert cfg["agent"]["mcps"] == [{"name": "mcp1", "type": "stdio", "command": "npx"}]
        assert cfg["agent"]["knowledgeBaseIds"] == ["kb-1", "kb-2"]
        assert cfg["agent"]["disabledTools"] == ["tool_x"]
        assert cfg["agent"]["modelName"] == "deepseek-v4-flash"
        assert cfg["agent"]["configuration"] == {"permission_mode": "default",
                                                 "max_turns": 50, "env_vars": {"K": "V"}}
        assert cfg["agent"]["id"] == "agent-uuid-1"
        # 包详情可见 skills/mcps/knowledgeBaseIds
        got = client.get(f"/api/admin/agent-configs/测试助手",
                         headers={"X-Admin-Token": token}).json()["data"]
        assert got["config"]["skills"][0]["id"] == "s1"
        assert got["config"]["agent"]["mcps"][0]["name"] == "mcp1"
        assert got["config"]["agent"]["knowledgeBaseIds"] == ["kb-1", "kb-2"]

    def test_import_empty_skills_valid(self, token, import_flow):
        """skills 空数组 = 合法：可正常导入（不报错）。"""
        agent_no_skills = {**_FULL_AGENT, "skills": []}
        import_flow({"success": True, "with_skills": True,
                            "agents": [agent_no_skills]})
        r = client.post(f"/api/admin/devices/{DEVICE}/agent-import",
                        json={"agent_name": "测试助手"}, headers={"X-Admin-Token": token})
        assert r.status_code == 200, r.text
        cfg = r.json()["data"]["config"]["config"]
        assert cfg["skills"] == [] and cfg["agent"]["skills"] == []

    def test_import_skill_fetch_degraded_empty_ok(self, token, import_flow):
        """单 agent skills 拉取失败降级空列表 → 空数组合法导入。"""
        import_flow({"success": True, "with_skills": True,
                            "agents": [{**_FULL_AGENT, "skills": []}]})
        r = client.post(f"/api/admin/devices/{DEVICE}/agent-import",
                        json={"agent_name": "测试助手"}, headers={"X-Admin-Token": token})
        assert r.status_code == 200, r.text

    def test_legacy_sidecar_missing_skills_rejected_no_persist(self, token, import_flow):
        """存量 sidecar：技能回执缺 skills 键 → 519 ERR_SKILLS_MISSING + 文案，拒绝落库。"""
        legacy = {k: v for k, v in _FULL_AGENT.items()}  # 无 skills 键（存量行为）
        reply = {"success": True, "with_skills": False, "agents": [legacy]}
        import_flow(reply)
        r = client.post(f"/api/admin/devices/{DEVICE}/agent-import",
                        json={"agent_name": "测试助手"}, headers={"X-Admin-Token": token})
        assert r.status_code == 519, r.text
        detail = r.json()["detail"]
        assert "ERR_SKILLS_MISSING" in detail, detail
        assert "请升级员工端侧车" in detail, detail
        # 拒绝落库
        items = client.get("/api/admin/agent-configs", headers={"X-Admin-Token": token}).json()["data"]
        assert items == []

    def test_import_unknown_agent_404(self, token, import_flow):
        import_flow({"success": True, "with_skills": True, "agents": []})
        r = client.post(f"/api/admin/devices/{DEVICE}/agent-import",
                        json={"agent_name": "不存在"}, headers={"X-Admin-Token": token})
        assert r.status_code == 404