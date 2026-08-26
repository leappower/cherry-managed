"""JJC-20260826-001：从设备导入补全 skills/MCP —— sidecar 侧单测。

覆盖：
  - CherryClient.list_skills(agent_id=...) URL 构造（agentId camelCase 查询参数；
    无 agent_id 时不带 query，向后兼容）
  - _handle_agent_list with_skills=true：按 isEnabled（camelCase）过滤，
    仅 enabled 的完整 skill 实体装入 a["skills"]
  - 单 agent 拉取 skills 失败 → 降级空列表 + 不阻塞整体
  - 不带 with_skills 时行为与现状一致（不发 skills 请求，不带 skills 键）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from sidecar.sidecar import SidecarRunner  # noqa: E402
from cherry_client import CherryClient  # noqa: E402


class FakeCherry:
    """记录 list_skills 调用的 CherryClient 替身。"""

    def __init__(self, agents: list, skill_map: dict):
        self.agents = agents
        self.skill_map = skill_map  # agent_id -> skill 实体列表
        self.list_skills_calls: list = []

    def list_agents(self) -> list:
        return self.agents

    def list_skills(self, agent_id=None) -> list:
        self.list_skills_calls.append(agent_id)
        if agent_id is None:
            return []
        return self.skill_map.get(agent_id, [])


class FlakyCherry(FakeCherry):
    """特定 agent 拉取 skills 抛异常的替身（验证降级不阻塞）。"""

    def list_skills(self, agent_id=None):
        self.list_skills_calls.append(agent_id)
        if agent_id == "a1":
            raise RuntimeError("skill api boom")
        return []


class FakeRunner:
    """最小 SidecarRunner 替身：仅含 _handle_agent_list 用到成员。"""

    def __init__(self, cherry):
        self.cherry = cherry
        self.sent: list = []

    def _send(self, data: dict) -> None:
        self.sent.append(data)


# ---- CherryClient.list_skills URL 构造 ----
class TestListSkillsUrl:
    def test_with_agent_id_builds_agentId_query(self):
        c = CherryClient(host="127.0.0.1", port=23333, api_key="k")
        captured = {}

        def fake_request(method, path):
            captured["method"] = method
            captured["path"] = path
            return {"data": []}

        c._request = fake_request
        c.list_skills(agent_id="ag-1")
        assert captured["path"] == "/v1/skills?agentId=ag-1", captured["path"]
        # 受管前缀映射发生在 _request 内（_path），最终 URL 应为 /v1/admin/skills?agentId=...
        assert c._path(captured["path"]) == "/v1/admin/skills?agentId=ag-1"

    def test_no_agent_id_no_query(self):
        c = CherryClient(host="127.0.0.1", port=23333, api_key="k")
        captured = {}

        def fake_request(method, path):
            captured["method"] = method
            captured["path"] = path
            return {"data": []}

        c._request = fake_request
        c.list_skills()
        assert captured["path"] == "/v1/skills", captured["path"]
        assert c._path(captured["path"]) == "/v1/admin/skills"

    def test_agent_id_urlencoded(self):
        c = CherryClient(host="127.0.0.1", port=23333, api_key="k")
        captured = {}

        def fake_request(method, path):
            captured["path"] = path
            return {"data": []}

        c._request = fake_request
        c.list_skills(agent_id="带 空格/id")
        final = c._path(captured["path"])
        assert final == "/v1/admin/skills?agentId=%E5%B8%A6+%E7%A9%BA%E6%A0%BC%2Fid", final


# ---- _handle_agent_list with_skills 过滤 ----
class TestHandleAgentListSkills:
    def test_with_skills_filters_enabled_only(self):
        agents = [{"id": "a1", "name": "甲"}, {"id": "a2", "name": "乙"}]
        skills_a1 = [
            {"id": "s1", "name": "启用", "isEnabled": True},
            {"id": "s2", "name": "禁用", "isEnabled": False},
            {"id": "s3", "name": "默认启用"},  # 无 isEnabled → 视为启用
        ]
        cherry = FakeCherry(agents, {"a1": skills_a1, "a2": []})
        runner = FakeRunner(cherry)
        SidecarRunner._handle_agent_list(
            runner, {"type": "agent_list", "request_id": "r1", "with_skills": True})
        assert len(runner.sent) == 1
        reply = runner.sent[0]
        assert reply["success"] is True
        assert reply["with_skills"] is True
        assert [s["id"] for s in reply["agents"][0]["skills"]] == ["s1", "s3"]
        assert reply["agents"][1]["skills"] == []
        # 每个 agent 都按 id 拉了一次 skills
        assert cherry.list_skills_calls == ["a1", "a2"]

    def test_skill_failure_degrades_empty_not_blocking(self):
        agents = [{"id": "a1", "name": "甲"}, {"id": "a2", "name": "乙"}]
        cherry = FlakyCherry(agents, {})
        runner = FakeRunner(cherry)
        SidecarRunner._handle_agent_list(
            runner, {"type": "agent_list", "request_id": "r2", "with_skills": True})
        reply = runner.sent[0]
        assert reply["success"] is True, "单个 agent skills 拉取失败不得阻塞整体"
        assert reply["agents"][0]["skills"] == []
        assert reply["agents"][1]["skills"] == []

    def test_backward_compat_no_with_skills(self):
        agents = [{"id": "a1", "name": "甲", "type": "claude-code"}]
        cherry = FakeCherry(agents, {})
        runner = FakeRunner(cherry)
        SidecarRunner._handle_agent_list(runner, {"type": "agent_list", "request_id": "r3"})
        reply = runner.sent[0]
        assert reply["success"] is True
        assert reply["with_skills"] is False
        assert "skills" not in reply["agents"][0], "不带 with_skills 时不得注入 skills 键"
        assert cherry.list_skills_calls == [], "不带 with_skills 时不得调用 list_skills"

    def test_agent_list_failure_reports_error(self):
        class BoomCherry(FakeCherry):
            def list_agents(self):
                raise RuntimeError("agents api boom")

        runner = FakeRunner(BoomCherry([], {}))
        SidecarRunner._handle_agent_list(runner, {"type": "agent_list", "request_id": "r4",
                                                  "with_skills": True})
        reply = runner.sent[0]
        assert reply["success"] is False
        assert "boom" in reply["error"]